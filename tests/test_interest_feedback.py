from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from frontier import cli
from frontier.agent_runner import build_prompt, run_task
from frontier.database import SessionLocal
from frontier.models import Paper, TaskRun
from frontier.services.interest_evidence import feedback_payload_hash_v2


def _paper(client: TestClient, title: str, *, relevance: float, research: float) -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": title,
            "source": "manual",
            "relevance_score": relevance,
            "research_score": research,
            "summary": f"{title} summary",
        },
    )
    assert response.status_code == 200, response.text
    paper = response.json()["paper"]
    updated = client.patch(
        f"/api/papers/{paper['id']}",
        json={
            "summary": f"{title} 摘要",
            "relevance_score": relevance,
            "research_score": research,
        },
    )
    assert updated.status_code == 200, updated.text
    return updated.json()


def _published_brief(
    client: TestClient,
    papers: list[dict],
    *,
    day_offset: int,
    digest: str | None = None,
) -> dict:
    payload = {
        "brief_date": (date(2032, 1, 1) + timedelta(days=day_offset)).isoformat(),
        "title": f"Interest feedback test {day_offset}",
    }
    if digest:
        payload["interest_context_digest"] = digest
    brief_response = client.post("/api/briefs", json=payload)
    assert brief_response.status_code == 200, brief_response.text
    brief = brief_response.json()
    for rank, paper in enumerate(papers, start=1):
        item = client.post(
            f"/api/briefs/{brief['id']}/items",
            json={
                "paper_id": paper["id"],
                "rank": rank,
                "tier": "featured" if rank == 1 else "related",
            },
        )
        assert item.status_code == 200, item.text
    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200, published.text
    return published.json()


def _summary(attitude: str) -> dict[str, str]:
    return {
        "problem": "解决的问题。",
        "innovation": "创新内容。",
        "experiment": "实验结果。",
        "limitations": "局限。",
        "open_questions": "未解决问题。",
        "user_attitude": attitude,
    }


