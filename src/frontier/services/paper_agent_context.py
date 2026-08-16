from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Annotation,
    ArchitectureDocument,
    Conversation,
    Message,
    Paper,
    PaperAgentContextSync,
    PaperNode,
    PaperTag,
    ReadingProgress,
    ResearchNode,
    Tag,
)
from .common import as_json_value
from .completion_summary import completion_summary_structured
from .interest_evidence import paper_interest_level, paper_quality_level
from .paper_conversations import find_paper_conversation

_PAPER_FACT_FIELDS = (
    "id",
    "title",
    "abstract",
    "authors",
    "first_author",
    "corresponding_author",
    "primary_affiliation",
    "source",
    "source_url",
    "pdf_url",
    "arxiv_id",
    "arxiv_version",
    "doi",
    "published_at",
    "status",
    "ignored_reason",
    "summary",
    "agent_comment",
    "problem",
    "innovation",
    "experiment",
    "limitations",
    "research_score",
    "relevance_score",
    "recommendation_reason",
    "is_interested",
    "is_quality",
    "reading_completed_at",
    "pending_feedback",
)
_RECENT_MESSAGE_LIMIT = 6
_MESSAGE_CONTENT_LIMIT = 2_000
_FULL_ANALYSIS_CHAR_BUDGET = 24_000
_ANNOTATIONS_CHAR_BUDGET = 16_000
_ANNOTATION_FIELD_LIMIT = 2_000
_SYNC_SCHEMA_VERSION = 1
# Conversation history already lives in the Codex thread, while volatile page,
# scroll, and zoom progress arrives in each UI request. Neither belongs in the
# durable paper-state delta.
_SYNC_SECTION_KEYS = (
    "paper",
    "full_analysis",
    "completion_summary_structured",
    "metadata",
    "tags",
    "nodes",
    "annotations",
    "architecture",
    "document",
)


def _pdf_version(paper: Paper) -> str | None:
    if not paper.pdf_path:
        return None
    path = Path(paper.pdf_path)
    if not path.is_file():
        return None
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _paper_facts(paper: Paper) -> dict[str, Any]:
    return {
        **{field: as_json_value(getattr(paper, field)) for field in _PAPER_FACT_FIELDS},
        "interest_level": paper_interest_level(paper),
        "quality_level": paper_quality_level(paper),
        "open_questions": (paper.metadata_json or {}).get("open_questions"),
    }


def _curated_metadata(paper: Paper) -> dict[str, Any]:
    """Expose durable semantic metadata, not workflow, provenance, or cache internals."""
    metadata = paper.metadata_json or {}
    personal_comment = metadata.get("personal_comment")
    suggested_tags = metadata.get("suggested_tags")
    return {
        "personal_comment": personal_comment if isinstance(personal_comment, str) else None,
        "suggested_tags": suggested_tags if isinstance(suggested_tags, list) else [],
    }


def _annotations(session: Session, paper_id: str) -> list[dict[str, Any]]:
    items = session.scalars(
        select(Annotation)
        .where(Annotation.paper_id == paper_id)
        .order_by(Annotation.page_number, Annotation.created_at, Annotation.id)
    ).all()
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "page_number": item.page_number,
            "selected_text": item.selected_text,
            "content": item.content,
            "updated_at": as_json_value(item.updated_at),
        }
        for item in items
    ]


def _reading_progress(session: Session, paper_id: str) -> dict[str, Any]:
    item = session.get(ReadingProgress, paper_id)
    if item is None:
        return {
            "saved": False,
            "page_number": 1,
            "scroll_offset": 0,
            "zoom": 1,
            "updated_at": None,
        }
    return {
        "saved": True,
        "page_number": item.page_number,
        "scroll_offset": item.scroll_offset,
        "zoom": item.zoom,
        "updated_at": as_json_value(item.updated_at),
    }


def _conversation_summary(
    session: Session, paper_id: str, *, include_recent_messages: bool = True
) -> dict[str, Any] | None:
    conversation = find_paper_conversation(
        session,
        paper_id,
        include_archived_fallback=True,
    )
    if conversation is None:
        return None

    message_count = session.scalar(
        select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
    )
    result = {
        "id": conversation.id,
        "title": conversation.title,
        "status": conversation.status,
        "has_codex_session": bool(conversation.codex_session_id),
        "message_count": int(message_count or 0),
        "updated_at": as_json_value(conversation.updated_at),
    }
    if not include_recent_messages:
        return result
    recent = list(
        session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(_RECENT_MESSAGE_LIMIT)
        ).all()
    )
    recent.reverse()
    result["recent_messages"] = [
        {
            "role": message.role,
            "content": (
                message.content
                if len(message.content) <= _MESSAGE_CONTENT_LIMIT
                else f"{message.content[:_MESSAGE_CONTENT_LIMIT]}…"
            ),
            "citations": message.citations_json or [],
            "created_at": as_json_value(message.created_at),
        }
        for message in recent
    ]
    return result


