from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaperCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    research_group_id: str | None = None
    research_group_summary: str | None = Field(default=None, max_length=2_000)
    source: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    published_at: date | None = None
    pre_read_summary: str | None = None
    model_short_name: str | None = Field(default=None, max_length=255)
    search_aliases: list[str] | None = None
    model_version: str | None = None
    model_review_required: bool | None = None
    pending_feedback: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    suggested_tags: list[str] | None = None
    post_read_summary: str | None = None
    user_note: str | None = None
    agent_score: float | None = Field(default=None, ge=0, le=1, strict=True)
    model_score: float | None = Field(default=None, ge=0, le=1, strict=True)
    model_confidence: float | None = Field(default=None, ge=0, le=1, strict=True)


class PaperMetadataUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    abstract: str | None = None
    authors: list[dict[str, Any]] | None = None
    first_author: str | None = None
    corresponding_author: str | None = None
    primary_affiliation: str | None = None
    research_group_id: str | None = None
    research_group_summary: str | None = Field(default=None, max_length=2_000)
    source: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    doi: str | None = None
    published_at: date | None = None
    pre_read_summary: str | None = None
    model_short_name: str | None = Field(default=None, max_length=255)
    search_aliases: list[str] | None = None
    metadata_json: dict[str, Any] | None = None
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)
    post_read_summary: str | None = None
    user_note: str | None = None
    user_score: float | None = Field(default=None, ge=0, le=1, strict=True)


class ResearchGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    institution: str = Field(min_length=1, max_length=500)
    homepage_url: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    research_focus: list[str] = Field(default_factory=list)
    representative_contributions: list[dict[str, Any]] = Field(default_factory=list)
    representative_summary: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    verification_status: Literal["candidate", "verified"] = "candidate"


class ResearchGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    institution: str | None = Field(default=None, min_length=1, max_length=500)
    homepage_url: str | None = None
    evidence_json: dict[str, Any] | None = None
    aliases: list[str] | None = None
    research_focus: list[str] | None = None
    representative_contributions: list[dict[str, Any]] | None = None
    representative_summary: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    verification_status: Literal["candidate", "verified", "archived"] | None = None


class IgnorePaper(BaseModel):
    reason: str = Field(min_length=1)
    source_brief_item_id: str | None = None


class StartReading(BaseModel):
    source_brief_item_id: str | None = None


class FinishReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_read_summary: str | None = None
    suggested_score: float | None = Field(default=None, ge=0, le=1, strict=True)
    suggested_tags: list[str] = Field(default_factory=list)
    suggested_nodes: list[SuggestedNode] = Field(default_factory=list)


class SuggestedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    name: str = Field(min_length=1)
    core_question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    domain: str | None = None


class ConfirmedNodeLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    reason: str = Field(min_length=1)
    is_key: bool = False


class NewConfirmedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    core_question: str = Field(min_length=1)
    summary: str | None = None
    domain: str | None = None
    reason: str = Field(min_length=1)
    is_key: bool = False


class ConfirmFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_score: float | None = Field(ge=0, le=1, strict=True)
    create_node: bool = False
    source_brief_item_id: str | None = None
    # Omitted means preserve current metadata tags; an explicit [] clears them.
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)
    node_links: list[ConfirmedNodeLink] = Field(default_factory=list)
    new_nodes: list[NewConfirmedNode] = Field(default_factory=list)
    personal_comment: str | None = None
    post_read_summary: str | None = None

    @model_validator(mode="after")
    def unique_nodes(self) -> Self:
        ids = [link.node_id for link in self.node_links]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node_id")
        names = [node.name.strip().casefold() for node in self.new_nodes]
        if len(names) != len(set(names)):
            raise ValueError("duplicate node name")
        return self


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
    interest_context_digest: str | None = None


class BriefUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    introduction: str | None = Field(default=None, max_length=20_000)


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


class CandidatePoolUpdate(BaseModel):
    discovery_run_id: str | None = None
    paper_ids: list[str] = Field(max_length=600)
    discovery_complete: bool
    notes: str = Field(default="", max_length=4_000)


class MemoryUpdate(BaseModel):
    kind: str | None = None
    title: str | None = None
    content: str | None = None
    source: str | None = None
    active: bool | None = None
    metadata_json: dict[str, Any] | None = None


class SemanticInterestPreference(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=1_000)
    polarity: Literal["positive", "negative", "mixed"]
    confidence: float = Field(ge=0, le=1, strict=True)
    evidence_keys: list[str] = Field(min_length=1, max_length=20)


class InterestProfileUpdate(BaseModel):
    feedback_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content: str = Field(min_length=1, max_length=12_000)
    semantic_preferences: list[SemanticInterestPreference] = Field(
        default_factory=list,
        max_length=50,
    )


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class ConversationCreate(BaseModel):
    kind: str = "global"
    reference_id: str | None = None
    title: str | None = None


class PaperAgentContextSyncRequest(BaseModel):
    conversation_id: str
    query: str | None = Field(default=None, max_length=4_000)


class CodexSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=30)


class MessageCreate(BaseModel):
    content: str
    selected_text: str | None = None
    page_number: int | None = None
    retry_assistant_id: str | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)
    codex: CodexSelection | None = None


class SettingUpdate(BaseModel):
    value: Any


class TaskCreate(BaseModel):
    kind: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
