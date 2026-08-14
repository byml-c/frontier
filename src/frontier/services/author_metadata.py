from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, SoupStrainer, Tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import OperationLog, Paper
from .common import log_operation, parse_arxiv_id

AUTHOR_METADATA_PARSER_VERSION = 1
BIBLIOGRAPHIC_PROVENANCE_KEY = "bibliographic_provenance"
MANUAL_BIBLIOGRAPHIC_FIELDS_KEY = "manual_bibliographic_fields"
BIBLIOGRAPHIC_FIELDS = frozenset(
    {"authors", "first_author", "corresponding_author", "primary_affiliation"}
)

_CORRESPONDING_RE = re.compile(r"\bcorrespond(?:ing|ence)\b", re.IGNORECASE)
_EQUAL_CONTRIBUTION_RE = re.compile(
    r"\b(?:equal(?:ly)?\s+contribut|co[- ]?first|joint\s+first)\b", re.IGNORECASE
)
_CONTACT_PREFIX_RE = re.compile(
    r"^(?:affiliation|thanks?|email|address)\s*:\s*", re.IGNORECASE
)
logger = logging.getLogger(__name__)
_backfill_lock = threading.Lock()
_backfill_running = False


def extract_arxiv_html_author_metadata(
    html: str, known_authors: list[dict[str, Any]]
) -> dict[str, Any]:
    """Extract only explicit author roles and affiliations from LaTeXML HTML.

    arXiv abstract metadata remains the authority for author order. The HTML title
    block is used to enrich those known names; ambiguous markers are deliberately
    left unresolved instead of guessing a role.
    """
    authors = [dict(author) for author in known_authors if str(author.get("name") or "").strip()]
    for author in authors:
        author["name"] = _clean_text(str(author["name"]))
    soup = BeautifulSoup(
        html,
        "lxml",
        parse_only=SoupStrainer(class_=lambda value: value and "ltx_authors" in value),
    )
    container = soup.select_one(".ltx_authors")
    warnings: list[str] = []
    if not isinstance(container, Tag):
        return _author_metadata_result(authors, [], warnings=["arXiv HTML 没有作者结构"])

    by_key = {_name_key(str(author["name"])): author for author in authors}
    global_affiliations: list[str] = []
    creators = container.select(".ltx_creator.ltx_role_author")
    for creator in creators:
        creator_names = _matched_authors(creator, authors)
        affiliations = _contact_values(creator, "ltx_role_affiliation")
        notes = _contact_values(creator, "ltx_role_thanks")
        global_affiliations.extend(affiliations)
        note_text = " ".join(notes)
        note_name_matches = _matched_names(note_text, authors)

        targets = creator_names if len(creator_names) == 1 else note_name_matches
        if affiliations and len(creator_names) == 1:
            _merge_list(by_key[_name_key(creator_names[0])], "affiliations", affiliations)
        elif affiliations and len(creator_names) > 1 and len(affiliations) == 1:
            for name in creator_names:
                _merge_list(by_key[_name_key(name)], "affiliations", affiliations)

        if _CORRESPONDING_RE.search(note_text):
            for name in targets:
                by_key[_name_key(name)]["is_corresponding"] = True
        if _EQUAL_CONTRIBUTION_RE.search(note_text):
            for name in targets:
                by_key[_name_key(name)]["is_equal_contributor"] = True

    shared_affiliations = _shared_affiliations(container)
    global_affiliations = _unique([*global_affiliations, *shared_affiliations])
    if len(global_affiliations) == 1:
        for author in authors:
            if not author.get("affiliations"):
                _merge_list(author, "affiliations", global_affiliations)

    all_notes = " ".join(_contact_values(container, "ltx_role_thanks"))
    if _CORRESPONDING_RE.search(all_notes):
        explicit_names = _matched_names(all_notes, authors)
        for name in explicit_names:
            by_key[_name_key(name)]["is_corresponding"] = True
        if not explicit_names and not any(author.get("is_corresponding") for author in authors):
            warnings.append("通讯作者脚注存在，但无法可靠映射到具体作者")

    primary_affiliation = None
    if authors:
        first_affiliations = authors[0].get("affiliations") or []
        primary_affiliation = first_affiliations[0] if first_affiliations else None
    if not primary_affiliation and global_affiliations:
        primary_affiliation = global_affiliations[0]
    corresponding = [
        str(author["name"]) for author in authors if author.get("is_corresponding") is True
    ]
    return _author_metadata_result(
        authors,
        global_affiliations,
        corresponding_authors=corresponding,
        primary_affiliation=primary_affiliation,
        warnings=warnings,
    )


