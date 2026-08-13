from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, index=True)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    abstract: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    first_author: Mapped[str | None] = mapped_column(String(255))
    corresponding_author: Mapped[str | None] = mapped_column(String(255))
    primary_affiliation: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), default="manual", index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    arxiv_version: Mapped[int | None] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_considered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    ignored_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    agent_comment: Mapped[str | None] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)
    innovation: Mapped[str | None] = mapped_column(Text)
    experiment: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    full_analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    research_score: Mapped[float | None] = mapped_column(Float)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    recommendation_reason: Mapped[str | None] = mapped_column(Text)
    is_interested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_quality: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reading_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_summary: Mapped[str | None] = mapped_column(Text)
    pending_feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PaperVersion(Base, TimestampMixin):
    __tablename__ = "paper_versions"
    __table_args__ = (UniqueConstraint("paper_id", "version", name="uq_paper_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResearchNode(Base, TimestampMixin):
    __tablename__ = "research_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    core_question: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    solved: Mapped[str | None] = mapped_column(Text)
    open_questions: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(100), index=True)
    color: Mapped[str | None] = mapped_column(String(20))
    layout_x: Mapped[float | None] = mapped_column(Float)
    layout_y: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ResearchEdge(Base, TimestampMixin):
    __tablename__ = "research_edges"
    __table_args__ = (UniqueConstraint("source_id", "target_id", name="uq_research_edge"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("research_nodes.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("research_nodes.id", ondelete="CASCADE"), index=True
    )


class PaperNode(Base, TimestampMixin):
    __tablename__ = "paper_nodes"
    __table_args__ = (UniqueConstraint("paper_id", "node_id", name="uq_paper_node"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("research_nodes.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str | None] = mapped_column(Text)
    is_key: Mapped[bool] = mapped_column(Boolean, default=False)


class NodeProgress(Base, TimestampMixin):
    __tablename__ = "node_progress"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("research_nodes.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    happened_at: Mapped[date | None] = mapped_column(Date)


class ResearchIdea(Base, TimestampMixin):
    __tablename__ = "research_ideas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("research_nodes.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="to_explore", index=True)
    source_type: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)


class Brief(Base, TimestampMixin):
    __tablename__ = "briefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brief_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    introduction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BriefItem(Base, TimestampMixin):
    __tablename__ = "brief_items"
    __table_args__ = (UniqueConstraint("brief_id", "paper_id", name="uq_brief_paper"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brief_id: Mapped[str] = mapped_column(ForeignKey("briefs.id", ondelete="CASCADE"), index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(32), default="related", index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DailySelection(Base, TimestampMixin):
    __tablename__ = "daily_selections"

    selection_date: Mapped[date] = mapped_column(Date, primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    color: Mapped[str | None] = mapped_column(String(20))


class PaperTag(Base, TimestampMixin):
    __tablename__ = "paper_tags"
    __table_args__ = (UniqueConstraint("paper_id", "tag_id", name="uq_paper_tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


class Annotation(Base, TimestampMixin):
    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    selected_text: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    position_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    color: Mapped[str | None] = mapped_column(String(20))


class TranslationCache(Base, TimestampMixin):
    __tablename__ = "translation_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    selection_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, index=True)
    selected_text: Mapped[str] = mapped_column(Text)
    target_language: Mapped[str] = mapped_column(String(50), default="简体中文")
    content: Mapped[str] = mapped_column(Text)
    position_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ReadingProgress(Base, TimestampMixin):
    __tablename__ = "reading_progress"

    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    scroll_offset: Mapped[float] = mapped_column(Float, default=0)
    zoom: Mapped[float] = mapped_column(Float, default=1)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), default="global", index=True)
    reference_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    codex_session_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TaskRun(Base, TimestampMixin):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    log_path: Mapped[str | None] = mapped_column(Text)
    codex_session_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String(64), default="system", index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)
