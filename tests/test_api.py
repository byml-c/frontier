from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from frontier.agent_runner import _parse_citations
from frontier.api.papers import _translation_key
from frontier.database import SessionLocal
from frontier.models import Paper
from frontier.schemas import PaperCreate, TranslationRequest
from frontier.services.document_reader import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentReaderUnavailable,
    _document_payload_is_current,
    _estimate_conversion_seconds,
    _processing_status_is_live,
    convert_with_docling,
)
from frontier.services.ingest import parse_arxiv_abs_html


def create_paper(client: TestClient, title: str = "Tactile VLA for Fine Manipulation") -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": title,
            "abstract": "We combine tactile sensing with a vision-language-action policy.",
            "authors": [{"name": "Ada Researcher"}],
            "first_author": "Ada Researcher",
            "source": "manual",
            "published_at": "2026-08-01",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def test_stream_close_after_metadata_marks_task_interrupted(client: TestClient) -> None:
    from frontier.api.system import stream_task
    from frontier.database import SessionLocal
    from frontier.models import TaskRun
    from frontier.schemas import TaskCreate

    with SessionLocal() as session:
        response = stream_task(
            TaskCreate(kind="translate", payload_json={"text": "hello"}),
            session,
        )

    async def close_after_metadata() -> None:
        iterator = response.body_iterator
        first = await anext(iterator)
        assert '"type": "meta"' in first
        await iterator.aclose()

    asyncio.run(close_after_metadata())
    with SessionLocal() as session:
        task = session.scalar(select(TaskRun).order_by(TaskRun.created_at.desc()).limit(1))
        assert task is not None
        assert task.status == "failed"
        assert task.result_json["stream_status"] == "interrupted"


def test_conversation_close_after_metadata_releases_active_task(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations
    from frontier.database import SessionLocal
    from frontier.schemas import MessageCreate

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)
    conversation = client.post(
        "/api/conversations",
        json={"kind": "global", "title": "Early disconnect"},
    ).json()
    with SessionLocal() as session:
        response = conversations.stream_message(
            conversation["id"],
            MessageCreate(content="请分析"),
            session,
        )

    async def close_after_metadata() -> None:
        iterator = response.body_iterator
        first = await anext(iterator)
        assert '"type": "meta"' in first
        await iterator.aclose()

    asyncio.run(close_after_metadata())
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["active_task"] is None
    assistant = detail["messages"][-1]
    assert assistant["context_json"]["stream_status"] == "interrupted"
    task = client.get(f"/api/tasks/{assistant['context_json']['task_id']}").json()
    assert task["status"] == "failed"


def test_translation_close_after_metadata_marks_task_interrupted(client: TestClient) -> None:
    from frontier.api.papers import stream_translation
    from frontier.database import SessionLocal
    from frontier.models import TaskRun

    paper = create_paper(client, "Translation disconnect")
    with SessionLocal() as session:
        response = stream_translation(
            paper["id"],
            TranslationRequest(text="disconnect-before-provider"),
            session,
        )

    async def close_after_metadata() -> None:
        iterator = response.body_iterator
        first = await anext(iterator)
        assert '"type": "meta"' in first
        await iterator.aclose()

    asyncio.run(close_after_metadata())
    with SessionLocal() as session:
        task = session.scalar(select(TaskRun).order_by(TaskRun.created_at.desc()).limit(1))
        assert task is not None
        assert task.status == "failed"
        assert task.result_json["stream_status"] == "interrupted"


def test_paper_search_covers_saved_metadata_and_abstract(client: TestClient) -> None:
    response = client.post(
        "/api/papers",
        json={
            "title": "π0.5: Locomotion with Predictive Representations",
            "abstract": "The controller transfers across unseen terrains.",
            "authors": [{"name": "Primary Author"}, {"name": "Hidden Collaborator"}],
            "first_author": "Primary Author",
            "corresponding_author": "Senior Researcher",
            "primary_affiliation": "Frontier Robotics Institute",
            "source": "arxiv",
            "arxiv_id": "2608.01234",
            "doi": "10.1000/pi05",
            "published_at": "2026-08-01",
        },
    )
    assert response.status_code == 200, response.text
    paper_id = response.json()["paper"]["id"]

    for query in (
        "PI05",
        "Hidden Collaborator",
        "Senior Researcher",
        "Frontier Robotics",
        "arXiv:2608.01234",
        "10.1000/pi05",
    ):
        result = client.get("/api/papers", params={"q": query}).json()
        assert paper_id in {item["id"] for item in result["items"]}, query


def test_arxiv_discovery_query_is_searchable_only_after_ingest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "opaque-pi05-discovery-alias"
    payload = PaperCreate(
        title="A Paper Whose Metadata Does Not Contain the Query",
        abstract="A generic abstract.",
        authors=[{"name": "Example Author"}],
        source="arxiv",
        arxiv_id="2608.09991",
    )
    monkeypatch.setattr("frontier.api.system.search_arxiv", lambda *_args, **_kwargs: [payload])

    preview = client.post(
        "/api/sources/arxiv/search",
        params={"query": query, "max_results": 10, "ingest": "false"},
    )
    assert preview.status_code == 200
    assert client.get("/api/papers", params={"q": query}).json()["total"] == 0

    ingested = client.post(
        "/api/sources/arxiv/search",
        params={"query": query, "max_results": 10, "ingest": "true"},
    )
    assert ingested.status_code == 200
    paper_id = ingested.json()["paper_ids"][0]
    result = client.get("/api/papers", params={"q": query}).json()
    assert paper_id in {item["id"] for item in result["items"]}


def test_parse_citations_supports_page_ranges() -> None:
    citations = _parse_citations(
        "范围 [[page:1–2]]、半角 [[p:3-5]] 与自定义名称 [[page:8—9|实验部分]]。",
        "paper-1",
    )

    assert citations == [
        {
            "paper_id": "paper-1",
            "page_number": 1,
            "end_page_number": 2,
            "label": "第 1–2 页",
            "marker": "[[page:1–2]]",
        },
        {
            "paper_id": "paper-1",
            "page_number": 3,
            "end_page_number": 5,
            "label": "第 3–5 页",
            "marker": "[[p:3-5]]",
        },
        {
            "paper_id": "paper-1",
            "page_number": 8,
            "end_page_number": 9,
            "label": "实验部分",
            "marker": "[[page:8—9|实验部分]]",
        },
    ]


def test_parse_citations_supports_separate_page_lists() -> None:
    citations = _parse_citations("两处证据 [[page:2, 12]]，另一处 [[p:4，7]]。", "paper-1")

    assert [(item["page_number"], item["label"]) for item in citations] == [
        (2, "第 2 页"),
        (12, "第 12 页"),
        (4, "第 4 页"),
        (7, "第 7 页"),
    ]
    assert citations[0]["marker"] == citations[1]["marker"] == "[[page:2, 12]]"


