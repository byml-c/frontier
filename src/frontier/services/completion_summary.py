from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import Paper

COMPLETION_SUMMARY_METADATA_KEY = "completion_summary_structured"
COMPLETION_SUMMARY_FIELDS = (
    "research_question",
    "evidence_and_contribution",
    "experiments",
    "limitations",
    "user_attitude",
)
PUBLIC_COMPLETION_SUMMARY_FIELDS = COMPLETION_SUMMARY_FIELDS[:-1]

_SECTION_TITLES = {
    "research_question": "研究问题",
    "evidence_and_contribution": "证据与贡献",
    "experiments": "实验",
    "limitations": "局限",
    "user_attitude": "用户态度",
}


def completion_summary_structured(
    paper: Paper,
    *,
    include_user_attitude: bool,
) -> dict[str, str | None] | None:
    """Return the canonical reading summary, with a legacy-string fallback.

    User attitude is deliberately opt-in so public serializers cannot expose it by
    accidentally dumping all persisted metadata.
    """
    metadata = paper.metadata_json or {}
    stored = metadata.get(COMPLETION_SUMMARY_METADATA_KEY)
    if isinstance(stored, Mapping):
        fields = (
            COMPLETION_SUMMARY_FIELDS
            if include_user_attitude
            else PUBLIC_COMPLETION_SUMMARY_FIELDS
        )
        return {
            field: value if isinstance(value, str) and value.strip() else None
            for field in fields
            if (value := stored.get(field)) is None or isinstance(value, str)
        }
    if include_user_attitude and paper.completion_summary:
        return {"overview": paper.completion_summary}
    public_fallback = {
        "research_question": paper.problem,
        "evidence_and_contribution": paper.innovation,
        "experiments": paper.experiment,
        "limitations": paper.limitations,
    }
    if any(public_fallback.values()):
        return public_fallback
    return None


def render_completion_summary(
    summary: Mapping[str, Any],
    *,
    include_user_attitude: bool,
) -> str | None:
    """Render structured data for clients that still consume the legacy string."""
    fields = (
        COMPLETION_SUMMARY_FIELDS
        if include_user_attitude
        else PUBLIC_COMPLETION_SUMMARY_FIELDS
    )
    sections = [
        f"### {_SECTION_TITLES[field]}\n\n{value.strip()}"
        for field in fields
        if isinstance((value := summary.get(field)), str) and value.strip()
    ]
    return "\n\n".join(sections) or None


def save_completion_summary(paper: Paper, summary: Mapping[str, str]) -> None:
    """Persist one canonical structured summary and keep legacy readers in sync."""
    canonical = {
        field: summary[field].strip()
        for field in COMPLETION_SUMMARY_FIELDS
    }
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        COMPLETION_SUMMARY_METADATA_KEY: canonical,
    }
    paper.completion_summary = render_completion_summary(
        canonical, include_user_attitude=True
    )


def brief_completion_summary(paper: Paper) -> str | None:
    """Keep old brief clients working while stripping new structured attitudes.

    Brief routes are private. Legacy strings predate the privacy boundary and cannot be
    decomposed reliably, so they remain available there; public daily routes never call this.
    """
    structured = completion_summary_structured(paper, include_user_attitude=False)
    metadata = paper.metadata_json or {}
    if isinstance(metadata.get(COMPLETION_SUMMARY_METADATA_KEY), Mapping) and structured:
        return render_completion_summary(structured, include_user_attitude=False)
    return paper.completion_summary