def _confirm(
    client: TestClient,
    paper_id: str,
    *,
    interested: bool,
    quality: bool,
    tag_id: str,
    attitude: str,
    interest_level: str | None = None,
) -> dict:
    finish = client.post(
        f"/api/papers/{paper_id}/finish",
        json={"completion_summary_structured": _summary(attitude)},
    )
    assert finish.status_code == 200, finish.text
    feedback = {
        "quality": quality,
        "tag_ids": [tag_id],
        "completion_summary_structured": _summary(attitude),
    }
    if interest_level is None:
        feedback["interested"] = interested
    else:
        feedback["interest_level"] = interest_level
    response = client.post(
        f"/api/papers/{paper_id}/confirm-feedback",
        json=feedback,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_confirmed_feedback_is_attributed_and_reused_by_daily_context(
    client: TestClient,
) -> None:
    before = client.get("/api/interests/context").json()["calibration"]
    tag = client.post("/api/tags", json={"name": "闭环正向主题"}).json()
    paper = _paper(client, "Closed loop positive paper", relevance=0.8, research=0.6)
    brief = _published_brief(client, [paper], day_offset=1)
    item = brief["items"][0]

    started = client.post(
        f"/api/papers/{paper['id']}/start-reading",
        json={"source_brief_item_id": item["id"]},
    )
    assert started.status_code == 200, started.text
    confirmed = _confirm(
        client,
        paper["id"],
        interested=True,
        quality=False,
        tag_id=tag["id"],
        attitude="用户明确关注跨平台迁移，但认为实验覆盖不足。",
    )

    provenance = confirmed["metadata_json"]["feedback_provenance"]
    assert provenance["brief_item_id"] == item["id"]
    assert provenance["evidence_key"] == f"paper:{paper['id']}:feedback:1"
    context = client.get("/api/interests/context").json()
    signal = next(item for item in context["recent_feedback"] if item["paper_id"] == paper["id"])
    assert signal["labels"] == {"interest": "positive", "quality": "neutral"}
    assert signal["user_attitude"].startswith("用户明确关注")
    assert signal["attribution"]["resolution"] == "explicit"
    assert signal["attribution"]["brief_item_id"] == item["id"]
    topic = next(
        item
        for item in context["layers"]["confirmed_topic_profile"]["topics"]
        if item["name"] == tag["name"]
    )
    assert topic["positive_interest"] == 1
    assert topic["neutral_quality"] == 1
    assert topic["interest_affinity"] == 0.3333
    calibration = context["calibration"]
    assert (
        calibration["unique_daily_recommendations"]
        == before["unique_daily_recommendations"] + 1
    )
    assert (
        calibration["evaluated_daily_recommendations"]
        == before["evaluated_daily_recommendations"] + 1
    )
    assert calibration["interest_hits"] == before["interest_hits"] + 1
    assert calibration["quality_hits"] == before["quality_hits"]
    before_relevance_count = before["relevance_prediction_sample_count"]
    before_relevance_sum = (before["relevance_brier_score"] or 0) * before_relevance_count
    assert calibration["relevance_prediction_sample_count"] == before_relevance_count + 1
    assert calibration["relevance_brier_score"] == pytest.approx(
        (before_relevance_sum + 0.04) / (before_relevance_count + 1), abs=0.0002
    )
    before_research_count = before["research_prediction_sample_count"]
    before_research_sum = (before["research_brier_score"] or 0) * before_research_count
    assert calibration["research_prediction_sample_count"] == before_research_count + 1
    assert calibration["research_brier_score"] == pytest.approx(
        (before_research_sum + 0.01) / (before_research_count + 1), abs=0.0002
    )

    metadata_update = client.patch(
        f"/api/papers/{paper['id']}",
        json={"metadata_json": {"custom": "preserved alongside feedback provenance"}},
    )
    assert metadata_update.status_code == 200, metadata_update.text
    assert (
        metadata_update.json()["metadata_json"]["feedback_provenance"]["evidence_key"]
        == provenance["evidence_key"]
    )

    before_tasks = client.get("/api/tasks", params={"limit": 200}).json()
    repeated = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": False,
            "tag_ids": [tag["id"]],
            "completion_summary_structured": _summary(
                "用户明确关注跨平台迁移，但认为实验覆盖不足。"
            ),
        },
    )
    assert repeated.status_code == 200, repeated.text
    after_tasks = client.get("/api/tasks", params={"limit": 200}).json()
    assert len(after_tasks) == len(before_tasks)
    conflicting = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False, "tag_ids": [tag["id"]]},
    )
    assert conflicting.status_code == 409


def test_negative_interest_quality_and_ignore_are_calibrated_independently(
    client: TestClient,
) -> None:
    tag = client.post("/api/tags", json={"name": "闭环负向兴趣主题"}).json()
    completed = _paper(client, "Quality but irrelevant paper", relevance=0.9, research=0.9)
    ignored = _paper(client, "Ignored recommendation paper", relevance=0.7, research=0.7)
    brief = _published_brief(client, [completed, ignored], day_offset=2)
    completed_item = next(
        item for item in brief["items"] if item["paper"]["id"] == completed["id"]
    )
    ignored_item = next(
        item for item in brief["items"] if item["paper"]["id"] == ignored["id"]
    )
    assert (
        client.post(
            f"/api/papers/{completed['id']}/start-reading",
            json={"source_brief_item_id": completed_item["id"]},
        ).status_code
        == 200
    )
    _confirm(
        client,
        completed["id"],
        interested=False,
        quality=True,
        tag_id=tag["id"],
        attitude="用户认可论文质量，但明确表示不属于当前优先方向。",
        interest_level="ignored",
    )
    ignored_response = client.post(
        f"/api/papers/{ignored['id']}/ignore",
        json={
            "reason": "方向相邻，但不解决当前问题",
            "source_brief_item_id": ignored_item["id"],
        },
    )
    assert ignored_response.status_code == 200, ignored_response.text

    context = client.get("/api/interests/context").json()
    topic = next(
        item
        for item in context["layers"]["confirmed_topic_profile"]["topics"]
        if item["name"] == tag["name"]
    )
    assert topic["negative_interest"] == 1
    assert topic["positive_quality"] == 1
    calibration = context["calibration"]
    assert calibration["evaluated_daily_recommendations"] >= 2
    assert calibration["ignored_daily_recommendations"] >= 1
    ignored_signal = next(
        item for item in context["recent_feedback"] if item["paper_id"] == ignored["id"]
    )
    assert ignored_signal["labels"] == {
        "interest": "negative_weak",
        "quality": "unknown",
    }
    assert ignored_signal["user_attitude"] == "方向相邻，但不解决当前问题"
    assert ignored_signal["attribution"]["resolution"] == "explicit"


