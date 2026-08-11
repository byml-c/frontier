from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaperCreate(BaseModel):
    title: str
    abstract: str | None = None
    authors: list[dict[str, Any]] = Field(default_factory=list)
    first_author: str | None = None
    corresponding_author: str | None = None
    primary_affiliation: str | None = None
    source: str = "manual"
    source_url: str | None = None
    pdf_url: str | None = None
    arxiv_id: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    published_at: date | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class PaperUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    abstract: str | None = None
    authors: list[dict[str, Any]] | None = None
    first_author: str | None = None
    corresponding_author: str | None = None
    primary_affiliation: str | None = None
    source: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    published_at: date | None = None
    summary: str | None = None
    agent_comment: str | None = None
    problem: str | None = None
    innovation: str | None = None
    experiment: str | None = None
    limitations: str | None = None
    full_analysis: dict[str, Any] | None = None
    research_score: float | None = None
    relevance_score: float | None = None
    recommendation_reason: str | None = None
    completion_summary: str | None = None
    pending_feedback: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    suggested_tags: list[str] | None = None


class PaperMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    abstract: str | None = None
    authors: list[dict[str, Any]] | None = None
    first_author: str | None = None
    corresponding_author: str | None = None
    primary_affiliation: str | None = None
    source: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    doi: str | None = None
    published_at: date | None = None
    summary: str | None = None
    agent_comment: str | None = None
    problem: str | None = None
    innovation: str | None = None
    experiment: str | None = None
    limitations: str | None = None
    recommendation_reason: str | None = None
    research_score: float | None = Field(default=None, ge=0, le=1)
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    metadata_json: dict[str, Any] | None = None
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)


class IgnorePaper(BaseModel):
    reason: str = Field(min_length=1)


class FinishReading(BaseModel):
    summary: str | None = None
    suggested_interested: bool | None = None
    suggested_quality: bool | None = None
    suggested_create_node: bool | None = None
    suggested_tags: list[str] = Field(default_factory=list)


class ConfirmFeedback(BaseModel):
    interested: bool
    quality: bool
    create_node: bool = False
    # Omitted means preserve current metadata tags; an explicit [] clears them.
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)
    personal_comment: str | None = None


class AnnotationCreate(BaseModel):
    kind: str
    page_number: int = Field(ge=1)
    selected_text: str | None = None
    content: str | None = None
    position_json: dict[str, Any] = Field(default_factory=dict)
    color: str | None = None


class ReadingProgressUpdate(BaseModel):
    page_number: int = Field(ge=1)
    scroll_offset: float = 0
    zoom: float = Field(default=1, gt=0)


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    page_number: int | None = Field(default=None, ge=1)
    target_language: str = "简体中文"
    position_json: dict[str, Any] = Field(default_factory=dict)
    page_context: str = Field(default="", max_length=4000)


class NodeCreate(BaseModel):
    name: str
    core_question: str
    summary: str | None = None
    solved: str | None = None
    open_questions: str | None = None
    domain: str | None = None
    color: str | None = None


class NodeUpdate(BaseModel):
    name: str | None = None
    core_question: str | None = None
    summary: str | None = None
    solved: str | None = None
    open_questions: str | None = None
    domain: str | None = None
    color: str | None = None
    layout_x: float | None = None
    layout_y: float | None = None
    is_active: bool | None = None


class EdgeCreate(BaseModel):
    source_id: str
    target_id: str


class PaperNodeCreate(BaseModel):
    paper_id: str
    node_id: str
    reason: str | None = None
    is_key: bool = False


class ProgressCreate(BaseModel):
    title: str
    content: str
    paper_id: str | None = None
    happened_at: date | None = None


class IdeaCreate(BaseModel):
    content: str
    status: str = "to_explore"
    source_type: str | None = None
    source_id: str | None = None
    note: str | None = None


class IdeaUpdate(BaseModel):
    content: str | None = None
    status: str | None = None
    note: str | None = None


class BriefCreate(BaseModel):
    brief_date: date
    title: str | None = None
    introduction: str | None = None
    task_id: str | None = None


class BriefItemCreate(BaseModel):
    paper_id: str
    rank: int = Field(ge=1)
    tier: str = "related"


class MemoryCreate(BaseModel):
    kind: str
    title: str
    content: str
    source: str | None = None
    active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    kind: str | None = None
    title: str | None = None
    content: str | None = None
    source: str | None = None
    active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class ConversationCreate(BaseModel):
    kind: str = "global"
    reference_id: str | None = None
    title: str | None = None


class MessageCreate(BaseModel):
    content: str
    selected_text: str | None = None
    page_number: int | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)


class SettingUpdate(BaseModel):
    value: Any


class TaskCreate(BaseModel):
    kind: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
