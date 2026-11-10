from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DiscoveryCursor, DiscoveryItem, DiscoveryRun, Paper
from ..schemas import PaperCreate
from .arxiv_rss import fetch_rss_records
from .common import get_daily_time, get_setting, log_operation, normalize_title
from .discovery import _identity_keys
from .ingest import (
    _arxiv_get,
    arxiv_request_budget,
    create_or_update_paper,
    find_existing_paper,
    search_arxiv_page,
)
from .interest_ranker import score_paper, train_daily_ranker
from .manual_recommendations import (
    is_daily_recommendation_eligible,
    previously_recommended_paper_ids,
)
from .research_day import current_research_date

SYNC_CURSOR_ID = "arxiv-paper-version-time-v2"
CURSOR_BASIS = "paper-version-time"
OAI_ENDPOINT = "https://oaipmh.arxiv.org/oai"
DEFAULT_SYNC_CATEGORIES = ["cs.RO", "cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.MA"]
DEFAULT_BOOTSTRAP_DAYS = 3
ATOM_PAGE_SIZE = 100
ATOM_BUDGET_SECONDS = 180.0
_SYNC_LOCK = threading.Lock()
_logger = logging.getLogger(__name__)


class InvalidBriefDate(ValueError):
    pass


CORE_EMBODIED = re.compile(
    r"\b(?:embodied|robot\w*|manipulat\w*|grasp\w*|locomotion|navigation|tactile|"
    r"haptic\w*|visuomotor|humanoids?|teleoperat\w*|sim[- ]to[- ]real|"
    r"vision[- ]language[- ]action|vla|world[- ]action|wam|"
    r"robot learning|embodied evaluation|embodied benchmark)\b",
    re.IGNORECASE,
)
THREE_D_VISION = re.compile(
    r"\b(?:3[- ]?d|three[- ]dimensional|point clouds?|rgb[- ]?d|stereo|"
    r"depth estimation|scene reconstruction|radiance fields?|gaussian splatt\w*|"
    r"nerf|lidar|slam|visual odometry|pose estimation|multi[- ]view|"
    r"structure from motion)\b",
    re.IGNORECASE,
)
EMBODIED_CONTEXT = re.compile(
    r"\b(?:robot\w*|embodied|manipulat\w*|agentic|action|control|policy|"
    r"navigation|locomotion|simulation|sensor|perception)\b",
    re.IGNORECASE,
)


def _aware_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _parse_source_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value.strip()))
    except ValueError:
        return None


def _domain_scope(payload: PaperCreate) -> tuple[bool, list[str], str]:
    categories = payload.metadata_json.get("categories")
    category_values = categories if isinstance(categories, list) else []
    text = f"{payload.title} {payload.abstract or ''}"
    core = sorted({match.group(0).casefold() for match in CORE_EMBODIED.finditer(text)})
    if "cs.RO" in category_values:
        return True, ["embodied intelligence"], "cs.RO recall"
    if core:
        return True, ["embodied intelligence"], "core embodied concept: " + ", ".join(core[:5])
    visual = sorted({match.group(0).casefold() for match in THREE_D_VISION.finditer(text)})
    context = sorted({match.group(0).casefold() for match in EMBODIED_CONTEXT.finditer(text)})
    if visual and context:
        return (
            True,
            ["3D vision", "embodied intelligence"],
            "3D concept with embodied context: " + ", ".join([*visual[:3], *context[:3]]),
        )
    return False, [], "no embodied or embodied-perception signal"