def test_neutral_interest_is_not_negative_feedback(client: TestClient) -> None:
    tag = client.post("/api/tags", json={"name": "闭环中性主题"}).json()
    paper = _paper(client, "Neutral interest paper", relevance=0.5, research=0.8)
    confirmed = _confirm(
        client,
        paper["id"],
        interested=False,
        quality=True,
        tag_id=tag["id"],
        attitude="论文质量不错，但用户目前态度一般。",
        interest_level="neutral",
    )
    assert confirmed["interest_level"] == "neutral"
    assert confirmed["is_interested"] is False
    context = client.get("/api/interests/context").json()
    topic = next(
        item
        for item in context["layers"]["confirmed_topic_profile"]["topics"]
        if item["name"] == tag["name"]
    )
    assert topic["neutral_interest"] == 1
    assert topic["negative_interest"] == 0
    signal = next(
        item for item in context["recent_feedback"] if item["paper_id"] == paper["id"]
    )
    assert signal["labels"]["interest"] == "neutral"


def test_finish_suggests_nodes_and_confirm_atomically_mounts_them(
    client: TestClient,
) -> None:
    existing = client.post(
        "/api/graph/nodes",
        json={"name": "Existing Robotics", "core_question": "已有研究问题是什么？"},
    ).json()
    paper = _paper(client, "Node selection feedback paper", relevance=0.6, research=0.8)
    summary = _summary("用户希望把论文纳入明确的研究问题。")
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "completion_summary_structured": summary,
            "suggested_interest_level": "interested",
            "suggested_nodes": [
                {
                    "node_id": existing["id"],
                    "name": existing["name"],
                    "core_question": existing["core_question"],
                    "reason": "为已有问题提供新证据",
                },
                {
                    "name": "Existing Robotics Policy",
                    "core_question": "如何学习可迁移的机器人策略？",
                    "reason": "论文直接研究策略迁移",
                    "domain": "Robotics",
                },
            ],
        },
    )
    assert finish.status_code == 200, finish.text
    assert len(finish.json()["pending_feedback"]["suggested_nodes"]) == 2
    payload = {
        "interest_level": "interested",
        "quality": True,
        "completion_summary_structured": summary,
        "node_links": [
            {
                "node_id": existing["id"],
                "reason": "为已有问题提供新证据",
            }
        ],
        "new_nodes": [
            {
                "name": "Existing Robotics Policy",
                "core_question": "如何学习可迁移的机器人策略？",
                "reason": "论文直接研究策略迁移",
                "domain": "Robotics",
            }
        ],
    }
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback", json=payload
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert {node["name"] for node in body["nodes"]} == {
        "Existing Robotics",
        "Existing Robotics Policy",
    }
    provenance = body["metadata_json"]["feedback_provenance"]
    assert provenance["interest_level"] == "interested"
    assert set(provenance["selected_node_ids"]) == {node["id"] for node in body["nodes"]}
    tasks = client.get("/api/tasks", params={"limit": 200}).json()
    assert not any(
        task["kind"] == "graph_maintenance"
        and task["payload_json"].get("context", {}).get("paper_id") == paper["id"]
        for task in tasks
    )
    for node in body["nodes"]:
        detail = client.get(f"/api/graph/nodes/{node['id']}").json()
        progress = [item for item in detail["progress"] if item["paper_id"] == paper["id"]]
        assert len(progress) == 1
        assert "创新内容" in progress[0]["content"]

    repeated = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback", json=payload
    )
    assert repeated.status_code == 200, repeated.text
    assert len(repeated.json()["nodes"]) == 2

    renamed = client.patch(
        f"/api/graph/nodes/{existing['id']}",
        json={"name": "Renamed Existing Robotics"},
    )
    assert renamed.status_code == 200, renamed.text
    repeated_after_rename = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback", json=payload
    )
    assert repeated_after_rename.status_code == 200, repeated_after_rename.text