def test_translation_cache_key_is_stable_across_selection_geometry() -> None:
    first = TranslationRequest(
        text="action-grounded latent",
        page_number=2,
        position_json={"rects": [[0.1, 0.2, 0.3, 0.04]]},
    )
    second = TranslationRequest(
        text="action-grounded   latent",
        page_number=2,
        position_json={"rects": [[0.11, 0.21, 0.29, 0.05]]},
    )
    assert _translation_key("paper-id", first) == _translation_key("paper-id", second)


def test_arxiv_abs_metadata_parses_all_authors_without_atom_api() -> None:
    payload = parse_arxiv_abs_html(
        """
        <html><head>
          <meta property="og:url" content="https://arxiv.org/abs/2608.10232v1" />
          <meta name="citation_title" content="FACT: Failure-Aware Causal Training" />
          <meta name="citation_author" content="Peng, Quanquan" />
          <meta name="citation_author" content="Liang, Yutong" />
          <meta name="citation_date" content="2026/08/10" />
          <meta name="citation_pdf_url" content="https://arxiv.org/pdf/2608.10232" />
          <meta name="citation_arxiv_id" content="2608.10232" />
          <meta name="citation_abstract" content="Failure-aware world-action training." />
        </head></html>
        """,
        "2608.10232",
    )

    assert payload.first_author == "Quanquan Peng"
    assert [author["name"] for author in payload.authors] == [
        "Quanquan Peng",
        "Yutong Liang",
    ]
    assert payload.arxiv_version == 1
    assert payload.published_at.isoformat() == "2026-08-10"


def test_health_and_defaults(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    settings = {item["key"]: item["value_json"] for item in client.get("/api/settings").json()}
    assert settings["abstract_limit"] == 100
    assert settings["fulltext_limit"] == 10
    assert settings["brief_limit"] == 50
    assert settings["featured_limit"] == 10
    assert settings["daily_time"] == "09:05"
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 10, "featured_limit": 4},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 51, "featured_limit": 4},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 3, "featured_limit": 3},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 50, "featured_limit": 10},
        ).status_code
        == 200
    )
    assert client.put("/api/settings/daily_time", json={"value": "09:30"}).status_code == 200
    assert client.put("/api/settings/daily_time", json={"value": "9:30"}).status_code == 422
    settings_after = {
        item["key"]: item["value_json"] for item in client.get("/api/settings").json()
    }
    assert settings_after["daily_time"] == "09:30"
    assert settings_after["daily_hour"] == 9
    assert settings_after["daily_minute"] == 30


def test_paper_dedup_annotations_and_feedback(client: TestClient) -> None:
    paper = create_paper(client)
    duplicate = create_paper(client)
    assert duplicate["id"] == paper["id"]
    assert (
        client.patch(f"/api/papers/{paper['id']}", json={"status": "completed"}).status_code == 422
    )

    started = client.post(f"/api/papers/{paper['id']}/start-reading")
    assert started.status_code == 200
    assert started.json()["status"] == "reading"

    annotation = client.post(
        f"/api/papers/{paper['id']}/annotations",
        json={
            "kind": "highlight",
            "page_number": 2,
            "selected_text": "tactile sensing",
            "position_json": {"rects": [[0.1, 0.2, 0.3, 0.1]]},
        },
    )
    assert annotation.status_code == 200
    assert annotation.json()["kind"] == "note"
    assert annotation.json()["content"] == ""
    assert len(client.get(f"/api/papers/{paper['id']}/annotations").json()) == 1
    updated_annotation = client.patch(
        f"/api/papers/{paper['id']}/annotations/{annotation.json()['id']}",
        json={
            "kind": "note",
            "page_number": 2,
            "selected_text": "tactile sensing",
            "content": "Compare this representation with vision features.",
            "position_json": {"rects": [[0.1, 0.2, 0.3, 0.1]]},
            "color": "#9CCCF4",
        },
    )
    assert updated_annotation.json()["color"] == "#9CCCF4"
    assert (
        client.delete(
            f"/api/papers/{paper['id']}/annotations/{annotation.json()['id']}"
        ).status_code
        == 204
    )
    assert client.get(f"/api/papers/{paper['id']}/annotations").json() == []

    tag = client.post("/api/tags", json={"name": "VLA"}).json()
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "summary": "A useful tactile VLA paper [[page:2]].",
            "suggested_interested": True,
            "suggested_quality": True,
            "suggested_tags": ["VLA"],
        },
    )
    assert finish.json()["status"] == "feedback_pending"
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": True,
            "tag_ids": [tag["id"]],
            "new_tags": ["触觉"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    graph_tasks = [
        task
        for task in client.get("/api/tasks", params={"limit": 20}).json()
        if task["kind"] == "graph_maintenance"
        and task["payload_json"].get("context", {}).get("paper_id") == paper["id"]
    ]
    assert graph_tasks == []
    library = client.get("/api/papers", params={"library": True}).json()
    assert any(item["id"] == paper["id"] for item in library["items"])
    assert library["total"] >= 1
    node = client.post(
        "/api/graph/nodes",
        json={"name": "Tactile Library Test", "core_question": "How does touch improve control?"},
    ).json()
    assert (
        client.post(
            "/api/graph/paper-links",
            json={"paper_id": paper["id"], "node_id": node["id"], "reason": "Library test"},
        ).status_code
        == 200
    )
    cancelled = client.post(f"/api/papers/{paper['id']}/cancel-reading")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "candidate"
    assert cancelled.json()["reading_completed_at"] is None
    assert cancelled.json()["is_interested"] is False
    assert cancelled.json()["is_quality"] is False
    assert cancelled.json()["pending_feedback"] == {}
    assert cancelled.json()["nodes"] == []
    assert {item["name"] for item in cancelled.json()["tags"]} == {"VLA", "触觉"}
    assert not any(
        item["id"] == paper["id"]
        for item in client.get("/api/papers", params={"library": True}).json()["items"]
    )


def test_finish_reading_stores_and_serializes_structured_summary(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Structured completion summary")
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-08-01", "title": "Structured summary brief"},
    ).json()
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": 1, "tier": "featured"},
        ).status_code
        == 200
    )
    assert client.post(f"/api/briefs/{brief['id']}/publish").status_code == 200
    structured = {
        "problem": "研究如何提升操作泛化能力 [[page:1]]。",
        "innovation": "提出统一策略并给出消融证据 [[page:4]]。",
        "experiment": "在三个基准上进行了实验 [[page:7]]。",
        "limitations": "真实场景覆盖仍然有限 [[page:12]]。",
        "open_questions": "跨平台迁移仍待验证 [[page:12]]。",
        "user_attitude": "用户对跨平台迁移最感兴趣。",
    }

    response = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "completion_summary_structured": structured,
            "suggested_interested": True,
            "suggested_tags": ["VLA"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completion_summary_structured"] == structured
    assert body["metadata_json"]["completion_summary_structured"] == structured
    assert "### 解决的问题" in body["completion_summary"]
    assert "### 用户态度" in body["completion_summary"]
    assert "completion_summary_structured" not in body["pending_feedback"]
    assert body["pending_feedback"]["suggested_interested"] is True
    brief_paper = client.get(f"/api/briefs/{brief['id']}").json()["items"][0]["paper"]
    assert brief_paper["completion_summary_structured"] == {
        key: value for key, value in structured.items() if key != "user_attitude"
    }
    assert "用户对跨平台迁移" not in brief_paper["completion_summary"]
    metadata_update = client.patch(
        f"/api/papers/{paper['id']}", json={"metadata_json": {"custom": "value"}}
    )
    assert metadata_update.status_code == 200, metadata_update.text
    assert metadata_update.json()["completion_summary_structured"] == structured


def test_metadata_update_saves_edited_structured_summary_and_preserves_metadata(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Edit completion summary from metadata")
    seeded = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"metadata_json": {"custom_metadata": {"must": "remain"}}},
    )
    assert seeded.status_code == 200, seeded.text
    edited = {
        "problem": "  手动修改后的解决问题。  ",
        "innovation": "手动修改后的创新内容。",
        "experiment": "手动修改后的实验。",
        "limitations": "手动修改后的局限。",
        "open_questions": "手动修改后的未解决问题。",
        "user_attitude": "手动修改后的用户态度。",
    }

    response = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"completion_summary_structured": edited},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    expected = {**edited, "problem": edited["problem"].strip()}
    assert body["completion_summary_structured"] == expected
    assert body["metadata_json"]["completion_summary_structured"] == expected
    assert body["metadata_json"]["custom_metadata"] == {"must": "remain"}
    assert "手动修改后的解决问题" in body["completion_summary"]
    assert "手动修改后的用户态度" in body["completion_summary"]
    assert body["is_interested"] is False
    assert body["is_quality"] is False


