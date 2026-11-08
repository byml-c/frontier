from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect

from frontier.database import SessionLocal, engine
from frontier.services.interest_ranker import collect_training_examples


def paper(client):
    return client.post("/api/papers", json={"title": str(uuid4())}).json()["paper"]


def test_current_form_and_all_three_sources(client):
    p = paper(client)
    url = f"/api/papers/{p['id']}"
    saved = client.patch(
        url,
        json={
            "pre_read_summary": "# 读前\n\n简介",
            "post_read_summary": "# 读后\n\n结论",
            "agent_score": 0.731,
            "model_score": 0.812,
        },
    )
    assert saved.status_code == 200, saved.text
    rated = client.patch(url + "/metadata", json={"user_score": 0.613})
    assert rated.status_code == 200, rated.text
    assert rated.json()["user_score"] == 0.613
    assert rated.json()["agent_score"] == 0.731
    assert rated.json()["model_score"] == 0.812
    assert (
        not {
            "summary",
            "problem",
            "full_analysis",
            "completion_summary_structured",
            "is_interested",
            "is_quality",
            "research_score",
            "relevance_score",
        }
        & rated.json().keys()
    )
    for obsolete in (
        "summary",
        "problem",
        "interest_level",
        "quality_level",
        "interest_score",
        "quality_score",
        "interested",
        "quality",
    ):
        assert client.patch(url + "/metadata", json={obsolete: 0.5}).status_code == 422
    assert client.patch(url, json={"user_score": 0.9}).status_code == 422
    assert client.patch(url + "/metadata", json={"agent_score": 0.9}).status_code == 422


@pytest.mark.parametrize("value", [-0.01, 1.01, True, False, "0.5"])
def test_score_rejects_invalid_or_ambiguous_input(client, value):
    p = paper(client)
    assert (
        client.patch(f"/api/papers/{p['id']}/metadata", json={"user_score": value}).status_code
        == 422
    )


def test_zero_and_null_are_distinct_in_queries_and_training(client):
    p = paper(client)
    url = f"/api/papers/{p['id']}/metadata"
    assert client.patch(url, json={"user_score": 0}).json()["user_score"] == 0
    result = client.get(
        "/api/papers",
        params={"q": p["title"], "rated": "true", "rating_source": "user", "score_max": 0},
    ).json()
    assert result["total"] == 1
    with SessionLocal() as session:
        assert (
            next(e for e in collect_training_examples(session) if e.paper_id == p["id"]).target == 0
        )
    assert client.patch(url, json={"user_score": None}).json()["user_score"] is None
    assert (
        client.get("/api/papers", params={"q": p["title"], "rated": "false"}).json()["total"] == 1
    )
    with SessionLocal() as session:
        assert not any(e.paper_id == p["id"] for e in collect_training_examples(session))


def test_reading_confirm_and_graph_use_markdown_and_single_score(client):
    p = paper(client)
    url = f"/api/papers/{p['id']}"
    assert client.post(url + "/start-reading", json={}).status_code == 200
    finished = client.post(
        url + "/finish",
        json={"post_read_summary": "## 结论\n\n可验证的证据 [[page:2]]。", "suggested_score": 0.8},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["user_score"] is None
    body = {
        "user_score": 0.694,
        "post_read_summary": "## 读后\n\n用户修订后的结论。",
        "new_nodes": [
            {"name": str(uuid4()), "core_question": "核心问题", "reason": "论文提供证据"}
        ],
    }
    confirmed = client.post(url + "/confirm-feedback", json=body)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    assert len(confirmed.json()["nodes"]) == 1
    assert client.post(url + "/confirm-feedback", json=body).status_code == 200


def test_brief_reads_current_paper_not_a_snapshot(client):
    p = paper(client)
    url = f"/api/papers/{p['id']}"
    client.patch(url, json={"pre_read_summary": "原简介", "agent_score": 0.623})
    brief = client.post("/api/briefs", json={"brief_date": "2050-01-01"}).json()
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items", json={"paper_id": p["id"], "rank": 1}
        ).status_code
        == 200
    )
    assert client.post(f"/api/briefs/{brief['id']}/publish").status_code == 200
    client.patch(
        url + "/metadata",
        json={
            "pre_read_summary": "修改后的简介",
            "post_read_summary": "当前结论",
            "user_score": 0.417,
        },
    )
    current = client.get(f"/api/briefs/{brief['id']}").json()["items"][0]["paper"]
    assert current["pre_read_summary"] == "修改后的简介"
    assert current["post_read_summary"] == "当前结论"
    assert current["user_score"] == 0.417
    assert current["agent_score"] == 0.623
    assert "snapshot_json" not in current


def test_audio_and_legacy_tables_are_absent(client):
    tables = set(inspect(engine).get_table_names())
    assert not {"paper_narrations", "podcast_episodes", "daily_selections"} & tables
    fields = {c["name"] for c in inspect(engine).get_columns("papers")}
    assert {
        "pre_read_summary",
        "post_read_summary",
        "user_score",
        "agent_score",
        "model_score",
    } <= fields
    assert not {"summary", "problem", "full_analysis", "is_quality", "is_interested"} & fields
    assert "snapshot_json" not in {c["name"] for c in inspect(engine).get_columns("brief_items")}
    assert client.get("/api/briefs/any/podcast").status_code == 404
    assert client.get("/api/papers/any/narration").status_code == 404