def test_v2_neutral_feedback_cannot_be_replayed_as_ignored(client: TestClient) -> None:
    paper = _paper(client, "Legacy neutral feedback", relevance=0.5, research=0.5)
    summary = _summary("旧版一般反馈不能被误认成忽略。")
    assert client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"completion_summary_structured": summary},
    ).status_code == 200
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": False,
            "quality": True,
            "completion_summary_structured": summary,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    legacy_hash = feedback_payload_hash_v2(
        interested=False,
        quality=True,
        create_node=False,
        tag_names=[],
        user_attitude=summary["user_attitude"],
        personal_comment=None,
        source_brief_item_id=None,
    )
    with SessionLocal() as session:
        stored = session.get(Paper, paper["id"])
        assert stored is not None
        metadata = dict(stored.metadata_json or {})
        provenance = dict(metadata["feedback_provenance"])
        metadata["feedback_provenance"] = {
            **provenance,
            "payload_hash_version": 2,
            "payload_hash": legacy_hash,
        }
        stored.metadata_json = metadata
        session.commit()

    ignored = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interest_level": "ignored",
            "quality": True,
            "completion_summary_structured": summary,
        },
    )
    assert ignored.status_code == 409
    neutral = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interest_level": "neutral",
            "quality": True,
            "completion_summary_structured": summary,
        },
    )
    assert neutral.status_code == 200, neutral.text


def test_invalid_node_selection_rolls_back_feedback_and_new_nodes(
    client: TestClient,
) -> None:
    paper = _paper(client, "Invalid node selection paper", relevance=0.5, research=0.5)
    summary = _summary("无效节点不能产生部分保存。")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/finish",
            json={"completion_summary_structured": summary},
        ).status_code
        == 200
    )
    before_tasks = client.get("/api/tasks", params={"limit": 200}).json()
    response = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interest_level": "neutral",
            "quality": True,
            "node_links": [{"node_id": "missing-node"}],
            "new_nodes": [
                {
                    "name": "Must Not Be Created",
                    "core_question": "这条记录是否会错误落库？",
                }
            ],
        },
    )
    assert response.status_code == 422
    paper_after = client.get(f"/api/papers/{paper['id']}").json()
    assert paper_after["status"] == "feedback_pending"
    assert not paper_after["nodes"]
    nodes = client.get("/api/graph/nodes", params={"q": "Must Not Be Created"}).json()
    assert nodes == []
    assert len(client.get("/api/tasks", params={"limit": 200}).json()) == len(before_tasks)