def test_confirm_feedback_saves_user_edited_structured_summary(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Edit completion summary while confirming")
    assert client.post(f"/api/papers/{paper['id']}/finish", json={}).status_code == 200
    edited = {
        "problem": "确认时修改的解决问题。",
        "innovation": "确认时修改的创新内容。",
        "experiment": "确认时修改的实验。",
        "limitations": "确认时修改的局限。",
        "open_questions": "确认时修改的未解决问题。",
        "user_attitude": "确认时修改的用户态度。",
    }

    response = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": False,
            "completion_summary_structured": edited,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["is_interested"] is True
    assert body["is_quality"] is False
    assert body["completion_summary_structured"] == edited
    assert body["metadata_json"]["completion_summary_structured"] == edited
    assert "确认时修改的解决问题" in body["completion_summary"]
    assert "确认时修改的用户态度" in body["completion_summary"]


def test_quality_level_is_five_state_and_can_be_revised_with_interest(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Three-state user evaluation")
    assert client.post(f"/api/papers/{paper['id']}/finish", json={}).status_code == 200

    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interest_level": "neutral", "quality_level": "poor"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["interest_level"] == "neutral"
    assert confirmed.json()["quality_level"] == "poor"
    assert confirmed.json()["is_quality"] is False

    revised = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"interest_level": "interested", "quality_level": "excellent"},
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["interest_level"] == "interested"
    assert body["quality_level"] == "excellent"
    assert body["is_interested"] is True
    assert body["is_quality"] is True
    provenance = body["metadata_json"]["feedback_provenance"]
    assert provenance["revision"] == 2
    assert provenance["quality_level"] == "excellent"
    assert provenance["evidence_key"] == f"paper:{paper['id']}:feedback:2"


def test_screening_supports_five_levels_and_agent_score_confirmation(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Five-level screening evaluation")

    confirmed = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={
            "interest_level": "high",
            "quality_level": "good",
            "agent_score_confirmed": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["screening_interest_level"] == "high"
    assert body["screening_quality_level"] == "good"
    assert body["agent_score_confirmed"] is True

    revoked = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"agent_score_confirmed": False},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["agent_score_confirmed"] is False


def test_structured_completion_summary_requires_every_non_blank_section(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Incomplete structured completion summary")

    missing = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "completion_summary_structured": {
                "problem": "解决的问题。",
                "innovation": "创新内容。",
                "experiment": "实验。",
                "limitations": "局限。",
                "open_questions": "未解决问题。",
            }
        },
    )
    blank = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "completion_summary_structured": {
                "problem": "解决的问题。",
                "innovation": "创新内容。",
                "experiment": "实验。",
                "limitations": "局限。",
                "open_questions": "未解决问题。",
                "user_attitude": "   ",
            }
        },
    )

    assert missing.status_code == 422
    assert blank.status_code == 422

    metadata_missing = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={
            "completion_summary_structured": {
                "problem": "解决的问题。",
                "innovation": "创新内容。",
                "experiment": "实验。",
                "limitations": "局限。",
                "open_questions": "未解决问题。",
            }
        },
    )
    confirm_blank = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": False,
            "quality": False,
            "completion_summary_structured": {
                "problem": "解决的问题。",
                "innovation": "创新内容。",
                "experiment": "实验。",
                "limitations": "局限。",
                "open_questions": "未解决问题。",
                "user_attitude": "   ",
            },
        },
    )
    assert metadata_missing.status_code == 422
    assert confirm_blank.status_code == 422


def test_legacy_completion_summary_is_migrated_to_editable_structure(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Legacy completion summary")
    summary = "这是旧版精读总结 [[page:2]]。"

    response = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"summary": summary},
    )

    assert response.status_code == 200, response.text
    assert response.json()["completion_summary"] == summary
    structured = response.json()["completion_summary_structured"]
    assert structured["innovation"] == summary
    assert structured["problem"] == "历史记录未单独记录解决的问题。"
    assert structured["experiment"] == "历史记录未单独记录实验结果。"
    assert structured["limitations"] == "历史记录未单独记录局限。"
    assert structured["open_questions"] == "历史记录未单独记录未解决问题。"
    assert structured["user_attitude"] == "历史记录中没有可确认的用户态度。"
    assert "overview" not in structured


