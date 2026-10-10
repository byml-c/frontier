from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

PAPER_ANALYSIS_PROSE_FIELDS = frozenset(
    {
        "summary",
        "agent_comment",
        "problem",
        "innovation",
        "experiment",
        "limitations",
        "open_questions",
        "recommendation_reason",
        "completion_summary",
        "research_group_summary",
    }
)

COMPLETION_SUMMARY_PROSE_FIELDS = frozenset(
    {
        "problem",
        "innovation",
        "experiment",
        "limitations",
        "open_questions",
        "user_attitude",
        # Accepted only as compatibility input while old queued tasks drain.
        "research_question",
        "evidence_and_contribution",
        "experiments",
    }
)

_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def non_chinese_prose_fields(
    payload: Mapping[str, Any],
    *,
    fields: Iterable[str] = PAPER_ANALYSIS_PROSE_FIELDS,
) -> list[str]:
    """Return non-empty Agent prose fields that contain no Chinese text.

    Technical terms, formulas, and proper names may remain in their original language, so this
    deliberately checks only that each generated prose field contains Chinese rather than imposing
    a brittle character-ratio threshold.
    """
    invalid: list[str] = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip() and not _CJK_CHARACTER.search(value):
            invalid.append(field)
    return sorted(invalid)


def non_chinese_structured_summary_fields(payload: Mapping[str, Any]) -> list[str]:
    summary = payload.get("completion_summary_structured")
    if not isinstance(summary, Mapping):
        return []
    return [
        f"completion_summary_structured.{field}"
        for field in non_chinese_prose_fields(summary, fields=COMPLETION_SUMMARY_PROSE_FIELDS)
    ]
