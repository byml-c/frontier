from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from lxml import etree

from ..schemas import PaperCreate
from .common import parse_arxiv_id
from .ingest import _arxiv_get, fetch_arxiv_ids

RSS_BASE = "https://rss.arxiv.org/rss/"
RSS_RESULT_LIMIT = 2000


def _text(node: Any, name: str) -> str:
    return " ".join((node.findtext(name) or "").split())


def _date(value: str) -> datetime:
    stamp = parsedate_to_datetime(value)
    if stamp.tzinfo is None:
        raise ValueError("RSS date is missing its timezone")
    return stamp.astimezone(UTC)


def _read_feed(categories: list[str]) -> tuple[list[PaperCreate], str]:
    response = _arxiv_get(
        {},
        url=RSS_BASE + "+".join(categories),
        request_timeout=15.0,
        max_attempts=2,
        retry_backoff=3.0,
    )
    root = etree.fromstring(response.content)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("arXiv returned an invalid RSS feed")
    channel = root.find("channel")
    batch_date = _date(_text(channel, "pubDate"))
    records = []
    for item in channel.findall("item"):
        parsed = parse_arxiv_id(_text(item, "guid"))
        if parsed is None or parsed[1] is None:
            raise ValueError("RSS record omitted its article ID or version")
        identifier, version = parsed
        title = _text(item, "title")
        description = _text(item, "description")
        abstract = re.split(r"\bAbstract:\s*", description, maxsplit=1)
        if not title or len(abstract) != 2 or not abstract[1]:
            raise ValueError("RSS record omitted its title or abstract")
        announced = _date(_text(item, "pubDate"))
        authors = [
            {"name": name.strip()}
            for name in _text(item, "{http://purl.org/dc/elements/1.1/}creator").split(",")
            if name.strip()
        ]
        classes = [_text(node, ".") for node in item.findall("category")]
        records.append(
            PaperCreate(
                title=title,
                abstract=abstract[1],
                authors=authors,
                first_author=authors[0]["name"] if authors else None,
                source="arxiv",
                arxiv_id=identifier,
                arxiv_version=version,
                source_url=f"https://arxiv.org/abs/{identifier}v{version}",
                pdf_url=f"https://arxiv.org/pdf/{identifier}v{version}",
                doi=_text(item, "{http://arxiv.org/schemas/atom}DOI") or None,
                metadata_json={
                    "metadata_source": "rss",
                    "categories": classes,
                    "primary_category": classes[0] if classes else None,
                    "arxiv_announced_at": announced.isoformat(),
                    "arxiv_announce_type": _text(
                        item, "{http://arxiv.org/schemas/atom}announce_type"
                    ),
                    # RSS pubDate is an announcement date, never a submission/version timestamp.
                    "arxiv_timestamp_pending": True,
                },
            )
        )
    return records, batch_date.isoformat()


def fetch_rss_records(
    *, end: datetime, categories: list[str], pending: list[PaperCreate] | None = None
) -> tuple[list[PaperCreate], dict]:
    """Consume the entire current announcement batch, including late/old IDs."""
    records, batch_date = _read_feed(categories)
    pages = 1
    if len(records) >= RSS_RESULT_LIMIT:
        records = []
        dates = []
        for category in categories:
            items, stamp = _read_feed([category])
            pages += 1
            if len(items) >= RSS_RESULT_LIMIT:
                raise ValueError(f"RSS category {category} may be truncated")
            records.extend(items)
            dates.append(stamp)
        if len(set(dates)) != 1:
            raise ValueError("RSS category feeds belong to different announcement batches")
        batch_date = dates[0]
    if datetime.fromisoformat(batch_date) > end:
        raise ValueError("RSS only contains a newer batch than the requested cutoff")
    # Do not stop on the first existing/old article. RSS is grouped by announcement type.
    unique: dict[tuple[str | None, int | None], PaperCreate] = {}
    for payload in [*records, *(pending or [])]:
        if datetime.fromisoformat(payload.metadata_json["arxiv_announced_at"]) <= end:
            unique[(payload.arxiv_id, payload.arxiv_version)] = payload
    records = list(unique.values())
    enrichment_error = None
    for offset in range(0, len(records), 100):
        batch = records[offset : offset + 100]
        try:
            enriched = fetch_arxiv_ids([f"{item.arxiv_id}v{item.arxiv_version}" for item in batch])
            by_id = {(item.arxiv_id, item.arxiv_version): item for item in enriched}
            for index, rss in enumerate(batch, start=offset):
                payload = by_id[(rss.arxiv_id, rss.arxiv_version)]
                updated = payload.metadata_json.get("arxiv_updated_at")
                if not isinstance(updated, str) or datetime.fromisoformat(updated).tzinfo is None:
                    raise ValueError("API omitted the article version timestamp")
                payload.metadata_json = {
                    **rss.metadata_json,
                    **payload.metadata_json,
                    "metadata_source": "rss+atom",
                    "arxiv_timestamp_pending": False,
                }
                records[index] = payload
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # Metadata service availability must not discard valid RSS announcements.
            enrichment_error = str(exc)
            break
    pending = sum(bool(item.metadata_json.get("arxiv_timestamp_pending")) for item in records)
    return records, {
        "source": "rss",
        "pages": pages,
        "source_records": len(records),
        "feed_announced_at": batch_date,
        "metadata_pending": pending,
        "metadata_error": enrichment_error,
        "complete": True,
        "cursor_can_advance": pending == 0,
        "coverage_kind": "announcements+article-versions",
    }
