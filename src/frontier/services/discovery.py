from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper
from .common import (
    BROAD_DISCOVERY_DOMAINS,
    DISCOVERY_CATEGORIES,
    DISCOVERY_FALLBACK_LOOKBACK_DAYS,
    get_setting,
    log_operation,
    normalize_title,
    parse_arxiv_id,
)
from .ingest import search_arxiv_page

# Broad concepts, not fine-grained interests. Boundaries avoid matching "vision"
# inside "supervision" or "agent" inside "reagent".
DOMAIN_PATTERNS = {
    "3D vision": re.compile(
        r"\b(?:3[- ]?d|three[- ]dimensional|point clouds?|rgb[- ]?d|stereo|"
        r"depth estimation|scene reconstruction|neural render\w*|radiance fields?|"
        r"gaussian splatt\w*|nerf|lidar|slam|visual odometry|"
        r"pose estimation|multi[- ]view|structure from motion)\b",
        re.IGNORECASE,
    ),
    "embodied intelligence": re.compile(
        r"\b(?:robot\w*|manipulat\w*|grasp\w*|locomotion|navigation|tactile|embodied|"
        r"visuomotor|humanoids?|haptic\w*|sim[- ]to[- ]real|physical interaction)\b",
        re.IGNORECASE,
    ),
    "AI agents": re.compile(
        r"\b(?:agents?|agentic|tool[- ](?:use|using|calling)|function calling|"
        r"computer[- ]use|multi[- ]agent|browser automation)\b",
        re.IGNORECASE,
    ),
}
LLM_CONTEXT = re.compile(r"\b(?:llms?|language models?|foundation models?)\b", re.IGNORECASE)
AGENT_ACTIVITY = re.compile(
    r"\b(?:planning|reasoning|memory|tools?|workflow|autonomous)\b", re.IGNORECASE
)


def coarse_domain_match(
    title: str, abstract: str | None, categories: object
) -> tuple[float, list[str]]:
    """Conservative concept/category heuristic; the Agent still judges domain fit."""
    text = f"{title} {abstract or ''}"
    category_set = set(categories) if isinstance(categories, list) else set()
    hits = []
    score = 0.0
    for domain, pattern in DOMAIN_PATTERNS.items():
        matches = list(pattern.finditer(text))
        category_match = (domain == "embodied intelligence" and "cs.RO" in category_set) or (
            domain == "AI agents" and "cs.MA" in category_set
        )
        agent_context = (
            domain == "AI agents" and LLM_CONTEXT.search(text) and AGENT_ACTIVITY.search(text)
        )
        if matches or category_match or agent_context:
            hits.append(domain)
            score += 0.5 + min(0.5, 0.15 * len({match.group().casefold() for match in matches}))
            if pattern.search(title):
                score += 0.35
    return score, hits


def valid_string_list(value: object, *, categories: bool = False) -> bool:
    return (
        isinstance(value, list)
        and 0 < len(value) <= 30
        and all(
            isinstance(item, str)
            and bool(item.strip())
            and len(item) <= 100
            and (not categories or re.fullmatch(r"[a-zA-Z-]+(?:\.[A-Z]{2})?", item) is not None)
            for item in value
        )
        and len(set(value)) == len(value)
    )


def _identity_keys(arxiv_id: str | None, doi: str | None, title: str) -> set[str]:
    keys = set()
    if arxiv_id:
        parsed = parse_arxiv_id(arxiv_id)
        keys.add(f"arxiv:{parsed[0] if parsed else arxiv_id.strip().casefold()}")
    if doi:
        normalized = re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.IGNORECASE
        )
        keys.add(f"doi:{normalized.casefold()}")
    if normalized_title := normalize_title(title):
        keys.add(f"title:{normalized_title}")
    return keys