def test_legacy_markdown_sections_are_migrated_without_losing_content(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Structured legacy completion summary")
    legacy = """### 研究问题

旧问题。

### 核心贡献

旧贡献。

### 实验结果

旧实验。

### 局限与遗留问题

旧局限。

### 用户态度

旧态度。"""

    response = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"summary": legacy},
    )

    assert response.status_code == 200, response.text
    assert response.json()["completion_summary_structured"] == {
        "problem": "旧问题。",
        "innovation": "旧贡献。",
        "experiment": "旧实验。",
        "limitations": "旧局限。",
        "open_questions": "历史记录未单独记录未解决问题。",
        "user_attitude": "旧态度。",
    }


def test_agent_analysis_is_not_exposed_as_a_private_reading_summary(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Analysis is not a completion summary")

    response = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={
            "problem": "Agent 提取的研究问题",
            "innovation": "Agent 提取的创新",
            "experiment": "Agent 提取的实验",
            "limitations": "Agent 提取的局限",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["completion_summary_structured"] is None


def test_papers_are_grouped_by_reading_status(client: TestClient) -> None:
    candidate = create_paper(client, "Status order candidate")
    reading = create_paper(client, "Status order reading")
    pending = create_paper(client, "Status order pending")
    completed = create_paper(client, "Status order completed")
    ignored = create_paper(client, "Status order ignored")

    assert client.post(f"/api/papers/{reading['id']}/start-reading").status_code == 200
    assert client.post(f"/api/papers/{pending['id']}/finish", json={}).status_code == 200
    assert client.post(f"/api/papers/{completed['id']}/finish", json={}).status_code == 200
    assert (
        client.post(
            f"/api/papers/{completed['id']}/confirm-feedback",
            json={"interested": False, "quality": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/papers/{ignored['id']}/ignore",
            json={"reason": "not relevant"},
        ).status_code
        == 200
    )

    target_ids = {candidate["id"], reading["id"], pending["id"], completed["id"], ignored["id"]}
    ordered = [
        item["status"]
        for item in client.get("/api/papers", params={"limit": 200}).json()["items"]
        if item["id"] in target_ids
    ]
    assert ordered == ["feedback_pending", "reading", "candidate", "completed", "ignored"]


def test_timestamps_are_serialized_in_beijing_time(client: TestClient) -> None:
    memory = client.post(
        "/api/memories",
        json={"kind": "agent_experience", "title": "Timezone", "content": "UTC storage"},
    ).json()
    assert memory["created_at"].endswith("+08:00")


def test_subgraph_uses_node_budget_and_exposes_boundary_nodes(client: TestClient) -> None:
    nodes = [
        client.post(
            "/api/graph/nodes",
            json={"name": f"Budget node {index}", "core_question": f"Question {index}"},
        ).json()
        for index in range(5)
    ]
    for source, target in pairwise(nodes):
        response = client.post(
            "/api/graph/edges",
            json={"source_id": source["id"], "target_id": target["id"]},
        )
        assert response.status_code == 200

    response = client.get(f"/api/graph/subgraph?focus_id={nodes[0]['id']}&limit=3")

    assert response.status_code == 200
    payload = response.json()
    assert {node["id"] for node in payload["nodes"]} == {
        nodes[0]["id"],
        nodes[1]["id"],
        nodes[2]["id"],
    }
    boundary = next(node for node in payload["boundary_nodes"] if node["id"] == nodes[3]["id"])
    assert boundary["connects_to"] == [nodes[2]["id"]]
    assert payload["has_more"] is True


def test_graph_and_brief_snapshot(client: TestClient) -> None:
    paper = create_paper(client, "A New Robot World Model")
    parent = client.post(
        "/api/graph/nodes",
        json={"name": "VLA Test", "core_question": "How should robots act?"},
    ).json()
    child = client.post(
        "/api/graph/nodes",
        json={"name": "Tactile VLA Test", "core_question": "How should touch improve action?"},
    ).json()
    assert (
        client.post(
            "/api/graph/edges", json={"source_id": parent["id"], "target_id": child["id"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/graph/paper-links",
            json={
                "paper_id": paper["id"],
                "node_id": child["id"],
                "reason": "Addresses the core question.",
                "is_key": True,
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/progress",
            json={
                "title": "New evidence",
                "content": "Touch improved success.",
                "paper_id": paper["id"],
            },
        ).status_code
        == 409
    )
    assert client.get(f"/api/graph/nodes/{child['id']}").json()["paper_count"] == 0

    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"summary": "Reading complete."},
    )
    assert finish.status_code == 200
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert (
        client.post(
            "/api/graph/paper-links",
            json={
                "paper_id": paper["id"],
                "node_id": child["id"],
                "reason": "Addresses the core question.",
                "is_key": True,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/progress",
            json={
                "title": "New evidence",
                "content": "Touch improved success.",
                "paper_id": paper["id"],
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/ideas",
            json={"content": "Test asynchronous fusion.", "status": "to_explore"},
        ).status_code
        == 200
    )
    detail = client.get(f"/api/graph/nodes/{child['id']}").json()
    assert detail["parents"][0]["id"] == parent["id"]
    assert detail["papers"][0]["id"] == paper["id"]
    assert detail["ideas"][0]["status"] == "to_explore"

    brief_paper = create_paper(client, "Unread paper for brief snapshot")
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-29", "title": "Test brief"},
    ).json()
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": brief_paper["id"], "rank": 1, "tier": "featured"},
        ).status_code
        == 200
    )
    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200
    assert published.json()["items"][0]["paper"]["title"] == brief_paper["title"]
    assert (
        client.post(
            f"/api/papers/{brief_paper['id']}/finish",
            json={"summary": "Reading complete after publication."},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/papers/{brief_paper['id']}/confirm-feedback",
            json={"interested": False, "quality": False},
        ).status_code
        == 200
    )
    historical = client.get(f"/api/briefs/{brief['id']}").json()
    historical_paper = historical["items"][0]["paper"]
    assert (
        historical_paper["completion_summary_structured"]["innovation"]
        == "Reading complete after publication."
    )
    assert "Reading complete after publication." in historical_paper["completion_summary"]
    assert "用户态度" not in historical_paper["completion_summary"]
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": brief_paper["id"], "rank": 2, "tier": "related"},
        ).status_code
        == 409
    )


def test_daily_brief_excludes_prior_reading_but_stays_stable_after_publish(
    client: TestClient,
) -> None:
    candidate = create_paper(client, "Unread daily candidate")
    other_candidate = create_paper(client, "Second unread daily candidate")
    reading = create_paper(client, "Already being read")
    completed = create_paper(client, "Already completed")
    assert client.post(f"/api/papers/{reading['id']}/start-reading").status_code == 200
    assert client.post(f"/api/papers/{completed['id']}/finish", json={}).status_code == 200
    assert (
        client.post(
            f"/api/papers/{completed['id']}/confirm-feedback",
            json={"interested": False, "quality": False},
        ).status_code
        == 200
    )

    # Daily briefs follow Frontier's configured research-day rollover, which can
    # still be the previous calendar date before the configured scheduler boundary.
    from frontier.database import SessionLocal
    from frontier.services.research_day import current_research_date

    with SessionLocal() as session:
        today = current_research_date(session).isoformat()
    brief = client.post(
        "/api/briefs",
        json={"brief_date": today, "title": "Unread-only daily brief"},
    ).json()
    for rank, paper in enumerate((candidate, other_candidate), start=1):
        assert (
            client.post(
                f"/api/briefs/{brief['id']}/items",
                json={"paper_id": paper["id"], "rank": rank, "tier": "featured"},
            ).status_code
            == 200
        )
    for paper in (reading, completed):
        rejected = client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": 3, "tier": "related"},
        )
        assert rejected.status_code == 409
        assert "Only unread candidate papers" in rejected.json()["detail"]

    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200, published.text
    assert {item["paper"]["id"] for item in published.json()["items"]} == {
        candidate["id"],
        other_candidate["id"],
    }

    assert client.post(f"/api/papers/{candidate['id']}/start-reading").status_code == 200
    current = client.get(f"/api/briefs/{brief['id']}").json()
    assert [item["paper"]["id"] for item in current["items"]] == [
        candidate["id"],
        other_candidate["id"],
    ]
    assert current["items"][0]["current_status"] == "reading"

    assert client.post(f"/api/papers/{candidate['id']}/cancel-reading").status_code == 200
    restored = client.get(f"/api/briefs/{brief['id']}").json()
    assert {item["paper"]["id"] for item in restored["items"]} == {
        candidate["id"],
        other_candidate["id"],
    }

    ignored = client.post(
        f"/api/papers/{candidate['id']}/ignore",
        json={"reason": "Not relevant after review"},
    )
    assert ignored.status_code == 200, ignored.text
    retained = client.get(f"/api/briefs/{brief['id']}").json()
    assert [item["paper"]["id"] for item in retained["items"]] == [
        candidate["id"],
        other_candidate["id"],
    ]
    assert retained["items"][0]["current_status"] == "ignored"
    listed = client.get("/api/briefs").json()
    assert next(item for item in listed if item["id"] == brief["id"])["item_count"] == 2