def record_manual_bibliographic_changes(
    paper: Paper, original_values: dict[str, Any], changes: dict[str, Any]
) -> set[str]:
    changed = {
        field
        for field in BIBLIOGRAPHIC_FIELDS
        if field in changes and original_values.get(field) != changes[field]
    }
    if not changed:
        return set()
    metadata = dict(paper.metadata_json or {})
    protected = {
        str(field)
        for field in metadata.get(MANUAL_BIBLIOGRAPHIC_FIELDS_KEY, [])
        if str(field) in BIBLIOGRAPHIC_FIELDS
    }
    protected.update(changed)
    provenance = dict(metadata.get(BIBLIOGRAPHIC_PROVENANCE_KEY) or {})
    fields = dict(provenance.get("fields") or {})
    for field in changed:
        fields[field] = {"source": "manual", "confidence": "authoritative"}
    provenance["fields"] = fields
    metadata[MANUAL_BIBLIOGRAPHIC_FIELDS_KEY] = sorted(protected)
    metadata[BIBLIOGRAPHIC_PROVENANCE_KEY] = provenance
    paper.metadata_json = metadata
    return changed


def preserve_author_details_for_unchanged_names(
    current: list[dict[str, Any]], submitted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    current_names = [_name_key(str(author.get("name") or "")) for author in current]
    submitted_names = [_name_key(str(author.get("name") or "")) for author in submitted]
    if current_names == submitted_names:
        return current
    return submitted


def manual_bibliographic_fields(session: Session, paper: Paper) -> set[str]:
    metadata = paper.metadata_json or {}
    protected = {
        str(field)
        for field in metadata.get(MANUAL_BIBLIOGRAPHIC_FIELDS_KEY, [])
        if str(field) in BIBLIOGRAPHIC_FIELDS
    }
    rows = session.scalars(
        select(OperationLog).where(
            OperationLog.entity_type == "paper",
            OperationLog.entity_id == paper.id,
            OperationLog.actor == "user",
            OperationLog.action == "paper.metadata_updated",
        )
    )
    for row in rows:
        detail = row.detail_json or {}
        fields = detail.get("manually_changed_bibliographic_fields")
        if fields is None:
            fields = detail.get("fields", [])
        protected.update(
            str(field)
            for field in fields
            if str(field) in BIBLIOGRAPHIC_FIELDS
        )
    return protected


def apply_arxiv_html_author_metadata(
    session: Session,
    paper: Paper,
    extracted: dict[str, Any],
    *,
    source_url: str | None,
) -> set[str]:
    protected = manual_bibliographic_fields(session, paper)
    changed: set[str] = set()
    extracted_authors = extracted.get("authors") or []
    if extracted_authors and "authors" not in protected:
        merged_authors = _merge_authors(paper.authors or [], extracted_authors)
        if merged_authors != (paper.authors or []):
            paper.authors = merged_authors
            changed.add("authors")

    first_author = str(extracted.get("first_author") or "").strip() or None
    if first_author and not paper.first_author and "first_author" not in protected:
        paper.first_author = first_author
        changed.add("first_author")

    corresponding = [
        str(value).strip() for value in extracted.get("corresponding_authors", []) if str(value).strip()
    ]
    corresponding_value = ", ".join(_unique(corresponding)) or None
    if (
        corresponding_value
        and "corresponding_author" not in protected
        and paper.corresponding_author != corresponding_value
    ):
        paper.corresponding_author = corresponding_value
        changed.add("corresponding_author")

    affiliation = str(extracted.get("primary_affiliation") or "").strip() or None
    if (
        affiliation
        and "primary_affiliation" not in protected
        and paper.primary_affiliation != affiliation
    ):
        paper.primary_affiliation = affiliation
        changed.add("primary_affiliation")

    metadata = dict(paper.metadata_json or {})
    provenance = dict(metadata.get(BIBLIOGRAPHIC_PROVENANCE_KEY) or {})
    field_sources = dict(provenance.get("fields") or {})
    for field in changed:
        field_sources[field] = {
            "source": "arxiv_html_explicit",
            "confidence": "high",
        }
    provenance.update(
        {
            "parser_version": AUTHOR_METADATA_PARSER_VERSION,
            "source": "arxiv_html",
            "source_url": source_url,
            "arxiv_version": paper.arxiv_version,
            "updated_at": datetime.now(UTC).isoformat(),
            "fields": field_sources,
            "warnings": list(extracted.get("warnings") or []),
        }
    )
    metadata[BIBLIOGRAPHIC_PROVENANCE_KEY] = provenance
    if protected:
        metadata[MANUAL_BIBLIOGRAPHIC_FIELDS_KEY] = sorted(protected)
    paper.metadata_json = metadata
    if changed:
        log_operation(
            session,
            actor="system",
            action="paper.author_metadata_enriched",
            entity_type="paper",
            entity_id=paper.id,
            detail={"fields": sorted(changed), "source": "arxiv_html"},
        )
    return changed


def backfill_cached_arxiv_author_metadata(session: Session) -> int:
    updated = 0
    papers = session.scalars(select(Paper).where(Paper.arxiv_id.is_not(None))).all()
    for paper in papers:
        root = settings.document_dir / paper.id
        html_path = root / "arxiv.html"
        if not html_path.exists():
            continue
        metadata = paper.metadata_json or {}
        provenance = metadata.get(BIBLIOGRAPHIC_PROVENANCE_KEY) or {}
        if (
            provenance.get("parser_version") == AUTHOR_METADATA_PARSER_VERSION
            and provenance.get("arxiv_version") == paper.arxiv_version
        ):
            continue
        source_url = _cached_source_url(root, paper)
        parsed_source = parse_arxiv_id(source_url or "")
        if (
            parsed_source
            and parsed_source[1]
            and paper.arxiv_version
            and parsed_source[1] != paper.arxiv_version
        ):
            continue
        extracted = extract_arxiv_html_author_metadata(
            html_path.read_text(encoding="utf-8"), paper.authors or []
        )
        changed = apply_arxiv_html_author_metadata(
            session, paper, extracted, source_url=source_url
        )
        if changed:
            updated += 1
    session.commit()
    return updated


def queue_cached_arxiv_author_metadata_backfill() -> bool:
    """Run the potentially large one-time cache scan outside application startup."""
    global _backfill_running
    with _backfill_lock:
        if _backfill_running:
            return False
        _backfill_running = True

    def run() -> None:
        global _backfill_running
        try:
            with SessionLocal() as session:
                updated = backfill_cached_arxiv_author_metadata(session)
            if updated:
                logger.info("Backfilled arXiv HTML author metadata for %s papers", updated)
        except Exception:
            logger.exception("Cached arXiv author metadata backfill failed")
        finally:
            with _backfill_lock:
                _backfill_running = False

    thread = threading.Thread(target=run, name="arxiv-author-backfill", daemon=True)
    try:
        thread.start()
    except Exception:
        with _backfill_lock:
            _backfill_running = False
        logger.exception("Could not start cached arXiv author metadata backfill")
        return False
    return True


def _cached_source_url(root: Path, paper: Paper) -> str:
    reader_path = root / "reader.json"
    if reader_path.exists():
        try:
            value = json.loads(reader_path.read_text(encoding="utf-8")).get("source_url")
            if value:
                return str(value)
        except (OSError, ValueError, TypeError):
            pass
    identifier = paper.arxiv_id or ""
    if paper.arxiv_version:
        identifier = f"{identifier}v{paper.arxiv_version}"
    return f"https://arxiv.org/html/{identifier}"


def _author_metadata_result(
    authors: list[dict[str, Any]],
    affiliations: list[str],
    *,
    corresponding_authors: list[str] | None = None,
    primary_affiliation: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "parser_version": AUTHOR_METADATA_PARSER_VERSION,
        "source": "arxiv_html",
        "authors": authors,
        "first_author": str(authors[0]["name"]) if authors else None,
        "corresponding_authors": corresponding_authors or [],
        "affiliations": _unique(affiliations),
        "primary_affiliation": primary_affiliation,
        "warnings": warnings or [],
    }


def _matched_authors(node: Tag, authors: list[dict[str, Any]]) -> list[str]:
    person = node.select_one(".ltx_personname")
    return _matched_names(person.get_text(" ", strip=True) if person else "", authors)


def _matched_names(text: str, authors: list[dict[str, Any]]) -> list[str]:
    haystack = f" {_name_key(text)} "
    matched: list[str] = []
    for author in authors:
        name = str(author.get("name") or "")
        key = _name_key(name)
        if key and (f" {key} " in haystack or (" " not in key and key in haystack)):
            matched.append(name)
    return matched


def _contact_values(node: Tag, role: str) -> list[str]:
    values: list[str] = []
    for contact in node.select(f".ltx_contact.{role}, .{role}"):
        value = _clean_text(contact.get_text(" ", strip=True))
        value = _CONTACT_PREFIX_RE.sub("", value).strip()
        if value:
            values.append(value)
    return _unique(values)


def _shared_affiliations(container: Tag) -> list[str]:
    values: list[str] = []
    for person in container.select(".ltx_personname"):
        segments: list[str] = []
        current: list[str] = []
        for child in person.children:
            if isinstance(child, Tag) and child.name == "br":
                value = _clean_text(" ".join(current))
                if value:
                    segments.append(value)
                current = []
            elif isinstance(child, NavigableString):
                current.append(str(child))
            elif isinstance(child, Tag):
                if child.name != "a":
                    current.append(child.get_text(" ", strip=True))
        value = _clean_text(" ".join(current))
        if value:
            segments.append(value)
        for segment in segments[1:]:
            if _looks_like_affiliation(segment):
                values.append(segment)
    return _unique(values)


def _looks_like_affiliation(value: str) -> bool:
    lowered = value.lower()
    if "@" in value or "http://" in lowered or "https://" in lowered:
        return False
    if _CORRESPONDING_RE.search(value) or _EQUAL_CONTRIBUTION_RE.search(value):
        return False
    return len(value) >= 4


def _merge_authors(
    current: list[dict[str, Any]], extracted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    extracted_by_name = {
        _name_key(str(author.get("name") or "")): author for author in extracted
    }
    merged: list[dict[str, Any]] = []
    for current_author in current:
        item = dict(current_author)
        source = extracted_by_name.get(_name_key(str(item.get("name") or "")))
        if source:
            if source.get("affiliations"):
                _merge_list(item, "affiliations", list(source["affiliations"]))
            for key in ("is_corresponding", "is_equal_contributor"):
                if source.get(key) is True:
                    item[key] = True
        merged.append(item)
    return merged


def _merge_list(target: dict[str, Any], key: str, values: list[str]) -> None:
    target[key] = _unique([*(target.get(key) or []), *values])


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(str(value))
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u2003", " ").replace("\xa0", " ").split())


def _name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[\w]+", without_marks, flags=re.UNICODE))
