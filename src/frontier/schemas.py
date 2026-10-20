from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    research_group_id: str | None = None
    research_group_summary: str | None = Field(default=None, max_length=2_000)
    source: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    arxiv_version: int | None = None
    doi: str | None = None
    published_at: date | None = None
    catalog_summary: str | None = None
    summary: str | None = None
    agent_comment: str | None = None
    problem: str | None = None
    innovation: str | None = None
    experiment: str | None = None
    limitations: str | None = None
    open_questions: str | None = None
    full_analysis: dict[str, Any] | None = None
    research_score: float | None = Field(default=None, ge=0, le=1)
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    topic_relevance_score: float | None = Field(default=None, ge=0, le=1)
    author_influence_score: float | None = Field(default=None, ge=0, le=1)
    recommendation_reason: str | None = None
    completion_summary: str | None = None
    pending_feedback: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    suggested_tags: list[str] | None = None


class CompletionSummaryStructured(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str = Field(min_length=1)
    innovation: str = Field(min_length=1)
    experiment: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    open_questions: str = Field(min_length=1)
    user_attitude: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        aliases = {
            "research_question": "problem",
            "evidence_and_contribution": "innovation",
            "experiments": "experiment",
        }
        for legacy, canonical in aliases.items():
            if legacy in normalized:
                normalized.setdefault(canonical, normalized[legacy])
                normalized.pop(legacy)
        if "open_questions" not in normalized and any(
            key in normalized for key in ("problem", "innovation", "experiment")
        ):
            normalized["open_questions"] = "历史记录未单独记录未解决问题。"
        return normalized

    @field_validator("*")
    @classmethod
    def require_non_blank_section(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("completion summary sections cannot be blank")
        return value


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
    catalog_summary: str | None = None
    summary: str | None = None
    agent_comment: str | None = None
    problem: str | None = None
    innovation: str | None = None
    experiment: str | None = None
    limitations: str | None = None
    open_questions: str | None = None
    recommendation_reason: str | None = None
    interest_level: Literal["ignored", "low", "neutral", "high", "interested"] | None = None
    quality_level: Literal["poor", "below_average", "normal", "good", "excellent"] | None = None
    screening_feedback: str | None = Field(default=None, max_length=2000)
    agent_score_confirmed: bool | None = None
    agent_quality_confirmed: bool | None = None
    agent_interest_confirmed: bool | None = None
    completion_summary_structured: CompletionSummaryStructured | None = None
    metadata_json: dict[str, Any] | None = None
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)


class ResearchGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    institution: str = Field(min_length=1, max_length=500)
    homepage_url: str | None = None
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
    aliases: list[str] | None = None
    research_focus: list[str] | None = None
    representative_contributions: list[dict[str, Any]] | None = None
    representative_summary: str | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    verification_status: Literal["candidate", "verified", "archived"] | None = None


class PaperMetadataEnrichmentComplete(BaseModel):
    status: Literal["completed", "failed"] = "completed"
    note: str | None = Field(default=None, max_length=2_000)


class IgnorePaper(BaseModel):
    reason: str = Field(min_length=1)
    source_brief_item_id: str | None = None


class StartReading(BaseModel):
    source_brief_item_id: str | None = None


class FinishReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    completion_summary_structured: CompletionSummaryStructured | None = None
    suggested_interest_level: Literal["ignored", "low", "neutral", "high", "interested"] | None = None
    suggested_interested: bool | None = None
    suggested_quality_level: Literal["poor", "below_average", "normal", "good", "excellent"] | None = None
    suggested_quality: bool | None = None
    suggested_create_node: bool | None = None
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
    interest_level: Literal["ignored", "low", "neutral", "high", "interested"] | None = None
    # Backward-compatible input for existing CLI/API clients.
    interested: bool | None = None
    quality_level: Literal["poor", "below_average", "normal", "good", "excellent"] | None = None
    # Backward-compatible input for existing CLI/API clients.
    quality: bool | None = None
    create_node: bool = False
    source_brief_item_id: str | None = None
    # Omitted means preserve current metadata tags; an explicit [] clears them.
    tag_ids: list[str] | None = None
    new_tags: list[str] = Field(default_factory=list)
    node_links: list[ConfirmedNodeLink] = Field(default_factory=list)
    new_nodes: list[NewConfirmedNode] = Field(default_factory=list)
    personal_comment: str | None = None
    completion_summary_structured: CompletionSummaryStructured | None = None

    @model_validator(mode="after")
    def require_consistent_interest(self) -> Self:
        if self.interest_level is None and self.interested is None:
            raise ValueError("interest_level is required")
        if (
            self.interest_level is not None
            and self.interested is not None
            and self.interested != (self.interest_level in {"high", "interested"})
        ):
            raise ValueError("interested conflicts with interest_level")
        if self.quality_level is None and self.quality is None:
            raise ValueError("quality_level is required")
        if (
            self.quality_level is not None
            and self.quality is not None
            and self.quality != (self.quality_level in {"good", "excellent"})
        ):
            raise ValueError("quality conflicts with quality_level")
        node_ids = [item.node_id for item in self.node_links]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_links contains duplicate node_id values")
        node_names = [item.name.strip().casefold() for item in self.new_nodes]
        if len(node_names) != len(set(node_names)):
            raise ValueError("new_nodes contains duplicate names")
        return self

    @property
    def resolved_interest_level(self) -> Literal["ignored", "low", "neutral", "high", "interested"]:
        if self.interest_level is not None:
            return self.interest_level
        return "interested" if self.interested else "neutral"

    @property
    def resolved_quality_level(self) -> Literal["poor", "below_average", "normal", "good", "excellent"]:
        if self.quality_level is not None:
            return self.quality_level
        return "excellent" if self.quality else "normal"


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


class BriefItemCreate(BaseModel):
    paper_id: str
    rank: int = Field(ge=1)
    tier: str = "related"


NarrationStatus = Literal["draft", "generating", "ready", "failed"]
PodcastStatus = Literal["draft", "generating", "scripted", "rendering", "ready", "failed"]


class PaperNarrationCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    language: str = Field(default="zh-CN", min_length=2, max_length=35)
    status: NarrationStatus = "ready"
    script_markdown: str | None = None
    speech_text: str | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    pronunciations: list[dict[str, Any]] = Field(default_factory=list)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_pdf_version: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=36)
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.status == "ready" and (
            not (self.script_markdown or "").strip() or not (self.speech_text or "").strip()
        ):
            raise ValueError("ready narration requires script_markdown and speech_text")
        if self.status == "failed" and not (self.error or "").strip():
            raise ValueError("failed narration requires error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("error is only valid for failed narration")
        return self


class PodcastEpisodeCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    language: str = Field(default="zh-CN", min_length=2, max_length=35)
    status: PodcastStatus = "scripted"
    title: str = Field(min_length=1, max_length=255)
    script_text: str | None = None
    speech_text: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)
    voice: str | None = Field(default=None, max_length=100)
    audio_path: str | None = None
    audio_content_hash: str | None = Field(default=None, pattern=r"^(?:sha256:)?[0-9a-f]{64}$")
    duration_ms: int | None = Field(default=None, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    task_id: str | None = Field(default=None, max_length=36)
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.status in {"scripted", "rendering", "ready"} and (
            not (self.script_text or "").strip() or not (self.speech_text or "").strip()
        ):
            raise ValueError(f"{self.status} episode requires script_text and speech_text")
        if self.status == "ready" and (
            not self.audio_path
            or not self.audio_content_hash
            or self.duration_ms is None
            or self.size_bytes is None
        ):
            raise ValueError(
                "ready episode requires audio_path, audio_content_hash, duration_ms and size_bytes"
            )
        if self.status == "failed" and not (self.error or "").strip():
            raise ValueError("failed episode requires error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("error is only valid for failed episode")
        return self


class PodcastEpisodeStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    status: PodcastStatus
    manifest: dict[str, Any] | None = None
    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)
    voice: str | None = Field(default=None, max_length=100)
    audio_path: str | None = None
    audio_content_hash: str | None = Field(default=None, pattern=r"^(?:sha256:)?[0-9a-f]{64}$")
    duration_ms: int | None = Field(default=None, ge=0)
    size_bytes: int | None = Field(default=None, ge=0)
    task_id: str | None = Field(default=None, max_length=36)
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.status == "failed" and not (self.error or "").strip():
            raise ValueError("failed episode requires error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("error is only valid for failed episode")
        return self


class MemoryCreate(BaseModel):
    kind: str
    title: str
    content: str
    source: str | None = None
    active: bool = True
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CandidatePoolUpdate(BaseModel):
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
    confidence: float = Field(ge=0, le=1)
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


class MessageCreate(BaseModel):
    content: str
    selected_text: str | None = None
    page_number: int | None = None
    retry_assistant_id: str | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)


class SettingUpdate(BaseModel):
    value: Any


class TaskCreate(BaseModel):
    kind: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