def test_brief_publish_drops_papers_that_started_reading_after_ranking(
    client: TestClient,
) -> None:
    stale = create_paper(client, "Candidate that starts reading before publish")
    eligible = create_paper(client, "Candidate that remains unread")
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-28", "title": "Publish race test"},
    ).json()
    for rank, paper in enumerate((stale, eligible), start=1):
        assert (
            client.post(
                f"/api/briefs/{brief['id']}/items",
                json={"paper_id": paper["id"], "rank": rank, "tier": "featured"},
            ).status_code
            == 200
        )
    assert client.post(f"/api/papers/{stale['id']}/start-reading").status_code == 200

    published = client.post(f"/api/briefs/{brief['id']}/publish")

    assert published.status_code == 200, published.text
    assert [item["paper"]["id"] for item in published.json()["items"]] == [eligible["id"]]


def test_brief_accepts_configured_limit_without_automatic_document_prewarm(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "frontier.api.briefs.get_setting",
        lambda _session, key, default: {
            "brief_limit": 10,
            "featured_limit": 6,
            "fulltext_limit": 4,
        }.get(key, default),
    )
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-30", "title": "Featured text cache test"},
    ).json()
    for index in range(5):
        paper = create_paper(client, f"Featured cache paper {index}")
        assert (
            client.post(
                f"/api/briefs/{brief['id']}/items",
                json={"paper_id": paper["id"], "rank": index + 1, "tier": "featured"},
            ).status_code
            == 200
        )
    for index in range(5, 10):
        related = create_paper(client, f"Related paper without text prewarm {index}")
        assert (
            client.post(
                f"/api/briefs/{brief['id']}/items",
                json={"paper_id": related["id"], "rank": index + 1, "tier": "related"},
            ).status_code
            == 200
        )
    overflow = create_paper(client, "Paper beyond the ten-item limit")
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": overflow["id"], "rank": 11, "tier": "related"},
        ).status_code
        == 409
    )

    published = client.post(f"/api/briefs/{brief['id']}/publish")

    assert published.status_code == 200, published.text
    assert len(published.json()["items"]) == 10


