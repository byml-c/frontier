from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from ..models import Paper, PaperTag, ResearchGroup, Tag

RatingSource = Literal["interest", "quality", "agent", "model"]
PaperSort = Literal[
    "default", "rating_desc", "rating_asc", "published_desc", "published_asc", "title"
]
INTEREST_VALUES = {"ignored": 0.0, "low": 0.25, "neutral": 0.5, "high": 0.75, "interested": 1.0}
QUALITY_VALUES = {"poor": 0.0, "below_average": 0.25, "normal": 0.5, "good": 0.75, "excellent": 1.0}


def rating_expression(source: RatingSource) -> ColumnElement:
    if source == "agent":
        return Paper.relevance_score
    if source == "model":
        return Paper.model_relevance_score
    field = "interest_level" if source == "interest" else "quality_level"
    values = INTEREST_VALUES if source == "interest" else QUALITY_VALUES
    confirmed = case(values, value=Paper.metadata_json["feedback_provenance"][field].as_string())
    confirmed = case(
        (Paper.metadata_json["feedback_provenance"]["active"].as_boolean().is_(False), None),
        else_=confirmed,
    )
    legacy = (
        case((Paper.is_interested.is_(True), 1.0), else_=0.5)
        if source == "interest"
        else case((Paper.is_quality.is_(True), 1.0), else_=0.5)
    )
    screened = case(values, value=Paper.screening_feedback[field].as_string())
    branches = []
    if source == "interest":
        branches.append((Paper.status == "ignored", 0.0))
    branches.append((Paper.status == "completed", func.coalesce(confirmed, legacy)))
    return case(*branches, else_=screened)


def rating_level_expression(score: ColumnElement) -> ColumnElement:
    return case(
        (score.is_(None), None),
        (score < 0.125, 0),
        (score < 0.375, 1),
        (score < 0.625, 2),
        (score < 0.875, 3),
        else_=4,
    )


def text_terms(value: str | None) -> list[str]:
    return list(dict.fromkeys((value or "").split()))[:20]


def literal_match(column: ColumnElement, term: str) -> ColumnElement:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def json_match(column: ColumnElement, term: str) -> ColumnElement:
    return or_(
        literal_match(cast(column, Text), term),
        literal_match(cast(column, Text), json.dumps(term, ensure_ascii=True)[1:-1]),
    )


def dimension_filters(
    *,
    title: str | None,
    affiliation: str | None,
    keyword: str | None,
    tag: str | None,
) -> list[ColumnElement]:
    filters = [literal_match(Paper.title, term) for term in text_terms(title)]
    for term in text_terms(affiliation):
        group = (
            select(ResearchGroup.id)
            .where(
                ResearchGroup.id == Paper.research_group_id,
                or_(
                    literal_match(ResearchGroup.name, term),
                    literal_match(ResearchGroup.institution, term),
                    json_match(ResearchGroup.aliases, term),
                ),
            )
            .exists()
        )
        filters.append(or_(literal_match(Paper.primary_affiliation, term), group))
    for term in text_terms(keyword):
        filters.append(
            or_(
                *[
                    literal_match(column, term)
                    for column in (
                        Paper.title,
                        Paper.abstract,
                        Paper.summary,
                        Paper.catalog_summary,
                        Paper.agent_comment,
                        Paper.recommendation_reason,
                        Paper.model_short_name,
                    )
                ],
                json_match(Paper.search_aliases, term),
                json_match(Paper.metadata_json["keywords"], term),
            )
        )
    for term in text_terms(tag):
        filters.append(
            select(PaperTag.paper_id)
            .join(Tag, Tag.id == PaperTag.tag_id)
            .where(
                PaperTag.paper_id == Paper.id,
                literal_match(Tag.name, term),
            )
            .exists()
        )
    return filters