def discover_arxiv_page(
    session: Session, *, through_date: date, offset: int = 0, page_size: int = 100
) -> dict:
    """Fetch bounded raw pages on the server; return only novel prefiltered papers.

    No Paper is created here. Legacy offset/page_size arguments address upstream
    pages, not additional batches for the Agent to read.
    """
    categories = get_setting(session, "discovery_arxiv_categories", DISCOVERY_CATEGORIES)
    if not valid_string_list(categories, categories=True):
        raise ValueError("Invalid discovery_arxiv_categories setting")
    days = int(get_setting(session, "candidate_lookback_days", 3))
    scan_limit = int(get_setting(session, "discovery_scan_limit", 300))
    prefilter_limit = int(get_setting(session, "discovery_prefilter_limit", 120))
    if offset < 0 or offset >= scan_limit or not 1 <= page_size <= 100:
        raise ValueError("offset must be below discovery_scan_limit; page_size must be 1–100")

    def fetch_window(window_days: int) -> tuple[list, int | None, str]:
        since = through_date - timedelta(days=window_days - 1)
        query = (
            "(" + " OR ".join(f"cat:{category}" for category in categories) + ")"
            f" AND submittedDate:[{since:%Y%m%d}0000 TO {through_date:%Y%m%d}2359]"
        )
        raw_items = []
        next_raw_offset = offset
        total: int | None = None
        scan_end = scan_limit
        while next_raw_offset < scan_end:
            page = search_arxiv_page(
                query, start=next_raw_offset, max_results=min(page_size, scan_end - next_raw_offset)
            )
            reported_total = page["total_results"]
            if reported_total is None:
                raise ValueError(
                    "arXiv omitted totalResults; coverage cannot be verified, retry discovery"
                )
            total = reported_total
            scan_end = min(total, scan_limit)
            if not page["items"] and next_raw_offset < scan_end:
                raise ValueError(f"Empty arXiv page at offset {next_raw_offset}; retry discovery")
            raw_items.extend(page["items"])
            next_raw_offset += len(page["items"])
            if not page["items"]:
                break
        return raw_items, total, query

    # arXiv's recent-list date is an announcement date, while the API's
    # submittedDate is the original submission date. Monday-Wednesday listings
    # therefore need one wider request to include Friday submissions announced
    # over the weekend or after a holiday. Avoid a second request after a zero
    # result because arXiv rate-limits bursty API clients.
    fallback_applied = through_date.weekday() in {0, 1, 2} and days < DISCOVERY_FALLBACK_LOOKBACK_DAYS
    effective_days = DISCOVERY_FALLBACK_LOOKBACK_DAYS if fallback_applied else days
    raw_items, total, query = fetch_window(effective_days)
    since = through_date - timedelta(days=effective_days - 1)

    # Every library status is excluded, including candidate and new arXiv versions.
    # Read identities only, not full paper/analysis records.
    library_keys: set[str] = set()
    for row in session.execute(select(Paper.arxiv_id, Paper.doi, Paper.title)):
        library_keys.update(_identity_keys(row.arxiv_id, row.doi, row.title))
    items = []
    seen_keys: set[str] = set()
    skipped_existing = duplicate_count = rejected_count = 0
    for payload in raw_items:
        keys = _identity_keys(payload.arxiv_id, payload.doi, payload.title)
        repeated = bool(keys & seen_keys)
        seen_keys.update(keys)
        if repeated:
            duplicate_count += 1
            continue
        if keys & library_keys:
            skipped_existing += 1
            continue
        coarse_score, coarse_domains = coarse_domain_match(
            payload.title, payload.abstract, payload.metadata_json.get("categories", [])
        )
        if not coarse_domains:
            rejected_count += 1
            continue
        items.append(
            {
                **payload.model_dump(mode="json"),
                "existing_paper_id": None,
                "existing_status": None,
                "eligible": True,
                "coarse_score": round(coarse_score, 3),
                "coarse_domains": coarse_domains,
            }
        )
    # Preserve upstream recency on ties, rather than preferring older papers.
    items.sort(key=lambda item: item["coarse_score"], reverse=True)
    coarse_match_count = len(items)
    items = items[:prefilter_limit]
    scan_capped = total is not None and total > scan_limit
    result = {
        "query": query,
        "domains": get_setting(session, "discovery_domains", BROAD_DISCOVERY_DOMAINS),
        "candidate_target": get_setting(session, "abstract_limit", 100),
        "scan_limit": scan_limit,
        "prefilter_limit": prefilter_limit,
        "requested_lookback_days": days,
        "effective_lookback_days": effective_days,
        "fallback_applied": fallback_applied,
        "since_date": since.isoformat(),
        "through_date": through_date.isoformat(),
        "date_timezone": "UTC",
        "offset": offset,
        "page_size": page_size,
        "total_results": total,
        "next_offset": None,
        "scan_capped": scan_capped,
        "coverage_complete": not scan_capped and offset == 0,
        "scan_complete": True,
        "raw_scanned": len(raw_items),
        "coarse_match_count": coarse_match_count,
        "coarse_rejected_count": rejected_count,
        "prefiltered_count": len(items),
        "prefilter_capped": coarse_match_count > prefilter_limit,
        "skipped_existing_count": skipped_existing,
        "duplicate_count": duplicate_count,
        "items": items,
        "count": len(items),
    }
    log_operation(
        session,
        actor="agent",
        action="discovery.page_fetched",
        detail={key: value for key, value in result.items() if key != "items"},
    )
    session.commit()
    return result