def test_pdf_upload_and_extract(client: TestClient, monkeypatch) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A Tactile Robot Paper")
    page.insert_text((72, 100), "The experiment improves manipulation success.")
    content = document.tobytes()
    document.close()
    response = client.post(
        "/api/papers/ingest/pdf",
        files={"file": ("paper.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    paper = response.json()["paper"]
    assert paper["pdf_version"]
    pdf_response = client.get(f"/api/papers/{paper['id']}/pdf?v={paper['pdf_version']}")
    assert pdf_response.status_code == 200
    assert pdf_response.headers["cache-control"] == "private, max-age=31536000, immutable"
    text = client.get(f"/api/papers/{paper['id']}/text").json()
    assert text["page_count"] == 1
    assert "manipulation success" in text["pages"][0]["text"]
    rendered = client.post(
        f"/api/papers/{paper['id']}/render-page",
        params={"page_number": 1, "dpi": 96},
    )
    assert rendered.status_code == 200
    assert rendered.json()["path"].endswith("96dpi.png")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/render-page",
            params={"page_number": 2},
        ).status_code
        == 400
    )


def test_arxiv_pdf_uses_source_redirect_without_local_download(client: TestClient) -> None:
    paper = create_paper(client, "arXiv redirect paper")
    with SessionLocal() as session:
        item = session.get(Paper, paper["id"])
        assert item is not None
        item.source = "arxiv"
        item.arxiv_id = "2410.24164"
        item.arxiv_version = 4
        session.commit()

    response = client.get(
        f"/api/papers/{paper['id']}/pdf",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "https://arxiv.org/pdf/2410.24164v4"

    download = client.post(f"/api/papers/{paper['id']}/download-pdf")
    assert download.status_code == 200
    assert download.json()["url"] == "https://arxiv.org/pdf/2410.24164v4"
    assert download.json()["pdf_path"] is None


def test_document_reader_queues_and_serves_cached_result(client: TestClient, monkeypatch) -> None:
    paper = create_paper(client, "A Structured Mobile Reader Paper")
    queued: list[str] = []
    monkeypatch.setattr(
        "frontier.api.papers.document_status",
        lambda _session, item: {
            "paper_id": item.id,
            "version": "v1",
            "status": "missing",
        },
    )
    monkeypatch.setattr(
        "frontier.api.papers.queue_document_conversion",
        lambda paper_id, **_kwargs: queued.append(paper_id) or True,
    )
    response = client.get(f"/api/papers/{paper['id']}/document")
    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert queued == [paper["id"]]

    monkeypatch.setattr(
        "frontier.api.papers.document_status",
        lambda _session, item: {
            "paper_id": item.id,
            "version": "v1",
            "status": "ready",
            "blocks": [
                {
                    "id": "intro",
                    "kind": "text",
                    "page_number": 1,
                    "level": 0,
                    "text": "A readable paragraph.",
                }
            ],
        },
    )
    ready = client.get(f"/api/papers/{paper['id']}/document")
    assert ready.json()["blocks"][0]["id"] == "intro"


def test_document_reader_estimate_scales_with_page_count() -> None:
    short = _estimate_conversion_seconds(4)
    long = _estimate_conversion_seconds(40)
    assert short[0] < short[1]
    assert long[0] > short[0]
    assert long[1] > short[1]


def test_document_resources_api_exposes_cached_arxiv_semantics(
    client: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    from frontier.api import papers

    paper = create_paper(client, "Structured resource API")
    image = tmp_path / "figure.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(
        papers,
        "document_status",
        lambda _session, item: {
            "paper_id": item.id,
            "version": "v1",
            "status": "ready",
            "reader_source": "arxiv_html",
            "blocks": [
                {
                    "id": "formula",
                    "kind": "formula",
                    "page_number": 2,
                    "html": '<span data-reader-tex="E=mc^2" data-reader-display="block"></span>',
                },
                {
                    "id": "figure",
                    "kind": "picture",
                    "page_number": 3,
                    "caption": "Figure 1",
                    "image_url": f"/api/papers/{item.id}/document/assets/figure.png",
                },
            ],
        },
    )
    monkeypatch.setattr(papers, "document_asset", lambda _paper_id, _filename: image)

    response = client.get(
        f"/api/papers/{paper['id']}/document/resources", params={"kind": "formula"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["formulas"][0]["tex"] == "E=mc^2"
    assert "figures" not in response.json()


def test_document_resources_api_reports_not_ready_without_partial_data(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import papers

    paper = create_paper(client, "Pending structured resources")
    queued: list[str] = []
    monkeypatch.setattr(
        papers,
        "document_status",
        lambda _session, item: {"paper_id": item.id, "status": "missing"},
    )
    monkeypatch.setattr(
        papers,
        "queue_document_conversion",
        lambda paper_id: queued.append(paper_id) or True,
    )

    response = client.get(f"/api/papers/{paper['id']}/document/resources")

    assert response.status_code == 409
    assert queued == []
    assert response.json()["detail"]["document_status"]["status"] == "missing"


def test_document_reader_detects_stale_queued_status() -> None:
    fresh = datetime.now(UTC).isoformat()
    stale = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    assert _processing_status_is_live("not-running", {"phase": "queued", "updated_at": fresh})
    assert not _processing_status_is_live("not-running", {"phase": "queued", "updated_at": stale})


def test_document_reader_invalidates_old_cache_and_worker_status() -> None:
    version = "pdf-v1"

    assert _document_payload_is_current(
        {"version": version, "schema_version": DOCUMENT_SCHEMA_VERSION}, version
    )
    assert not _document_payload_is_current(
        {"version": version, "schema_version": DOCUMENT_SCHEMA_VERSION - 1}, version
    )
    assert not _document_payload_is_current(
        {"version": "pdf-v0", "schema_version": DOCUMENT_SCHEMA_VERSION}, version
    )
    assert _document_payload_is_current(
        {"schema_version": DOCUMENT_SCHEMA_VERSION}, version, allow_missing_version=True
    )


def test_docling_is_blocked_before_import_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("frontier.services.document_reader.settings.enable_docling", False)
    try:
        convert_with_docling(
            tmp_path / "unused.pdf",
            paper_id="paper-without-html",
            version="v1",
            root=tmp_path,
        )
    except DocumentReaderUnavailable as exc:
        assert "Docling 已在当前部署中关闭" in str(exc)
    else:
        raise AssertionError("disabled Docling must not be imported or started")


def test_manual_ingest_stages_daily_recommendation_and_metadata_edit(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr("frontier.api.papers.settings.enable_codex_runs", True)
    monkeypatch.setattr("frontier.api.papers.submit_task", lambda _task_id: True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A User Added Tactile Foundation Policy")
    page.insert_text((72, 100), "The full paper studies transferable tactile manipulation.")
    content = document.tobytes()
    document.close()

    response = client.post(
        "/api/papers/ingest/pdf",
        data={"recommend": "true"},
        files={"file": ("manual-tactile.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_task_id"]
    assert result["paper"]["analysis_status"] == "queued"
    latest = client.get("/api/briefs/latest")
    assert latest.status_code == 200
    manual_items = [
        item for item in latest.json()["items"] if item["paper"]["id"] == result["paper"]["id"]
    ]
    assert len(manual_items) == 1
    assert manual_items[0]["tier"] == "manual"
    assert manual_items[0]["paper"]["analysis_status"] == "queued"

    suggested = client.patch(
        f"/api/papers/{result['paper']['id']}",
        json={"suggested_tags": ["触觉", "基础策略"]},
    )
    assert suggested.status_code == 200
    assert suggested.json()["suggested_tags"] == ["触觉", "基础策略"]

    edited = client.patch(
        f"/api/papers/{result['paper']['id']}/metadata",
        json={
            "title": "Edited Tactile Foundation Policy",
            "authors": [{"name": "First Author"}, {"name": "Second Author"}],
            "first_author": "First Author",
            "corresponding_author": "Second Author",
            "primary_affiliation": "Tactile Robotics Lab",
            "summary": "A manually reviewed summary.",
            "agent_comment": "Promising cross-sensor transfer.",
            "recommendation_reason": "Relevant to tactile policy generalization.",
            "tag_ids": [],
            "new_tags": ["触觉"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Edited Tactile Foundation Policy"
    assert edited.json()["corresponding_author"] == "Second Author"
    assert [tag["name"] for tag in edited.json()["tags"]] == ["触觉"]

    refreshed = client.get("/api/briefs/latest").json()
    manual = next(
        item for item in refreshed["items"] if item["paper"]["id"] == result["paper"]["id"]
    )
    assert manual["paper"]["title"] == "Edited Tactile Foundation Policy"
    assert manual["paper"]["summary"] == "A manually reviewed summary."


def test_manual_ingest_can_defer_agent_analysis(client: TestClient, monkeypatch) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Deferred Agent Analysis Paper")
    content = document.tobytes()
    document.close()

    response = client.post(
        "/api/papers/ingest/pdf",
        data={"recommend": "true", "analyze": "false"},
        files={"file": ("deferred-analysis.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_task_id"] is None
    assert result["paper"]["analysis_status"] is None
    latest = client.get("/api/briefs/latest").json()
    assert any(item["paper"]["id"] == result["paper"]["id"] for item in latest["items"])


def test_memory_task_and_conversation(client: TestClient, monkeypatch) -> None:
    from frontier.api import conversations

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)
    monkeypatch.setattr(conversations, "submit_task", lambda _task_id: True)
    memory = client.post(
        "/api/memories",
        json={
            "kind": "interest_profile",
            "title": "Tactile interests",
            "content": "Prefer tactile VLA papers.",
        },
    )
    assert memory.status_code == 200
    assert (
        client.patch(f"/api/memories/{memory.json()['id']}", json={"active": False}).status_code
        == 200
    )

    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Research assistant"}
    ).json()
    sent = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Show me recent tactile work.", "context_json": {"page": "brief"}},
    )
    assert sent.status_code == 200
    task = client.get(f"/api/tasks/{sent.json()['task_id']}").json()
    assert task["status"] == "queued"


def test_streamed_conversation_persists_activity_with_answer(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)

    def fake_stream(*_args, **_kwargs):
        yield {"type": "thread", "thread_id": "thread-with-activity"}
        yield {
            "type": "runtime",
            "model": "gpt-5.4",
            "model_provider": "openai",
            "reasoning_effort": "high",
        }
        yield {
            "type": "usage",
            "current_tokens": 1024,
            "context_window": 32768,
            "total_tokens": 1536,
        }
        yield {
            "type": "activity",
            "activity_id": "tool-1",
            "kind": "tool",
            "label": "正在读取论文",
            "status": "running",
        }
        yield {
            "type": "activity",
            "activity_id": "tool-1",
            "kind": "tool",
            "label": "已读取论文",
            "status": "completed",
        }
        yield {"type": "delta", "delta": "**结论** 正文"}

    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Activity persistence"}
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "请分析"},
    )
    assert response.status_code == 200, response.text
    assert '"type": "runtime"' in response.text
    assert '"type": "usage"' in response.text
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assistant = detail["messages"][-1]
    assert assistant["content"] == "**结论** 正文"
    assert assistant["context_json"]["stream_status"] == "completed"
    assert assistant["context_json"]["runtime"] == {
        "model": "gpt-5.4",
        "model_provider": "openai",
        "reasoning_effort": "high",
    }
    assert assistant["context_json"]["usage"] == {
        "current_tokens": 1024,
        "context_window": 32768,
        "total_tokens": 1536,
    }
    activities = assistant["context_json"]["activities"]
    assert next(item for item in activities if item["id"] == "tool-1") == {
        "id": "tool-1",
        "kind": "tool",
        "label": "已读取论文",
        "status": "completed",
    }
    task = client.get(f"/api/tasks/{assistant['context_json']['task_id']}").json()
    assert task["result_json"] == {
        "response": "**结论** 正文",
        "activities": activities,
        "stream_status": "completed",
        "runtime": assistant["context_json"]["runtime"],
        "usage": assistant["context_json"]["usage"],
    }


def test_streamed_conversation_persists_partial_answer_and_failed_activity(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)

    def fake_stream(*_args, **_kwargs):
        yield {"type": "thread", "thread_id": "thread-partial"}
        yield {"type": "runtime", "model": "gpt-5.4", "reasoning_effort": "medium"}
        yield {"type": "usage", "current_tokens": 400, "context_window": 8192}
        yield {
            "type": "activity",
            "activity_id": "reason-1",
            "kind": "reasoning",
            "label": "已完成分析",
            "detail": "已经找到关键证据",
            "status": "completed",
        }
        yield {"type": "delta", "delta": "已经生成的部分回答"}
        raise RuntimeError("模型连接中断")

    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Partial persistence"}
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "请分析"},
    )
    assert response.status_code == 200, response.text

    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assistant = detail["messages"][-1]
    assert assistant["content"] == "已经生成的部分回答"
    assert assistant["context_json"]["stream_status"] == "failed"
    assert assistant["context_json"]["stream_error"] == "模型连接中断"
    assert assistant["context_json"]["activities"][-1]["status"] == "failed"
    assert assistant["context_json"]["activities"][-1]["label"] == "Agent 响应未完成"
    assert assistant["context_json"]["runtime"] == {
        "model": "gpt-5.4",
        "reasoning_effort": "medium",
    }
    assert assistant["context_json"]["usage"] == {
        "current_tokens": 400,
        "context_window": 8192,
    }
    task = client.get(f"/api/tasks/{assistant['context_json']['task_id']}").json()
    assert task["status"] == "failed"
    assert task["result_json"]["response"] == "已经生成的部分回答"


def test_failed_conversation_answer_can_continue_without_a_duplicate_user_message(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)

    def failed_stream(*_args, **_kwargs):
        yield {"type": "thread", "thread_id": "thread-retry"}
        yield {"type": "delta", "delta": "已有的部分回答。"}
        raise RuntimeError("temporary disconnect")

    monkeypatch.setattr(conversations, "stream_codex_prompt", failed_stream)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Retry continuation"}
    ).json()
    first = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "请完整回答"},
    )
    assert first.status_code == 200
    failed_detail = client.get(f"/api/conversations/{conversation['id']}").json()
    failed_assistant = failed_detail["messages"][-1]
    assert failed_assistant["context_json"]["stream_status"] == "failed"

    def continued_stream(prompt, **_kwargs):
        assert "Persisted partial response" in prompt
        assert "已有的部分回答。" in prompt
        yield {"type": "thread", "thread_id": "thread-retry"}
        yield {"type": "delta", "delta": "这是继续完成的内容。"}

    monkeypatch.setattr(conversations, "stream_codex_prompt", continued_stream)
    retried = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={
            "content": "继续未完成的回答",
            "retry_assistant_id": failed_assistant["id"],
        },
    )
    assert retried.status_code == 200, retried.text
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assistant = detail["messages"][-1]
    assert assistant["id"] == failed_assistant["id"]
    assert assistant["content"] == "已有的部分回答。\n\n这是继续完成的内容。"
    assert assistant["context_json"]["stream_status"] == "completed"
    assert assistant["context_json"]["retry_history"][0]["stream_status"] == "failed"


def test_completed_or_cancelled_conversation_answer_cannot_be_retried(
    client: TestClient,
) -> None:
    from frontier.database import SessionLocal
    from frontier.models import Message

    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "No retry"}
    ).json()
    with SessionLocal() as session:
        assistant = Message(
            conversation_id=conversation["id"],
            role="assistant",
            content="已经完成",
            context_json={"stream_status": "completed"},
        )
        session.add(assistant)
        session.commit()
        assistant_id = assistant.id

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "继续", "retry_assistant_id": assistant_id},
    )
    assert response.status_code == 409


