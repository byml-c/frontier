from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier import cli
from frontier.database import Base, get_session
from frontier.models import Paper, PaperTag, ResearchGroup, Tag


@pytest.fixture
def query_db(client):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        client.app.dependency_overrides[get_session] = lambda: session
        group = ResearchGroup(
            name="机器人研究组",
            normalized_name="机器人研究组",
            institution="清华大学",
            normalized_institution="清华大学",
            aliases=["THU"],
        )
        session.add(group)
        session.flush()
        a = Paper(
            title="World Model for Robot Contact",
            normalized_title="world model for robot contact",
            abstract="Contact-rich tactile policy",
            primary_affiliation="Tsinghua Robotics",
            research_group_id=group.id,
            model_short_name="ForceVLA",
            search_aliases=["触觉策略"],
            published_at=date(2026, 10, 20),
            relevance_score=0.91,
            model_relevance_score=0.6,
            screening_feedback={"interest_level": "high"},
        )
        b = Paper(
            title="World Representation",
            normalized_title="world representation",
            primary_affiliation="MIT",
            published_at=date(2026, 10, 21),
            status="completed",
            relevance_score=0.125,
            model_relevance_score=0.9,
            metadata_json={
                "feedback_provenance": {
                    "active": True,
                    "interest_level": "high",
                    "quality_level": "excellent",
                }
            },
        )
        c = Paper(title="Unrated undated", normalized_title="unrated undated")
        d = Paper(
            title="100% robot_under control",
            normalized_title="100 robot under control",
            published_at=date(2026, 10, 22),
            relevance_score=0.0,
            model_relevance_score=0.0,
            screening_feedback={"interest_level": "ignored"},
        )
        tags = [Tag(name="WAM"), Tag(name="机器人"), Tag(name="Perception")]
        session.add_all([a, b, c, d, *tags])
        session.flush()
        session.add_all(
            [
                PaperTag(paper_id=a.id, tag_id=tags[0].id),
                PaperTag(paper_id=a.id, tag_id=tags[1].id),
                PaperTag(paper_id=b.id, tag_id=tags[2].id),
            ]
        )
        session.commit()
        try:
            yield session, (a, b, c, d)
        finally:
            client.app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def _ids(response):
    assert response.status_code == 200, response.text
    return [p["id"] for p in response.json()["items"]]


def test_library_combines_date_rating_title_affiliation_keyword_and_tag(client, query_db):
    _, (a, *_rest) = query_db
    response = client.get(
        "/api/papers",
        params={
            "published_from": "2026-10-20",
            "published_to": "2026-10-21",
            "title_q": "world contact",
            "affiliation_q": "清华",
            "keyword_q": "触觉策略",
            "tag_q": "WAM 机器人",
            "rating_source": "interest",
            "rating_level": 3,
        },
    )
    assert _ids(response) == [a.id]
    assert response.json()["total"] == 1
    assert _ids(client.get("/api/papers", params={"affiliation_q": "THU"})) == [a.id]
    assert _ids(client.get("/api/papers", params={"affiliation_q": "MIT", "tag_q": "WAM"})) == []


def test_dates_are_inclusive_and_invalid_ranges_are_rejected(client, query_db):
    _, (a, b, *_rest) = query_db
    response = client.get(
        "/api/papers",
        params={
            "published_from": "2026-10-20",
            "published_to": "2026-10-21",
            "sort": "published_asc",
        },
    )
    assert _ids(response) == [a.id, b.id]
    assert (
        client.get(
            "/api/papers",
            params={
                "published_from": "2026-10-22",
                "published_to": "2026-10-20",
            },
        ).status_code
        == 422
    )


def test_rating_sources_buckets_and_nulls_are_independent(client, query_db):
    _, (a, b, c, d) = query_db
    assert set(
        _ids(client.get("/api/papers", params={"rating_source": "interest", "rating_level": 3}))
    ) == {a.id, b.id}
    assert _ids(
        client.get("/api/papers", params={"rating_source": "quality", "rating_level": 4})
    ) == [b.id]
    assert _ids(
        client.get("/api/papers", params={"rating_source": "agent", "rating_level": 1})
    ) == [b.id]
    assert _ids(
        client.get("/api/papers", params={"rating_source": "agent", "rating_level": 0})
    ) == [d.id]
    assert _ids(
        client.get("/api/papers", params={"rating_source": "model", "rating_level": 4})
    ) == [b.id]
    assert _ids(
        client.get("/api/papers", params={"rating_source": "interest", "rating_level": -1})
    ) == [c.id]


def test_sorting_happens_before_pagination_with_missing_ratings_last(client, query_db):
    _, (a, b, c, d) = query_db
    response = client.get(
        "/api/papers",
        params={
            "rating_source": "agent",
            "sort": "rating_desc",
            "limit": 1,
            "offset": 1,
        },
    )
    assert _ids(response) == [b.id]
    assert response.json()["total"] == 4
    assert _ids(
        client.get("/api/papers", params={"rating_source": "agent", "sort": "rating_asc"})
    ) == [d.id, b.id, a.id, c.id]


def test_text_fields_treat_sql_wildcards_as_literal_characters(client, query_db):
    _, (*_rest, d) = query_db
    assert _ids(client.get("/api/papers", params={"title_q": "%"})) == [d.id]
    assert _ids(client.get("/api/papers", params={"title_q": "robot_under"})) == [d.id]


def test_inactive_feedback_uses_the_same_legacy_fallback_as_paper_payload(client, query_db):
    session, (_, b, *_rest) = query_db
    b.metadata_json = {"feedback_provenance": {"active": False, "quality_level": "excellent"}}
    session.commit()
    response = client.get("/api/papers", params={"rating_source": "quality", "rating_level": 2})
    assert _ids(response) == [b.id]
    assert response.json()["items"][0]["quality_level"] == "normal"


def test_library_cli_forwards_multidimensional_query(monkeypatch):
    captured = {}

    def request(method, path, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(cli.client, "request", request)
    cli.paper_list(
        query="robot",
        title_query="world",
        affiliation_query="清华",
        tag_query="WAM",
        published_from="2026-10-01",
        published_to="2026-10-28",
        rating_source="agent",
        rating_level=4,
        sort="rating_desc",
        offset=50,
    )
    assert captured["params"]["title_q"] == "world"
    assert captured["params"]["affiliation_q"] == "清华"
    assert captured["params"]["rating_level"] == 4
    assert captured["params"]["offset"] == 50