def test_graph_node_creation_reuses_only_exact_normalized_names(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/graph/nodes",
        json={"name": "  Exact Node Name  ", "core_question": "第一个问题？"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Exact Node Name"
    exact = client.post(
        "/api/graph/nodes",
        json={"name": "exact node name", "core_question": "不应覆盖原问题？"},
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["id"] == first.json()["id"]
    similar = client.post(
        "/api/graph/nodes",
        json={"name": "Exact Node Name Extension", "core_question": "相近但不同的问题？"},
    )
    assert similar.status_code == 200, similar.text
    assert similar.json()["id"] != first.json()["id"]


def test_unattributed_feedback_learns_without_polluting_daily_metrics(
    client: TestClient,
) -> None:
    before = client.get("/api/interests/context").json()["calibration"]
    tag = client.post("/api/tags", json={"name": "无归因学习主题"}).json()
    paper = _paper(client, "Unattributed manually added paper", relevance=0.5, research=0.5)
    _confirm(
        client,
        paper["id"],
        interested=True,
        quality=True,
        tag_id=tag["id"],
        attitude="用户主动添加并认可该主题。",
    )
    context = client.get("/api/interests/context").json()
    signal = next(item for item in context["recent_feedback"] if item["paper_id"] == paper["id"])
    assert signal["attribution"]["kind"] == "unattributed"
    assert (
        context["calibration"]["unique_daily_recommendations"]
        == before["unique_daily_recommendations"]
    )
    assert any(
        item["name"] == tag["name"]
        for item in context["layers"]["confirmed_topic_profile"]["topics"]
    )


def test_explicit_manual_source_wins_over_historical_daily_recommendation(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/papers",
        json={
            "title": "Manual source after daily appearance",
            "source": "manual",
            "summary": "manual source summary",
            "relevance_score": 0.8,
            "research_score": 0.8,
        },
    )
    assert created.status_code == 200, created.text
    paper = created.json()["paper"]
    manual_brief = client.post(
        "/api/briefs",
        json={"brief_date": date(2032, 2, 20).isoformat(), "title": "Manual source"},
    ).json()
    manual_item = client.post(
        f"/api/briefs/{manual_brief['id']}/items",
        json={"paper_id": paper["id"], "rank": 1, "tier": "manual"},
    )
    assert manual_item.status_code == 200, manual_item.text
    manual_item = manual_item.json()
    _published_brief(client, [paper], day_offset=20)
    before_feedback = client.get("/api/interests/context").json()["calibration"]

    started = client.post(
        f"/api/papers/{paper['id']}/start-reading",
        json={"source_brief_item_id": manual_item["id"]},
    )
    assert started.status_code == 200, started.text
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"completion_summary_structured": _summary("用户主动精读，而非响应日报推荐。")},
    )
    assert finish.status_code == 200, finish.text
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": True,
            "completion_summary_structured": _summary(
                "用户主动精读，而非响应日报推荐。"
            ),
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    context = client.get("/api/interests/context").json()
    signal = next(
        item for item in context["recent_feedback"] if item["paper_id"] == paper["id"]
    )
    assert signal["attribution"]["kind"] == "manual"
    assert signal["attribution"]["resolution"] == "explicit"
    assert (
        context["calibration"]["evaluated_daily_recommendations"]
        == before_feedback["evaluated_daily_recommendations"]
    )


def test_ambiguous_legacy_daily_source_is_excluded_from_calibration(
    client: TestClient,
) -> None:
    paper = _paper(client, "Ambiguous legacy source paper", relevance=0.6, research=0.6)
    _published_brief(client, [paper], day_offset=21)
    _published_brief(client, [paper], day_offset=22)
    before_feedback = client.get("/api/interests/context").json()["calibration"]
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"completion_summary_structured": _summary("旧反馈没有唯一日报来源。")},
    )
    assert finish.status_code == 200, finish.text
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": True,
            "completion_summary_structured": _summary("旧反馈没有唯一日报来源。"),
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    context = client.get("/api/interests/context").json()
    signal = next(
        item for item in context["recent_feedback"] if item["paper_id"] == paper["id"]
    )
    assert signal["attribution"]["kind"] == "unattributed"
    assert signal["attribution"]["resolution"] == "ambiguous"
    assert (
        context["calibration"]["evaluated_daily_recommendations"]
        == before_feedback["evaluated_daily_recommendations"]
    )


def test_feedback_source_is_part_of_idempotency_and_is_validated_first(
    client: TestClient,
) -> None:
    paper = _paper(client, "Feedback source identity paper", relevance=0.5, research=0.5)
    first = _published_brief(client, [paper], day_offset=23)["items"][0]
    second = _published_brief(client, [paper], day_offset=24)["items"][0]
    other = _paper(client, "Unrelated source paper", relevance=0.5, research=0.5)
    other_item = _published_brief(client, [other], day_offset=25)["items"][0]
    summary = _summary("相同标签但来源不同，不是同一条反馈。")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/finish",
            json={"completion_summary_structured": summary},
        ).status_code
        == 200
    )
    payload = {
        "interested": True,
        "quality": True,
        "completion_summary_structured": summary,
        "source_brief_item_id": first["id"],
    }
    confirmed = client.post(f"/api/papers/{paper['id']}/confirm-feedback", json=payload)
    assert confirmed.status_code == 200, confirmed.text
    inferred_repeat = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={key: value for key, value in payload.items() if key != "source_brief_item_id"},
    )
    assert inferred_repeat.status_code == 200, inferred_repeat.text
    changed_source = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={**payload, "source_brief_item_id": second["id"]},
    )
    assert changed_source.status_code == 409
    invalid_source = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={**payload, "source_brief_item_id": other_item["id"]},
    )
    assert invalid_source.status_code == 422