def test_streamed_conversation_keeps_runtime_usage_when_cancelled(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations
    from frontier.database import SessionLocal
    from frontier.models import TaskRun

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)

    def fake_stream(*_args, **kwargs):
        yield {"type": "runtime", "model": "gpt-5.4", "reasoning_effort": "high"}
        yield {"type": "usage", "current_tokens": 640, "context_window": 8192}
        with SessionLocal() as session:
            task = session.get(TaskRun, kwargs["run_id"])
            assert task is not None
            task.status = "cancelled"
            session.commit()
        raise RuntimeError("process terminated")

    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Cancelled usage"}
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "发错的消息"},
    )
    assert response.status_code == 200, response.text

    assistant = client.get(f"/api/conversations/{conversation['id']}").json()["messages"][-1]
    assert assistant["context_json"]["stream_status"] == "cancelled"
    assert assistant["context_json"]["runtime"] == {
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }
    assert assistant["context_json"]["usage"] == {
        "current_tokens": 640,
        "context_window": 8192,
    }
    task = client.get(f"/api/tasks/{assistant['context_json']['task_id']}").json()
    assert task["result_json"]["runtime"] == assistant["context_json"]["runtime"]
    assert task["result_json"]["usage"] == assistant["context_json"]["usage"]


def test_service_startup_marks_orphaned_conversation_draft_as_interrupted(
    client: TestClient,
) -> None:
    from frontier.api.conversations import mark_orphaned_conversation_streams
    from frontier.database import SessionLocal
    from frontier.models import Message, TaskRun

    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Restart recovery"}
    ).json()
    with SessionLocal() as session:
        task = TaskRun(kind="global_chat", status="running", payload_json={})
        session.add(task)
        session.flush()
        assistant = Message(
            conversation_id=conversation["id"],
            role="assistant",
            content="重启前生成的内容",
            context_json={
                "task_id": task.id,
                "stream_status": "running",
                "activities": [
                    {
                        "id": "tool-1",
                        "kind": "tool",
                        "label": "正在读取资料",
                        "status": "running",
                    }
                ],
                "runtime": {"model": "gpt-5.4", "reasoning_effort": "high"},
                "usage": {"current_tokens": 2048, "context_window": 32768},
            },
        )
        session.add(assistant)
        session.commit()
        task_id = task.id

    assert mark_orphaned_conversation_streams() == 1
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    recovered = detail["messages"][-1]
    assert recovered["content"] == "重启前生成的内容"
    assert recovered["context_json"]["stream_status"] == "interrupted"
    assert recovered["context_json"]["activities"][0]["status"] == "failed"
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "failed"
    assert task["result_json"]["stream_status"] == "interrupted"
    assert task["result_json"]["runtime"] == recovered["context_json"]["runtime"]
    assert task["result_json"]["usage"] == recovered["context_json"]["usage"]