def _architecture_summary(
    session: Session, paper_id: str, *, current_pdf_version: str | None
) -> dict[str, Any]:
    item = session.get(ArchitectureDocument, paper_id)
    if item is None:
        return {
            "status": "missing",
            "revision": 0,
            "schema_version": 1,
            "source_pdf_version": None,
            "current_pdf_version": current_pdf_version,
            "stale": False,
            "updated_at": None,
        }
    return {
        "status": item.status,
        "revision": item.revision,
        "schema_version": item.schema_version,
        "source_pdf_version": item.source_pdf_version,
        "current_pdf_version": current_pdf_version,
        "stale": bool(
            item.source_pdf_version
            and current_pdf_version
            and item.source_pdf_version != current_pdf_version
        ),
        "updated_at": as_json_value(item.updated_at),
    }


def build_paper_agent_context(
    session: Session,
    paper: Paper,
    *,
    query: str | None = None,
    include_recent_messages: bool = True,
) -> dict[str, Any]:
    """Compose durable paper facts for an Agent without creating another state store."""
    context = _raw_paper_agent_context(
        session,
        paper,
        query=query,
        include_recent_messages=include_recent_messages,
    )
    bounded, limits = _bounded_context(context)
    if limits:
        bounded["context_limits"] = limits
    return bounded


def sync_paper_agent_context(
    session: Session,
    paper: Paper,
    conversation: Conversation,
    *,
    query: str | None = None,
) -> dict[str, Any]:
    """Return only paper-state sections not yet seen by this Codex thread."""
    raw_context = _raw_paper_agent_context(
        session,
        paper,
        query=query,
        include_recent_messages=False,
    )
    context, limits = _bounded_context(raw_context)
    sections = {key: context[key] for key in _SYNC_SECTION_KEYS}
    hashes = _section_hashes(raw_context)
    revision = _content_hash(hashes)
    cursor = session.get(PaperAgentContextSync, conversation.id)
    thread_id = conversation.codex_session_id
    full_sync = bool(
        cursor is None
        or not thread_id
        or cursor.codex_session_id != thread_id
        or cursor.schema_version != _SYNC_SCHEMA_VERSION
    )
    if full_sync:
        changed = list(_SYNC_SECTION_KEYS)
        mode = "full"
    else:
        previous = cursor.section_hashes_json or {}
        changed = [key for key in _SYNC_SECTION_KEYS if previous.get(key) != hashes[key]]
        mode = "delta" if changed else "unchanged"

    if thread_id:
        if cursor is None:
            cursor = PaperAgentContextSync(
                conversation_id=conversation.id,
                paper_id=paper.id,
                codex_session_id=thread_id,
                schema_version=_SYNC_SCHEMA_VERSION,
                revision=revision,
                section_hashes_json=hashes,
            )
            session.add(cursor)
        else:
            cursor.paper_id = paper.id
            cursor.codex_session_id = thread_id
            cursor.schema_version = _SYNC_SCHEMA_VERSION
            cursor.revision = revision
            cursor.section_hashes_json = hashes
        session.flush()

    return {
        "schema_version": 2,
        "sync": {
            "mode": mode,
            "revision": revision,
            "changed_sections": changed,
            "thread_bound": bool(thread_id),
        },
        "request_context": context["request_context"],
        "context": {key: sections[key] for key in changed},
        "context_limits": {key: limits[key] for key in changed if key in limits},
    }


def paper_agent_context_sync_required(
    session: Session,
    paper: Paper,
    conversation: Conversation,
) -> bool:
    """Check for unseen paper state without advancing the Conversation cursor."""
    thread_id = conversation.codex_session_id
    cursor = session.get(PaperAgentContextSync, conversation.id)
    if (
        not thread_id
        or cursor is None
        or cursor.codex_session_id != thread_id
        or cursor.schema_version != _SYNC_SCHEMA_VERSION
    ):
        return True
    raw_context = _raw_paper_agent_context(
        session,
        paper,
        query=None,
        include_recent_messages=False,
    )
    hashes = _section_hashes(raw_context)
    previous = cursor.section_hashes_json or {}
    return any(previous.get(key) != hashes[key] for key in _SYNC_SECTION_KEYS)