def test_completed_manual_item_survives_brief_publication(client: TestClient) -> None:
    paper = _paper(client, "Completed manual item paper", relevance=0.5, research=0.5)
    brief_response = client.post(
        "/api/briefs",
        json={"brief_date": date(2032, 3, 1).isoformat(), "title": "Manual-only brief"},
    )
    assert brief_response.status_code == 200, brief_response.text
    brief = brief_response.json()
    item = client.post(
        f"/api/briefs/{brief['id']}/items",
        json={"paper_id": paper["id"], "rank": 1, "tier": "manual"},
    )
    assert item.status_code == 200, item.text
    source_item_id = item.json()["id"]
    assert (
        client.post(
            f"/api/papers/{paper['id']}/start-reading",
            json={"source_brief_item_id": source_item_id},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/papers/{paper['id']}/finish",
            json={"completion_summary_structured": _summary("手动精读条目仍需保留。")},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/papers/{paper['id']}/confirm-feedback",
            json={
                "interested": True,
                "quality": True,
                "completion_summary_structured": _summary("手动精读条目仍需保留。"),
            },
        ).status_code
        == 200
    )
    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200, published.text
    assert [row["id"] for row in published.json()["items"]] == [source_item_id]


def test_ignored_manual_source_survives_source_less_rereading(client: TestClient) -> None:
    paper = _paper(client, "Ignored then reread manual paper", relevance=0.5, research=0.5)
    brief = client.post(
        "/api/briefs",
        json={"brief_date": date(2032, 3, 2).isoformat(), "title": "Reread source"},
    ).json()
    item = client.post(
        f"/api/briefs/{brief['id']}/items",
        json={"paper_id": paper["id"], "rank": 1, "tier": "manual"},
    ).json()
    ignored = client.post(
        f"/api/papers/{paper['id']}/ignore",
        json={"reason": "稍后再读", "source_brief_item_id": item["id"]},
    )
    assert ignored.status_code == 200, ignored.text
    started = client.post(f"/api/papers/{paper['id']}/start-reading", json={})
    assert started.status_code == 200, started.text
    assert (
        started.json()["metadata_json"]["reading_provenance"]["brief_item_id"]
        == item["id"]
    )
    summary = _summary("重新精读后确认，仍保留原始手动来源。")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/finish",
            json={"completion_summary_structured": summary},
        ).status_code
        == 200
    )
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": True,
            "completion_summary_structured": summary,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    metadata = confirmed.json()["metadata_json"]
    assert metadata["feedback_provenance"]["brief_item_id"] == item["id"]
    assert metadata["ignore_provenance"]["active"] is False


def test_confirmation_requires_a_finished_paper_and_suggestions_do_not_learn(
    client: TestClient,
) -> None:
    paper = _paper(client, "Unconfirmed suggestion paper", relevance=0.5, research=0.5)
    direct = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": True, "quality": True},
    )
    assert direct.status_code == 409
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"suggested_interested": True, "suggested_quality": True},
    )
    assert finish.status_code == 200, finish.text
    context = client.get("/api/interests/context").json()
    assert not any(item["paper_id"] == paper["id"] for item in context["recent_feedback"])


def test_concurrent_identical_confirmation_has_one_effect(client: TestClient) -> None:
    paper = _paper(client, "Concurrent feedback paper", relevance=0.5, research=0.5)
    summary = _summary("并发确认只应形成一次反馈效果。")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/finish",
            json={"completion_summary_structured": summary},
        ).status_code
        == 200
    )
    payload = {
        "interested": True,
        "quality": True,
        "completion_summary_structured": summary,
    }

    def submit() -> int:
        return client.post(
            f"/api/papers/{paper['id']}/confirm-feedback",
            json=payload,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _index: submit(), range(2)))

    assert statuses == [200, 200]
    tasks = client.get("/api/tasks", params={"limit": 200}).json()
    matching = [
        task
        for task in tasks
        if task["kind"] == "graph_maintenance"
        and task["payload_json"].get("context", {}).get("paper_id") == paper["id"]
    ]
    assert matching == []