def test_metadata_generation_can_be_requested_without_adding_a_brief_item(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr("frontier.api.papers.settings.enable_codex_runs", True)
    monkeypatch.setattr("frontier.api.papers.submit_task", lambda _task_id: True)
    paper = create_paper(client, "Explicit Metadata Generation Test")
    requested = client.post(f"/api/papers/{paper['id']}/metadata/generate")
    assert requested.status_code == 200, requested.text
    assert requested.json()["created"] is True
    repeated = client.post(f"/api/papers/{paper['id']}/metadata/generate")
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["id"] == requested.json()["id"]

    detail = client.get(f"/api/papers/{paper['id']}").json()
    assert detail["analysis_status"] == "queued"
    after = client.get("/api/briefs/latest")
    after_ids = (
        {item["paper"]["id"] for item in after.json().get("items", [])}
        if after.status_code == 200
        else set()
    )
    assert paper["id"] not in after_ids


def test_agent_score_confirmations_are_independent(client: TestClient) -> None:
    paper = create_paper(client, "Independent Agent score confirmations")
    quality = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"agent_quality_confirmed": True},
    )
    assert quality.status_code == 200, quality.text
    body = quality.json()
    assert body["agent_quality_confirmed"] is True
    assert body["agent_interest_confirmed"] is False
    assert body["agent_score_confirmed"] is False

    interest = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"agent_interest_confirmed": True},
    )
    assert interest.status_code == 200, interest.text
    body = interest.json()
    assert body["agent_quality_confirmed"] is True
    assert body["agent_interest_confirmed"] is True
    assert body["agent_score_confirmed"] is True


def test_metadata_generation_queues_single_paper_abstract_translation(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr("frontier.api.papers.settings.enable_codex_runs", True)
    monkeypatch.setattr("frontier.api.papers.submit_task", lambda _task_id: True)
    paper = create_paper(client, "Metadata generation with abstract translation")
    requested = client.post(f"/api/papers/{paper['id']}/metadata/generate")
    assert requested.status_code == 200, requested.text
    translation_task_id = requested.json()["translation_task_id"]
    assert translation_task_id
    detail = client.get(f"/api/papers/{paper['id']}").json()
    assert detail["abstract_translation_status"] == "queued"


def test_confirm_feedback_preserves_metadata_tags_when_tag_ids_are_omitted(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Preserve Metadata Tags on Reading Completion")
    tag = client.post("/api/tags", json={"name": "保留标签"}).json()
    saved = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"tag_ids": [tag["id"]]},
    )
    assert [item["name"] for item in saved.json()["tags"]] == ["保留标签"]
    assert client.post(f"/api/papers/{paper['id']}/finish", json={}).status_code == 200

    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    )
    assert confirmed.status_code == 200
    assert [item["name"] for item in confirmed.json()["tags"]] == ["保留标签"]


def test_scheduled_daily_is_idempotent(client: TestClient, monkeypatch) -> None:
    from frontier.scheduler import scheduled_daily

    target_date = date(2040, 1, 2)
    target = target_date.isoformat()
    monkeypatch.setattr("frontier.scheduler.settings.enable_codex_runs", True)
    monkeypatch.setattr("frontier.scheduler.submit_task", lambda _task_id: True)
    monkeypatch.setattr("frontier.scheduler.current_research_date", lambda _session: target_date)
    scheduled_daily()
    scheduled_daily()
    tasks = client.get("/api/tasks", params={"limit": 200}).json()
    matching = [
        task
        for task in tasks
        if task["kind"] == "daily_brief" and task["payload_json"].get("date") == target
    ]
    assert len(matching) == 1
