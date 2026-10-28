from __future__ import annotations

import re
import threading
from datetime import UTC, date, datetime, time, timedelta
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DiscoveryCursor, DiscoveryItem, DiscoveryRun, Paper
from ..schemas import PaperCreate
from .common import get_daily_time, get_setting, log_operation
from .discovery import _identity_keys
from .ingest import create_or_update_paper
from .interest_ranker import score_paper, train_daily_ranker
from .manual_recommendations import (
    is_daily_recommendation_eligible,
    previously_recommended_paper_ids,
)
from .outbound_network import external_httpx_kwargs
from .paper_metadata import queue_metadata_enrichment
from .research_day import current_research_date

SYNC_CURSOR_ID = "arxiv-last-updated-v1"
OAI_ENDPOINT = "https://oaipmh.arxiv.org/oai"
DEFAULT_SYNC_CATEGORIES = ["cs.RO", "cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.MA"]
DEFAULT_BOOTSTRAP_DAYS = 3
_SYNC_LOCK = threading.Lock()


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
    records: list[PaperCreate] = []
    source_count = category_match_count = page_count = 0
    seen_tokens: set[str] = set()
    configured = set(categories)
    while True:
        response = httpx.get(
            OAI_ENDPOINT,
            params=params,
            headers={"User-Agent": "FrontierResearch/0.1 (personal research assistant)"},
            timeout=90,
            follow_redirects=True,
            **external_httpx_kwargs(OAI_ENDPOINT),
        )
        response.raise_for_status()
        page_count += 1
        root = etree.fromstring(response.content)
        oai = "{http://www.openarchives.org/OAI/2.0/}"
        error = root.find(f".//{oai}error")
        if error is not None:
            if error.get("code") == "noRecordsMatch":
                break
            raise RuntimeError(f"arXiv OAI-PMH error: {error.text or error.get('code')}")
        for record in root.findall(f".//{oai}record"):
            source_count += 1
            parsed = _oai_payload(record)
            if parsed is None:
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
        sleep(2)
    return records, {
        "source": "oai-pmh",
        "pages": page_count,
        "source_records": source_count,
        "category_matched": category_match_count,
        "complete": True,
    }


def _cursor(session: Session) -> DiscoveryCursor:
    cursor = session.get(DiscoveryCursor, SYNC_CURSOR_ID)
    if cursor is None:
        cursor = DiscoveryCursor(id=SYNC_CURSOR_ID)
        session.add(cursor)
        session.flush()
    return cursor


def _library_keys(session: Session) -> set[str]:
    keys: set[str] = set()
    for arxiv_id, doi, title in session.execute(select(Paper.arxiv_id, Paper.doi, Paper.title)):
        keys.update(_identity_keys(arxiv_id, doi, title))
    return keys


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
                Paper.model_relevance_score.is_not(None),
            )
            .order_by(Paper.model_relevance_score.desc(), Paper.published_at.desc())
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
            paper.relevance_score
            if paper.relevance_score is not None
            else paper.model_relevance_score
        ) >= relevance_threshold:
            eligible_ids.append(paper.id)
        else:
            filtered_ids.append(paper.id)
    return eligible_ids, review_ids, filtered_ids


def _in_source_window(payload: PaperCreate, start: datetime, end: datetime) -> bool:
    """OAI date-only stamps overlap whole UTC days; exact stamps use (start, end]."""
    metadata = payload.metadata_json
    stamp = (
        metadata.get("arxiv_datestamp")
        or metadata.get("arxiv_updated_at")
        or metadata.get("arxiv_published_at")
    )
    parsed = _parse_source_time(stamp)
    if parsed is None:
        return False
    if len(str(stamp)) == 10:
        return parsed <= end and parsed + timedelta(days=1) > start
    return start < parsed <= end


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
    if previous_committed is not None and previous_committed >= cutoff:
        completed = session.scalar(
            select(DiscoveryRun)
            .where(DiscoveryRun.status == "completed", DiscoveryRun.window_end == cutoff)
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
        return {
            "status": "up_to_date",
            "brief_date": brief_date.isoformat(),
            "window": {"start": cutoff.isoformat(), "end": cutoff.isoformat()},
            "cursor": {
                "previous": previous_committed.isoformat(),
                "committed_through": previous_committed.isoformat(),
            },
            "coverage_complete": True,
            "fetched": 0,
            "paper_ids": [],
            "agent_review_paper_ids": [],
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
    query = (
        f"OAI-PMH ListRecords datestamp [{start.date().isoformat()} TO "
        f"{cutoff.date().isoformat()}], categories={','.join(categories)}"
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
        raw_items, source_stats = _fetch_oai_records(
            start=start,
            end=cutoff,
            categories=categories,
        )
        run.total_results = len(raw_items)
        run.fetched_count = len(raw_items)

        library_keys = _library_keys(session)
        seen_keys: set[str] = set()
        paper_ids: list[str] = []
        agent_review_paper_ids: list[str] = []
        filtered_paper_ids: list[str] = []
        promoted_papers: list[Paper] = []
        promoted_this_run = 0
        domain_rejected = duplicate_count = skipped_existing = outside_window = 0
        batch_ids = list(retry_ids)
        for payload in raw_items:
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
            if not _in_source_window(payload, start, cutoff):
                outside_window += 1
                stage.status = "outside_window"
                stage.reason = "source update is outside this brief's cursor interval"
                continue
            if identity_keys & library_keys:
                skipped_existing += 1
                stage.status = "existing"
                stage.reason = "already present in Frontier library"
                continue
            in_scope, _domains, domain_reason = _domain_scope(payload)
            if not in_scope:
                domain_rejected += 1
                stage.status = "domain_rejected"
                stage.reason = domain_reason
                continue
            paper, _created = create_or_update_paper(session, payload, actor="system")
            library_keys.update(identity_keys)
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
            promoted_papers.append(paper)

        if promoted_papers:
            queue_metadata_enrichment(
                session, [paper.id for paper in promoted_papers], reason="arxiv-sync"
            )
            session.commit()

        routed_ids, routed_review_ids, routed_filtered_ids = _batch_candidate_routing(
            session,
            paper_ids=batch_ids,
            relevance_threshold=ranker.relevance_threshold,
        )
        paper_ids = routed_ids
        agent_review_paper_ids = routed_review_ids
        filtered_paper_ids = routed_filtered_ids

        cursor.committed_through = cutoff
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
                "previous": previous_committed.isoformat() if previous_committed else None,
                "committed_through": cursor.committed_through.isoformat(),
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
            "coverage_complete": run.total_results == run.fetched_count,
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