def test_cancel_retracts_profile_and_removes_paper_backed_graph_state(
    client: TestClient,
) -> None:
    tag = client.post("/api/tags", json={"name": "待撤销主题"}).json()
    paper = _paper(client, "Retracted feedback paper", relevance=0.4, research=0.4)
    confirmed = _confirm(
        client,
        paper["id"],
        interested=True,
        quality=True,
        tag_id=tag["id"],
        attitude="这条反馈稍后撤销。",
    )
    assert confirmed["status"] == "completed"
    node = client.post(
        "/api/graph/nodes",
        json={"name": "撤销测试节点", "core_question": "撤销后是否还有幽灵进展？"},
    ).json()
    assert (
        client.post(
            "/api/graph/paper-links",
            json={"paper_id": paper["id"], "node_id": node["id"], "reason": "测试"},
        ).status_code
        == 200
    )
    first_progress = client.post(
        f"/api/graph/nodes/{node['id']}/progress",
        json={"paper_id": paper["id"], "title": "测试进展", "content": "测试"},
    )
    assert first_progress.status_code == 200
    retried_progress = client.post(
        f"/api/graph/nodes/{node['id']}/progress",
        json={"paper_id": paper["id"], "title": "测试进展", "content": "重试后的内容"},
    )
    assert retried_progress.status_code == 200
    assert retried_progress.json()["id"] == first_progress.json()["id"]

    cancelled = client.post(f"/api/papers/{paper['id']}/cancel-reading")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["metadata_json"]["feedback_provenance"]["active"] is False
    stale_task = client.post(
        "/api/tasks",
        json={
            "kind": "graph_maintenance",
            "payload_json": {
                "context": {
                    "paper_id": paper["id"],
                    "feedback_evidence_key": confirmed["metadata_json"][
                        "feedback_provenance"
                    ]["evidence_key"],
                }
            },
        },
    )
    assert stale_task.status_code == 200, stale_task.text
    run_task(stale_task.json()["id"])
    guarded_task = next(
        task
        for task in client.get("/api/tasks", params={"limit": 200}).json()
        if task["id"] == stale_task.json()["id"]
    )
    assert guarded_task["status"] == "cancelled"
    context = client.get("/api/interests/context").json()
    assert not any(item["paper_id"] == paper["id"] for item in context["recent_feedback"])
    node_after = client.get(f"/api/graph/nodes/{node['id']}").json()
    assert not any(item.get("paper_id") == paper["id"] for item in node_after["progress"])
    assert not any(item["id"] == paper["id"] for item in node_after["papers"])


def test_brief_freezes_interest_context_digest_and_calibration(client: TestClient) -> None:
    context = client.get("/api/interests/context").json()
    paper = _paper(client, "Snapshot context paper", relevance=0.5, research=0.5)
    brief = _published_brief(
        client,
        [paper],
        day_offset=3,
        digest=context["source_digest"],
    )
    snapshot = brief["snapshot_json"]
    assert snapshot["interest_context_digest_used"] == context["source_digest"]
    assert snapshot["interest_context"]["source_digest"] == context["source_digest"]
    assert snapshot["interest_context_stale_at_publish"] is False
    assert "calibration" in snapshot["interest_context"]