def _raw_paper_agent_context(
    session: Session,
    paper: Paper,
    *,
    query: str | None,
    include_recent_messages: bool,
) -> dict[str, Any]:
    """Build the unbounded value used for change detection, never for Agent output."""
    tags = session.execute(
        select(Tag.id, Tag.name, Tag.color)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper.id)
        .order_by(Tag.name, Tag.id)
    ).all()
    nodes = session.execute(
        select(
            ResearchNode.id,
            ResearchNode.name,
            ResearchNode.core_question,
            PaperNode.reason,
            PaperNode.is_key,
        )
        .join(PaperNode, PaperNode.node_id == ResearchNode.id)
        .where(PaperNode.paper_id == paper.id)
        .order_by(ResearchNode.name, ResearchNode.id)
    ).all()
    pdf_version = _pdf_version(paper)
    normalized_query = query.strip() if query and query.strip() else None
    return {
        "schema_version": 1,
        "request_context": {"query": normalized_query},
        "paper": _paper_facts(paper),
        "full_analysis": paper.full_analysis or {},
        "completion_summary_structured": completion_summary_structured(
            paper, include_user_attitude=True
        ),
        "metadata": _curated_metadata(paper),
        "tags": [dict(row._mapping) for row in tags],
        "nodes": [dict(row._mapping) for row in nodes],
        "annotations": _annotations(session, paper.id),
        "reading_progress": _reading_progress(session, paper.id),
        "conversation": (
            _conversation_summary(session, paper.id)
            if include_recent_messages
            else None
        ),
        "architecture": _architecture_summary(session, paper.id, current_pdf_version=pdf_version),
        "document": {
            "has_pdf": pdf_version is not None,
            "pdf_version": pdf_version,
            "full_text_included": False,
        },
    }


def _bounded_context(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = dict(context)
    limits: dict[str, Any] = {}

    analysis = context.get("full_analysis") or {}
    analysis_size = _json_chars(analysis)
    if analysis_size > _FULL_ANALYSIS_CHAR_BUDGET:
        result["full_analysis"] = _compact_json(analysis, _FULL_ANALYSIS_CHAR_BUDGET)
        limits["full_analysis"] = {
            "truncated": True,
            "original_chars": analysis_size,
            "delivered_chars": _json_chars(result["full_analysis"]),
            "hint": "Use exact paper pages or structured resources for omitted detail.",
        }

    annotations = context.get("annotations") or []
    compact_annotations = [_compact_annotation(item) for item in annotations]
    if _json_chars(compact_annotations) > _ANNOTATIONS_CHAR_BUDGET:
        newest = sorted(
            compact_annotations,
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        included: list[dict[str, Any]] = []
        for item in newest:
            candidate = [*included, item]
            if _json_chars(candidate) > _ANNOTATIONS_CHAR_BUDGET:
                break
            included.append(item)
        included.sort(key=lambda item: (item.get("page_number") or 0, str(item.get("id") or "")))
        result["annotations"] = included
        limits["annotations"] = {
            "truncated": True,
            "total_items": len(annotations),
            "delivered_items": len(included),
            "hint": "Use `frontier paper context --no-include-text` when older annotations are needed.",
        }
    elif compact_annotations != annotations:
        result["annotations"] = compact_annotations
        limits["annotations"] = {
            "truncated": True,
            "total_items": len(annotations),
            "delivered_items": len(compact_annotations),
            "hint": "Long annotation fields were shortened; request annotations on demand for exact text.",
        }
    return result, limits


def _compact_annotation(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    for key in ("selected_text", "content"):
        value = result.get(key)
        if isinstance(value, str) and len(value) > _ANNOTATION_FIELD_LIMIT:
            result[key] = f"{value[: _ANNOTATION_FIELD_LIMIT - 1]}…"
    return result


def _compact_json(value: Any, budget: int) -> Any:
    if _json_chars(value) <= budget:
        return value
    if isinstance(value, str):
        if budget <= 4:
            return ""
        return f"{value[: budget - 3]}…"
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            remaining = budget - _json_chars(result) - 2
            if remaining < 32:
                break
            compacted = _compact_json(item, remaining)
            candidate = [*result, compacted]
            if _json_chars(candidate) > budget:
                break
            result.append(compacted)
        return result
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            remaining = budget - _json_chars(result) - _json_chars(str(key)) - 4
            if remaining < 32:
                break
            compacted = _compact_json(item, remaining)
            candidate = {**result, key: compacted}
            if _json_chars(candidate) > budget:
                break
            result[key] = compacted
        return result
    return value


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _content_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _section_hashes(context: dict[str, Any]) -> dict[str, str]:
    return {key: _content_hash(context[key]) for key in _SYNC_SECTION_KEYS}
