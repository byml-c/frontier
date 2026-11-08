from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import Text, cast, or_, select
from sqlalchemy.sql.elements import ColumnElement

from ..models import Paper, PaperTag, ResearchGroup, Tag

RatingSource = Literal["user", "agent", "model"]
PaperSort = Literal[
    "default", "rating_desc", "rating_asc", "published_desc", "published_asc", "title"
]


def rating_expression(source: RatingSource) -> ColumnElement:
    return {"user": Paper.user_score, "agent": Paper.agent_score, "model": Paper.model_score}[
        source
    ]


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
                        Paper.post_read_summary,
                        Paper.pre_read_summary,
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