def _xml_text(node: Any, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    value = " ".join(found.text.split())
    return value or None


def _oai_payload(record: Any) -> tuple[PaperCreate, list[str]] | None:
    oai = "{http://www.openarchives.org/OAI/2.0/}"
    arxiv = "{http://arxiv.org/OAI/arXiv/}"
    header = record.find(f"{oai}header")
    if header is not None and header.get("status") == "deleted":
        return None
    metadata = record.find(f"{oai}metadata/{arxiv}arXiv")
    if metadata is None:
        return None
    arxiv_id = _xml_text(metadata, f"{arxiv}id")
    title = _xml_text(metadata, f"{arxiv}title")
    if not arxiv_id or not title:
        return None
    categories = [
        value for value in (_xml_text(metadata, f"{arxiv}categories") or "").split() if value
    ]
    authors = []
    for author in metadata.findall(f"{arxiv}authors/{arxiv}author"):
        keyname = _xml_text(author, f"{arxiv}keyname") or ""
        forenames = _xml_text(author, f"{arxiv}forenames") or ""
        name = " ".join(part for part in (forenames, keyname) if part).strip()
        if name:
            authors.append({"name": name})
    created = _xml_text(metadata, f"{arxiv}created")
    updated = _xml_text(metadata, f"{arxiv}updated")
    published_at = None
    if created:
        try:
            published_at = datetime.fromisoformat(created).date()
        except ValueError:
            pass
    return (
        PaperCreate(
            title=title,
            abstract=_xml_text(metadata, f"{arxiv}abstract"),
            authors=authors,
            first_author=authors[0]["name"] if authors else None,
            source="arxiv",
            source_url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            arxiv_id=arxiv_id,
            arxiv_version=1,
            doi=_xml_text(metadata, f"{arxiv}doi"),
            published_at=published_at,
            metadata_json={
                "categories": categories,
                "primary_category": categories[0] if categories else None,
                "comment": _xml_text(metadata, f"{arxiv}comments"),
                "journal_ref": _xml_text(metadata, f"{arxiv}journal-ref"),
                "arxiv_updated_at": updated,
                "arxiv_published_at": created,
                "metadata_source": "oai-pmh",
                "arxiv_datestamp": _xml_text(header, f"{oai}datestamp")
                if header is not None
                else None,
            },
        ),
        categories,
    )


def _fetch_oai_records(
    *,
    start: datetime,
    end: datetime,
    categories: list[str],
) -> tuple[list[PaperCreate], dict[str, Any]]:
    """Fetch every OAI-PMH resumption page and keep configured categories."""
    params: dict[str, str] = {
        "verb": "ListRecords",
        "metadataPrefix": "arXiv",
        "from": start.date().isoformat(),
        "until": end.date().isoformat(),
    }
    # The default categories all belong to cs. Avoid harvesting every discipline.
    groups = {category.split(".")[0] for category in categories}
    if len(groups) == 1 and groups <= {"cs", "math", "stat", "eess", "econ", "q-bio", "q-fin"}:
        params["set"] = next(iter(groups))
    records: list[PaperCreate] = []
    source_count = category_match_count = page_count = 0
    seen_tokens: set[str] = set()
    configured = set(categories)
    while True:
        response = _arxiv_get(
            params,
            url=OAI_ENDPOINT,
            request_timeout=90.0,
            max_attempts=2,
            retry_backoff=6.0,
        )
        page_count += 1
        root = etree.fromstring(response.content)
        oai = "{http://www.openarchives.org/OAI/2.0/}"
        if root.tag != f"{oai}OAI-PMH":
            raise ValueError("arXiv returned a malformed OAI-PMH response")
        error = root.find(f".//{oai}error")
        if error is not None:
            if error.get("code") == "noRecordsMatch":
                break
            raise RuntimeError(f"arXiv OAI-PMH error: {error.text or error.get('code')}")
        listing = root.find(f"{oai}ListRecords")
        if listing is None:
            raise ValueError("arXiv omitted OAI-PMH ListRecords")
        page_records = listing.findall(f"{oai}record")
        if not page_records:
            raise ValueError("arXiv returned an empty OAI-PMH page without noRecordsMatch")
        for record in page_records:
            source_count += 1
            parsed = _oai_payload(record)
            if parsed is None:
                header = record.find(f"{oai}header")
                if header is None or header.get("status") != "deleted":
                    raise ValueError("arXiv returned an incomplete OAI-PMH record")
                continue
            payload, record_categories = parsed
            if not configured.intersection(record_categories):
                continue
            category_match_count += 1
            records.append(payload)
        token = root.find(f".//{oai}resumptionToken")
        token_value = (token.text or "").strip() if token is not None else ""
        if not token_value:
            break
        if token_value in seen_tokens:
            raise RuntimeError("arXiv OAI-PMH repeated a resumption token")
        seen_tokens.add(token_value)
        params = {"verb": "ListRecords", "resumptionToken": token_value}
    return records, {
        "source": "oai-pmh",
        "pages": page_count,
        "source_records": source_count,
        "category_matched": category_match_count,
        "complete": True,
        "coverage_kind": "metadata-datestamp",
    }


def _fetch_atom_records(
    *,
    start: datetime,
    end: datetime,
    categories: list[str],
) -> tuple[list[PaperCreate], dict[str, Any]]:
    """Walk article updates newest first until the persisted time window is covered.

    Atom supports updated-date sorting, but only submission-date range filtering.
    Do not filter by submittedDate: that would omit new versions of older papers.
    """
    query = "(" + " OR ".join(f"cat:{category}" for category in categories) + ")"
    records: list[PaperCreate] = []
    seen_ids: set[str] = set()
    offset = pages = 0
    total: int | None = None
    previous_stamp: datetime | None = None
    while True:
        if offset >= 30000:
            raise ValueError("Atom pagination limit reached before covering the cursor window")
        page = search_arxiv_page(
            query,
            start=offset,
            max_results=ATOM_PAGE_SIZE,
            sort_by="lastUpdatedDate",
            sort_order="descending",
            request_timeout=15.0,
            max_attempts=2,
            retry_backoff=3.0,
        )
        reported_total = page["total_results"]
        if not isinstance(reported_total, int) or reported_total < 0:
            raise ValueError("Atom omitted a valid totalResults; coverage cannot be verified")
        if total is not None and reported_total != total:
            raise ValueError("Atom result set changed during pagination; retry the cursor window")
        total = reported_total
        pages += 1
        items = page["items"]
        if page["start"] != offset or (not items and offset < total):
            raise ValueError("Atom returned an incomplete page before covering the cursor window")
        if offset + len(items) > total:
            raise ValueError("Atom returned more records than totalResults")
        reached_start = False
        for payload in items:
            stamp = _parse_source_time(payload.metadata_json.get("arxiv_updated_at"))
            if stamp is None or not payload.arxiv_id:
                raise ValueError("Atom record omitted its article ID or updated timestamp")
            if payload.arxiv_id in seen_ids:
                raise ValueError("Atom repeated an article across pages; coverage is incomplete")
            if previous_stamp is not None and stamp > previous_stamp:
                raise ValueError("Atom records are not sorted by updated date")
            seen_ids.add(payload.arxiv_id)
            previous_stamp = stamp
            if stamp < start:
                reached_start = True
            elif stamp <= end:
                payload.metadata_json = {**payload.metadata_json, "metadata_source": "atom"}
                records.append(payload)
        offset += len(items)
        if reached_start or offset == total:
            return records, {
                "source": "atom",
                "query": query,
                "pages": pages,
                "source_records": offset,
                "total_results": total,
                "category_matched": len(records),
                "complete": True,
                "coverage_kind": "article-updates",
            }


def _fetch_arxiv_records(
    *,
    start: datetime,
    end: datetime,
    categories: list[str],
    pending: list[PaperCreate] | None = None,
) -> tuple[list[PaperCreate], dict[str, Any]]:
    """RSS discovers announcements; API scanning catches up to the source-time cursor."""
    with arxiv_request_budget():
        rss_items: list[PaperCreate] = []
        rss_stats: dict[str, Any] = {}
        rss_error = None
        try:
            with arxiv_request_budget(seconds=180):
                rss_items, rss_stats = fetch_rss_records(
                    end=end, categories=categories, pending=pending
                )
        except (httpx.HTTPError, ValueError, etree.XMLSyntaxError) as exc:
            rss_error = str(exc)
            _logger.warning("RSS discovery unavailable: %s", exc)
        if rss_stats.get("metadata_error"):
            return rss_items, {
                **rss_stats,
                "coverage_complete": False,
                "cursor_can_advance": False,
                "fallback_used": False,
                "atom_error": "Metadata API unavailable; historical catch-up deferred",
            }
        try:
            with arxiv_request_budget(seconds=ATOM_BUDGET_SECONDS):
                items, stats = _fetch_atom_records(start=start, end=end, categories=categories)
            if not stats.get("complete"):
                raise ValueError("Atom did not cover the cursor window")
            return [*rss_items, *items], {
                **stats,
                "source": "rss+atom" if rss_stats else "atom",
                "rss": rss_stats,
                "rss_error": rss_error,
                "fallback_used": not bool(rss_stats),
                "coverage_complete": bool(rss_stats) and rss_stats.get("cursor_can_advance", False),
                "cursor_can_advance": bool(rss_stats)
                and rss_stats.get("cursor_can_advance", False),
            }
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as atom_error:
            if rss_stats:
                return rss_items, {
                    **rss_stats,
                    "atom_error": str(atom_error),
                    "fallback_used": False,
                    "coverage_complete": False,
                    "cursor_can_advance": False,
                }
            _logger.warning("Atom discovery failed; falling back to OAI-PMH: %s", atom_error)
            try:
                items, stats = _fetch_oai_records(start=start, end=end, categories=categories)
                if not stats.get("complete"):
                    raise ValueError("OAI-PMH did not complete pagination")
            except Exception as oai_error:
                raise RuntimeError(
                    f"arXiv discovery failed: RSS: {rss_error}; Atom: {atom_error}; OAI-PMH: {oai_error}"
                ) from oai_error
            return items, {
                **stats,
                "fallback_used": True,
                "rss_error": rss_error,
                "atom_error": str(atom_error),
                "coverage_complete": False,
                "cursor_can_advance": False,
            }


def _cursor(session: Session) -> DiscoveryCursor:
    cursor = session.get(DiscoveryCursor, SYNC_CURSOR_ID)
    if cursor is None:
        # Rebuild from actual stored article dates, never the old run-time cutoff.
        latest = session.scalar(
            select(func.max(func.coalesce(DiscoveryItem.updated_at, DiscoveryItem.published_at)))
            .join(DiscoveryRun, DiscoveryRun.id == DiscoveryItem.run_id)
            .where(DiscoveryRun.status == "completed", DiscoveryItem.status != "outside_window")
        )
        cursor = DiscoveryCursor(id=SYNC_CURSOR_ID, committed_through=latest)
        session.add(cursor)
        legacy = session.get(DiscoveryCursor, "arxiv-last-updated-v1")
        if legacy is not None:
            session.delete(legacy)
        session.flush()
    return cursor


def _batch_candidate_routing(
    session: Session,
    *,
    paper_ids: list[str],
    relevance_threshold: float,
) -> tuple[list[str], list[str], list[str]]:
    papers = list(
        session.scalars(
            select(Paper)
            .where(
                Paper.id.in_(paper_ids),
                Paper.model_score.is_not(None),
            )
            .order_by(Paper.model_score.desc(), Paper.published_at.desc())
        ).all()
    )
    eligible_ids: list[str] = []
    review_ids: list[str] = []
    filtered_ids: list[str] = []
    prior_ids = previously_recommended_paper_ids(session, paper_ids)
    for paper in papers:
        if not is_daily_recommendation_eligible(paper) or paper.id in prior_ids:
            continue
        if paper.model_review_required:
            eligible_ids.append(paper.id)
            review_ids.append(paper.id)
        elif (
            paper.agent_score if paper.agent_score is not None else paper.model_score
        ) >= relevance_threshold:
            eligible_ids.append(paper.id)
        else:
            filtered_ids.append(paper.id)
    return eligible_ids, review_ids, filtered_ids


def _in_source_window(payload: PaperCreate, start: datetime, end: datetime) -> bool:
    """Late RSS announcements bypass the API's optimization boundary."""
    metadata = payload.metadata_json
    announced = _parse_source_time(metadata.get("arxiv_announced_at"))
    stamp = metadata.get("arxiv_updated_at") or metadata.get("arxiv_published_at")
    parsed = _parse_source_time(stamp)
    if announced is not None:
        return announced <= end and (parsed is None or parsed <= end)
    if parsed is None:
        return False
    if len(str(stamp)) == 10:
        return parsed <= end and parsed + timedelta(days=1) > start
    return start <= parsed <= end


def sync_arxiv(
    session: Session,
    *,
    through_date: date | None = None,
    now: datetime | None = None,
    bootstrap_days: int = DEFAULT_BOOTSTRAP_DAYS,
) -> dict[str, Any]:
    # One cursor owner at a time; do not let overlapping sync requests regress it.
    with _SYNC_LOCK:
        return _sync_arxiv(
            session, through_date=through_date, now=now, bootstrap_days=bootstrap_days
        )


def _sync_arxiv(
    session: Session,
    *,
    through_date: date | None = None,
    now: datetime | None = None,
    bootstrap_days: int = DEFAULT_BOOTSTRAP_DAYS,
) -> dict[str, Any]:
    """Discover one batch ending at the requested brief's scheduled time."""
    now_utc = _aware_utc(now)
    brief_date = through_date or current_research_date(session, now=now_utc)
    hour, minute = get_daily_time(session)
    cutoff = datetime.combine(
        brief_date, time(hour, minute), tzinfo=ZoneInfo(settings.timezone)
    ).astimezone(UTC)
    if cutoff > now_utc:
        raise InvalidBriefDate("The brief's scheduled cutoff is in the future")
    cursor = _cursor(session)
    previous_committed = _aware_utc(cursor.committed_through) if cursor.committed_through else None
    if previous_committed is None:
        start = (cutoff - timedelta(days=max(1, bootstrap_days))).replace(second=0, microsecond=0)
    else:
        start = previous_committed
    if previous_committed is not None and previous_committed > cutoff:
        start = cutoff - timedelta(days=max(1, bootstrap_days))
    completed = session.scalar(
        select(DiscoveryRun)
        .where(
            DiscoveryRun.status == "completed",
            DiscoveryRun.window_end == cutoff,
            DiscoveryRun.result_json["cursor"]["basis"].as_string() == CURSOR_BASIS,
            DiscoveryRun.result_json["coverage_complete"].as_boolean() == True,
        )
        .order_by(DiscoveryRun.finished_at.desc())
        .limit(1)
    )
    if completed and (completed.result_json or {}).get("brief_date") == brief_date.isoformat():
        result = dict(completed.result_json)
        relevant, review, filtered = _batch_candidate_routing(
            session,
            paper_ids=result.get("batch_paper_ids", []),
            relevance_threshold=result["model"]["relevance_threshold"],
        )
        return {
            **result,
            "status": "up_to_date",
            "reused_run": True,
            "paper_ids": relevant,
            "agent_review_paper_ids": review,
            "model_filtered_paper_ids": filtered,
            "counts": {
                **result["counts"],
                "candidate_pool": len(relevant),
                "confident_relevant": len(relevant) - len(review),
                "agent_review": len(review),
                "model_filtered": len(filtered),
            },
        }
    # Recover only staging from interrupted attempts at this exact daily interval.
    retry_ids = list(
        session.scalars(
            select(DiscoveryItem.paper_id)
            .join(DiscoveryRun, DiscoveryRun.id == DiscoveryItem.run_id)
            .where(
                DiscoveryRun.window_start == start,
                DiscoveryRun.window_end == cutoff,
                DiscoveryItem.paper_id.is_not(None),
                DiscoveryItem.status.in_(["review", "promoted", "filtered"]),
            )
        )
    )
    categories = get_setting(session, "discovery_arxiv_categories", DEFAULT_SYNC_CATEGORIES)
    if not isinstance(categories, list) or not categories:
        categories = DEFAULT_SYNC_CATEGORIES
    categories = [
        category
        for category in categories
        if isinstance(category, str) and category not in {"cs.SE", "cs.HC"}
    ] or DEFAULT_SYNC_CATEGORIES
    fetch_start = start
    query = (
        f"RSS announcement batches + Atom article-version cursor; "
        f"updates ({fetch_start.isoformat()}, {cutoff.isoformat()}], "
        f"categories={','.join(categories)}"
    )
    run = DiscoveryRun(
        status="running",
        window_start=start,
        window_end=cutoff,
        query=query,
    )
    session.add(run)
    cursor.last_attempt_started_at = now_utc
    session.commit()
    run_id = run.id

    try:
        ranker, model_run, model_metrics = train_daily_ranker(session, now=now_utc)
        run.model_version = model_run.version
        raw_items, source_stats = _fetch_arxiv_records(
            start=fetch_start,
            end=cutoff,
            categories=categories,
            pending=[
                PaperCreate(**{name: getattr(paper, name) for name in PaperCreate.model_fields})
                for paper in session.scalars(
                    select(Paper).where(
                        Paper.metadata_json["arxiv_timestamp_pending"].as_boolean() == True
                    )
                )
            ],
        )
        if not source_stats.get("complete"):
            raise ValueError("arXiv source coverage is incomplete; cursor was not advanced")
        source_stats = {
            **source_stats,
            "window_start": fetch_start.isoformat(),
            "window_end": cutoff.isoformat(),
            "cursor_basis": CURSOR_BASIS,
        }
        run.total_results = len(raw_items)
        run.fetched_count = len(raw_items)

        # Prefer the highest version per ID and retain RSS provenance when API
        # scanning returns the same version, so a late announcement isn't filtered.
        merged: dict[str, PaperCreate] = {}
        source_duplicates = 0
        for item in raw_items:
            key = item.arxiv_id or item.title
            earlier = merged.get(key)
            if earlier is None:
                merged[key] = item
                continue
            source_duplicates += 1
            if (item.arxiv_version or 0) > (earlier.arxiv_version or 0):
                merged[key] = item
            elif item.arxiv_version == earlier.arxiv_version:
                metadata = {**earlier.metadata_json, **item.metadata_json}
                if _parse_source_time(metadata.get("arxiv_updated_at")) is not None:
                    metadata["arxiv_timestamp_pending"] = False
                item.metadata_json = metadata
                merged[key] = item
        seen_keys: set[str] = set()
        paper_ids: list[str] = []
        agent_review_paper_ids: list[str] = []
        filtered_paper_ids: list[str] = []
        promoted_this_run = 0
        domain_rejected = skipped_existing = outside_window = updated_existing = 0
        duplicate_count = source_duplicates
        source_stamps: list[datetime] = []
        batch_ids = list(retry_ids)
        for payload in merged.values():
            identity_keys = _identity_keys(payload.arxiv_id, payload.doi, payload.title)
            if identity_keys & seen_keys:
                duplicate_count += 1
                continue
            seen_keys.update(identity_keys)
            stage = DiscoveryItem(
                run_id=run.id,
                arxiv_id=payload.arxiv_id,
                arxiv_version=payload.arxiv_version,
                title=payload.title,
                abstract=payload.abstract,
                authors=payload.authors,
                categories=list(payload.metadata_json.get("categories") or []),
                published_at=_parse_source_time(payload.metadata_json.get("arxiv_published_at")),
                updated_at=_parse_source_time(payload.metadata_json.get("arxiv_updated_at")),
                status="fetched",
                source_json=payload.model_dump(mode="json"),
            )
            session.add(stage)
            if not _in_source_window(payload, fetch_start, cutoff):
                outside_window += 1
                stage.status = "outside_window"
                stage.reason = "source update is outside this discovery source window"
                continue
            stamp = _parse_source_time(
                payload.metadata_json.get("arxiv_updated_at")
                or payload.metadata_json.get("arxiv_published_at")
            )
            if stamp is not None:
                source_stamps.append(stamp)
            existing = find_existing_paper(
                session, arxiv_id=payload.arxiv_id, doi=payload.doi, title=payload.title
            )
            old_version = (existing.arxiv_version or 0) if existing else 0
            # Never downgrade a newer library version using an older RSS batch.
            if existing and (payload.arxiv_version or 0) < old_version:
                skipped_existing += 1
                stage.status = "existing"
                stage.reason = "older than the stored article version"
                stage.paper_id = existing.id
                continue
            paper, created = create_or_update_paper(session, payload, actor="system", commit=False)
            stage.paper_id = paper.id
            if not created:
                skipped_existing += 1
                newer = (payload.arxiv_version or 0) > old_version
                if newer:
                    paper.title = payload.title
                    paper.normalized_title = normalize_title(payload.title)
                    paper.abstract = payload.abstract or paper.abstract
                    paper.source_url = payload.source_url or paper.source_url
                    paper.pdf_url = payload.pdf_url or paper.pdf_url
                    # The prior path belongs to the PaperVersion captured by the merge.
                    paper.pdf_path = None
                    if payload.metadata_json.get("arxiv_timestamp_pending"):
                        paper.metadata_json = {**paper.metadata_json, "arxiv_updated_at": None}
                    updated_existing += 1
                stage.status = "updated_existing" if newer else "existing"
                stage.reason = (
                    "source metadata merged; existing feedback and recommendation state preserved"
                )
                continue
            in_scope, _domains, domain_reason = _domain_scope(payload)
            if not in_scope:
                domain_rejected += 1
                stage.status = "domain_rejected"
                stage.reason = domain_reason
                continue
            batch_ids.append(paper.id)
            promoted_this_run += 1
            ranked = score_paper(ranker, paper)
            if ranked.review_required:
                stage.status = "review"
                agent_review_paper_ids.append(paper.id)
                paper_ids.append(paper.id)
            elif ranked.score >= ranker.relevance_threshold:
                stage.status = "promoted"
                paper_ids.append(paper.id)
            else:
                stage.status = "filtered"
                filtered_paper_ids.append(paper.id)
            stage.paper_id = paper.id
            stage.model_score = ranked.score
            stage.model_confidence = ranked.confidence
            stage.review_required = ranked.review_required
            stage.reason = (
                f"model score={ranked.score:.2f}, confidence={ranked.confidence:.2f}, "
                f"support={ranked.support:.2f}"
            )

        routed_ids, routed_review_ids, routed_filtered_ids = _batch_candidate_routing(
            session,
            paper_ids=batch_ids,
            relevance_threshold=ranker.relevance_threshold,
        )
        paper_ids = routed_ids
        agent_review_paper_ids = routed_review_ids
        filtered_paper_ids = routed_filtered_ids

        if source_stats.get("cursor_can_advance", True) and source_stamps:
            cursor.committed_through = max(
                source_stamps + ([previous_committed] if previous_committed else [])
            )
        cursor.last_success_at = now_utc
        cursor.last_error = None
        run.status = "completed"
        run.finished_at = now_utc
        run.skipped_existing_count = skipped_existing
        run.duplicate_count = duplicate_count
        run.domain_rejected_count = domain_rejected
        run.promoted_count = promoted_this_run
        run.review_required_count = len(agent_review_paper_ids)
        result = {
            "status": "completed",
            "brief_date": brief_date.isoformat(),
            "run_id": run.id,
            "cursor": {
                "basis": CURSOR_BASIS,
                "previous": previous_committed.isoformat() if previous_committed else None,
                "committed_through": (
                    _aware_utc(cursor.committed_through).isoformat()
                    if cursor.committed_through
                    else None
                ),
            },
            "window": {"start": start.isoformat(), "end": cutoff.isoformat()},
            "query": query,
            "source": source_stats,
            "model": {
                "version": model_run.version,
                "sample_count": model_run.sample_count,
                "validation_mae": model_run.validation_mae,
                "residual_scale": model_run.residual_scale,
                "relevance_threshold": ranker.relevance_threshold,
                "confidence_threshold": ranker.confidence_threshold,
                "metrics": model_metrics,
            },
            "counts": {
                "total_results": run.total_results,
                "fetched": run.fetched_count,
                "deduplicated": duplicate_count,
                "existing": skipped_existing,
                "updated_existing": updated_existing,
                "domain_rejected": domain_rejected,
                "outside_window": outside_window,
                "promoted_this_run": promoted_this_run,
                "candidate_pool": len(paper_ids),
                "confident_relevant": len(paper_ids) - len(agent_review_paper_ids),
                "agent_review": len(agent_review_paper_ids),
                "model_filtered": len(filtered_paper_ids),
            },
            "paper_ids": paper_ids,
            "batch_paper_ids": list(dict.fromkeys(batch_ids)),
            "agent_review_paper_ids": agent_review_paper_ids,
            "model_filtered_paper_ids": filtered_paper_ids,
            "coverage_complete": source_stats.get("coverage_complete", True),
        }
        run.result_json = result
        log_operation(
            session,
            actor="system",
            action="arxiv.cursor_sync",
            detail={key: value for key, value in result.items() if key != "paper_ids"},
        )
        session.commit()
        return result
    except Exception as exc:
        # Discard an uncommitted cursor advance, including failures during the final commit.
        session.rollback()
        run = session.get(DiscoveryRun, run_id)
        cursor = _cursor(session)
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error = str(exc)
        cursor.last_error = str(exc)
        session.commit()
        raise
