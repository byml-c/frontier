from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Annotation,
    ArchitectureDocument,
    Message,
    Paper,
    PaperNode,
    PaperTag,
    ReadingProgress,
    ResearchNode,
    Tag,
)
from .common import as_json_value
from .completion_summary import completion_summary_structured
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


def _pdf_version(paper: Paper) -> str | None:
    if not paper.pdf_path:
        return None
    path = Path(paper.pdf_path)
    if not path.is_file():
        return None
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _paper_facts(paper: Paper) -> dict[str, Any]:
    return {field: as_json_value(getattr(paper, field)) for field in _PAPER_FACT_FIELDS}


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


def _conversation_summary(session: Session, paper_id: str) -> dict[str, Any] | None:
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
    recent = list(
        session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(_RECENT_MESSAGE_LIMIT)
        ).all()
    )
    recent.reverse()
    return {
        "id": conversation.id,
        "title": conversation.title,
        "status": conversation.status,
        "has_codex_session": bool(conversation.codex_session_id),
        "message_count": int(message_count or 0),
        "updated_at": as_json_value(conversation.updated_at),
        "recent_messages": [
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
        ],
    }


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
) -> dict[str, Any]:
    """Compose durable paper facts for an Agent without creating another state store."""
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
        "conversation": _conversation_summary(session, paper.id),
        "architecture": _architecture_summary(session, paper.id, current_pdf_version=pdf_version),
        "document": {
            "has_pdf": pdf_version is not None,
            "pdf_version": pdf_version,
            "full_text_included": False,
        },
    }
