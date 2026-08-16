from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper

COMPLETION_SUMMARY_METADATA_KEY = "completion_summary_structured"
COMPLETION_SUMMARY_FIELDS = (
    "problem",
    "innovation",
    "experiment",
    "limitations",
    "open_questions",
    "user_attitude",
)
PUBLIC_COMPLETION_SUMMARY_FIELDS = COMPLETION_SUMMARY_FIELDS[:-1]

_SECTION_TITLES = {
    "problem": "解决的问题",
    "innovation": "创新内容",
    "experiment": "实验结果",
    "limitations": "局限",
    "open_questions": "未解决问题",
    "user_attitude": "用户态度",
}

_LEGACY_SECTION_ALIASES = {
    "problem": ("研究问题", "解决的问题", "问题定义"),
    "innovation": ("证据与贡献", "核心贡献", "贡献与证据", "创新内容", "贡献", "创新"),
    "experiment": ("实验结果", "实验效果", "实验"),
    "limitations": ("局限", "限制"),
    "open_questions": ("未解决问题", "遗留问题", "开放问题", "后续问题"),
    "user_attitude": ("用户态度", "用户评价", "用户判断", "个人态度"),
}
_OLD_STRUCTURED_FIELDS = {
    "research_question": "problem",
    "evidence_and_contribution": "innovation",
    "experiments": "experiment",
    "limitations": "limitations",
    "user_attitude": "user_attitude",
}
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def completion_summary_structured(
    paper: Paper,
    *,
    include_user_attitude: bool,
) -> dict[str, str | None] | None:
    """Return a reading summary without conflating it with routine paper analysis.

    Private clients receive only a saved structured completion summary. Public
    clients may use non-personal analysis fields as an old-data fallback, but never
    receive user attitude.
    """
    metadata = paper.metadata_json or {}
    stored = metadata.get(COMPLETION_SUMMARY_METADATA_KEY)
    if isinstance(stored, Mapping):
        normalized = {
            _OLD_STRUCTURED_FIELDS.get(str(key), str(key)): value
            for key, value in stored.items()
        }
        fields = (
            COMPLETION_SUMMARY_FIELDS
            if include_user_attitude
            else PUBLIC_COMPLETION_SUMMARY_FIELDS
        )
        return {
            field: value if isinstance(value, str) and value.strip() else None
            for field in fields
            if (value := normalized.get(field)) is None or isinstance(value, str)
        }
    if include_user_attitude:
        return None
    public_fallback = {
        "problem": paper.problem,
        "innovation": paper.innovation,
        "experiment": paper.experiment,
        "limitations": paper.limitations,
        "open_questions": metadata.get("open_questions"),
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
    normalized = {
        _OLD_STRUCTURED_FIELDS.get(str(key), str(key)): value
        for key, value in summary.items()
    }
    normalized.setdefault("open_questions", "历史记录未单独记录未解决问题。")
    canonical = {
        field: normalized[field].strip()
        for field in COMPLETION_SUMMARY_FIELDS
    }
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        COMPLETION_SUMMARY_METADATA_KEY: canonical,
    }
    paper.completion_summary = render_completion_summary(
        canonical, include_user_attitude=True
    )


def _legacy_heading_field(title: str) -> str | None:
    normalized = re.sub(r"[\s*_`：:—–-]+", "", title)
    for field, aliases in _LEGACY_SECTION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return field
    return None


def _legacy_sections(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in content.splitlines():
        match = _MARKDOWN_HEADING.match(line)
        field = _legacy_heading_field(match.group(1)) if match else None
        if field:
            current = field
            sections.setdefault(field, [])
        elif current is not None:
            sections[current].append(line)
    return {
        field: "\n".join(lines).strip()
        for field, lines in sections.items()
        if "\n".join(lines).strip()
    }


def migrate_legacy_completion_summary(paper: Paper) -> bool:
    """Convert one legacy summary to the canonical editable structure, idempotently."""
    metadata = paper.metadata_json or {}
    stored = metadata.get(COMPLETION_SUMMARY_METADATA_KEY)
    if isinstance(stored, Mapping):
        if all(field in stored for field in COMPLETION_SUMMARY_FIELDS):
            return False
        normalized = {
            _OLD_STRUCTURED_FIELDS.get(str(key), str(key)): value
            for key, value in stored.items()
        }
        canonical = {
            "problem": normalized.get("problem") or "历史记录未单独记录解决的问题。",
            "innovation": normalized.get("innovation") or "历史记录未单独记录创新内容。",
            "experiment": normalized.get("experiment") or "历史记录未单独记录实验结果。",
            "limitations": normalized.get("limitations") or "历史记录未单独记录局限。",
            "open_questions": normalized.get("open_questions") or "历史记录未单独记录未解决问题。",
            "user_attitude": normalized.get("user_attitude") or "历史记录中没有可确认的用户态度。",
        }
        paper.metadata_json = {**metadata, COMPLETION_SUMMARY_METADATA_KEY: canonical}
        paper.completion_summary = render_completion_summary(
            canonical, include_user_attitude=True
        )
        return True
    legacy = (paper.completion_summary or "").strip()
    if not legacy:
        return False

    parsed = _legacy_sections(legacy)
    innovation = (
        parsed.get("innovation")
        or paper.innovation
        or (legacy if not parsed else None)
    )
    canonical = {
        "problem": parsed.get("problem")
        or paper.problem
        or "历史记录未单独记录解决的问题。",
        "innovation": innovation
        or "历史记录未单独记录创新内容。",
        "experiment": parsed.get("experiment")
        or paper.experiment
        or "历史记录未单独记录实验结果。",
        "limitations": parsed.get("limitations")
        or paper.limitations
        or "历史记录未单独记录局限。",
        "open_questions": parsed.get("open_questions")
        or "历史记录未单独记录未解决问题。",
        "user_attitude": parsed.get("user_attitude")
        or "历史记录中没有可确认的用户态度。",
    }
    # Keep the original legacy text intact as the lossless source. New edits use
    # save_completion_summary() and will then keep both representations in sync.
    paper.metadata_json = {
        **metadata,
        COMPLETION_SUMMARY_METADATA_KEY: canonical,
    }
    return True


def migrate_legacy_completion_summaries(session: Session) -> int:
    """Upgrade all legacy summaries at startup without rewriting canonical records."""
    migrated = 0
    for paper in session.scalars(
        select(Paper).where(Paper.completion_summary.is_not(None))
    ):
        migrated += int(migrate_legacy_completion_summary(paper))
    if migrated:
        session.commit()
    return migrated


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