def test_reused_draft_brief_refreshes_task_and_context_digest(client: TestClient) -> None:
    first_task = client.post(
        "/api/tasks",
        json={"kind": "daily_brief", "payload_json": {"date": "2032-04-01"}},
    ).json()["id"]
    second_task = client.post(
        "/api/tasks",
        json={"kind": "daily_brief", "payload_json": {"date": "2032-04-01"}},
    ).json()["id"]
    first = client.post(
        "/api/briefs",
        json={
            "brief_date": "2032-04-01",
            "task_id": first_task,
            "interest_context_digest": "first-digest",
        },
    )
    assert first.status_code == 200, first.text
    refreshed = client.post(
        "/api/briefs",
        json={
            "brief_date": "2032-04-01",
            "task_id": second_task,
            "interest_context_digest": "second-digest",
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["task_id"] == second_task
    assert (
        refreshed.json()["snapshot_json"]["interest_context_digest_used"]
        == "second-digest"
    )
    paper = _paper(client, "Ranked draft context paper", relevance=0.5, research=0.5)
    item = client.post(
        f"/api/briefs/{refreshed.json()['id']}/items",
        json={"paper_id": paper["id"], "rank": 1, "tier": "featured"},
    )
    assert item.status_code == 200, item.text
    conflicting = client.post(
        "/api/briefs",
        json={
            "brief_date": "2032-04-01",
            "task_id": first_task,
            "interest_context_digest": "third-digest",
        },
    )
    assert conflicting.status_code == 409


def test_semantic_profile_is_single_cas_cache_with_evidence_references(
    client: TestClient,
) -> None:
    tag = client.post("/api/tags", json={"name": "语义画像主题"}).json()
    paper = _paper(client, "Semantic profile evidence paper", relevance=0.5, research=0.5)
    _confirm(
        client,
        paper["id"],
        interested=True,
        quality=True,
        tag_id=tag["id"],
        attitude="用户长期偏好跨平台、带真实机器人证据的方法。",
    )
    context = client.get("/api/interests/context").json()
    evidence = next(
        item for item in context["recent_feedback"] if item["paper_id"] == paper["id"]
    )
    payload = {
        "feedback_digest": context["feedback_digest"],
        "content": "偏好跨平台且包含真实机器人证据的方法。",
        "semantic_preferences": [
            {
                "key": "cross-platform-real-robot",
                "statement": "偏好跨平台且包含真实机器人证据的方法。",
                "polarity": "positive",
                "confidence": 0.8,
                "evidence_keys": [evidence["evidence_key"]],
            }
        ],
    }
    created = client.put("/api/interests/profile", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["id"] == "derived-interest-profile-v1"
    assert created.json()["changed"] is True
    refreshed = client.get("/api/interests/context").json()
    assert refreshed["feedback_digest"] == context["feedback_digest"]
    assert refreshed["source_digest"] != context["source_digest"]
    assert refreshed["layers"]["semantic_profile"]["content"] == payload["content"]
    repeated = client.put("/api/interests/profile", json=payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["changed"] is False
    assert (
        client.patch(
            "/api/memories/derived-interest-profile-v1",
            json={"content": "绕过证据直接修改"},
        ).status_code
        == 409
    )
    assert client.delete("/api/memories/derived-interest-profile-v1").status_code == 409
    unknown = client.put(
        "/api/interests/profile",
        json={
            **payload,
            "semantic_preferences": [
                {
                    **payload["semantic_preferences"][0],
                    "evidence_keys": ["paper:missing:feedback:1"],
                }
            ],
        },
    )
    assert unknown.status_code == 422
    stale = client.put(
        "/api/interests/profile",
        json={**payload, "feedback_digest": f"sha256:{'0' * 64}"},
    )
    assert stale.status_code == 409

    cancelled = client.post(f"/api/papers/{paper['id']}/cancel-reading")
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = client.get("/api/interests/context").json()
    assert after_cancel["layers"]["semantic_profile"]["stale"] is True
    assert after_cancel["layers"]["semantic_profile"]["content"] is None
    assert "semantic_preferences" not in after_cancel["layers"]["semantic_profile"][
        "metadata"
    ]


def test_interest_context_cli_and_daily_prompt(monkeypatch) -> None:
    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        cli.client,
        "request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {},
    )
    cli.interest_context(recent_limit=7)
    cli.interest_profile_update(data='{"content":"画像"}')
    assert calls == [
        ("GET", "/api/interests/context", {"params": {"recent_limit": 7}}),
        ("PUT", "/api/interests/profile", {"json": {"content": "画像"}}),
    ]
    prompt = build_prompt(
        TaskRun(id="closed-loop-daily", kind="daily_brief", payload_json={"date": "2032-01-01"})
    )
    assert "frontier interest context --recent-limit 30" in prompt
    assert "source_digest" in prompt
    assert "frontier interest profile-update" in prompt
    assert "interest and paper quality as\nindependent axes" in prompt
    assert "A neutral judgment is neither positive nor negative interest" in prompt

    finish_prompt = build_prompt(
        TaskRun(
            id="closed-loop-finish",
            kind="finish_reading",
            payload_json={"paper_id": "paper-1"},
        )
    )
    assert "suggested_interest_level" in finish_prompt
    assert "suggested_nodes" in finish_prompt
    assert "node_id" in finish_prompt
