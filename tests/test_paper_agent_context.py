from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from frontier import cli
from frontier.database import SessionLocal
from frontier.models import (
    Annotation,
    ArchitectureDocument,
    Conversation,
    Message,
    Paper,
    PaperAgentContextSync,
    PaperNode,
    PaperTag,
    ReadingProgress,
    ResearchNode,
    Tag,
)
from frontier.services.completion_summary import save_completion_summary
from frontier.services.paper_agent_context import paper_agent_context_sync_required


def _create_paper(client: TestClient) -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": f"Agent Context {uuid4()}",
            "abstract": "A compact test paper.",
            "authors": [{"name": "Ada Researcher"}],
            "source": "manual",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def test_paper_agent_context_composes_existing_state_without_pdf_text(
    client: TestClient, tmp_path
) -> None:
    paper_id = _create_paper(client)["id"]
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nagent-context-test")
    now = datetime.now(UTC)
    tag_name = f"context-tag-{uuid4()}"
    node_name = f"context-node-{uuid4()}"

    with SessionLocal() as session:
        paper = session.get_one(Paper, paper_id)
        paper.pdf_path = str(pdf_path)
        paper.summary = "一句话总结"
        paper.problem = "研究问题"
        paper.full_analysis = {"claims": [{"claim": "核心论断", "page": 3}]}
        paper.metadata_json = {
            "personal_comment": "优先复现结构图。",
            "suggested_tags": ["Agent"],
            "manual_analysis": {"status": "completed", "task_id": "internal-task"},
            "bibliographic_provenance": {"source": "cache"},
            "pdf_metadata": {"producer": "test"},
        }
        save_completion_summary(
            paper,
            {
                "research_question": "精读研究问题 [[page:1]]",
                "evidence_and_contribution": "精读贡献 [[page:3]]",
                "experiments": "精读实验 [[page:5]]",
                "limitations": "精读局限 [[page:8]]",
                "user_attitude": "用户认为值得复现。",
            },
        )
        tag = Tag(name=tag_name, color="#123456")
        node = ResearchNode(
            name=node_name,
            core_question="如何复用论文上下文？",
        )
        session.add_all([tag, node])
        session.flush()
        session.add_all(
            [
                PaperTag(paper_id=paper_id, tag_id=tag.id),
                PaperNode(
                    paper_id=paper_id,
                    node_id=node.id,
                    reason="直接相关",
                    is_key=True,
                ),
                Annotation(
                    paper_id=paper_id,
                    kind="note",
                    page_number=3,
                    selected_text="evidence",
                    content="检查该证据。",
                ),
                ReadingProgress(
                    paper_id=paper_id,
                    page_number=4,
                    scroll_offset=0.25,
                    zoom=1.2,
                ),
                ArchitectureDocument(
                    paper_id=paper_id,
                    status="ready",
                    revision=4,
                    source_pdf_version="older-pdf-version",
                ),
            ]
        )
        conversation = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="论文问答",
            codex_session_id="thread-1",
        )
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="论文解决了什么问题？",
                    created_at=now - timedelta(seconds=1),
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="它解决了上下文复用问题。 [[page:1]]",
                    citations_json=[{"page": 1}],
                    created_at=now,
                ),
            ]
        )
        session.commit()
        tag_id = tag.id
        node_id = node.id

    response = client.get(
        f"/api/papers/{paper_id}/agent-context",
        params={"query": "  如何画结构图？  "},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert list(body) == [
        "schema_version",
        "request_context",
        "paper",
        "full_analysis",
        "completion_summary_structured",
        "metadata",
        "tags",
        "nodes",
        "annotations",
        "reading_progress",
        "conversation",
        "architecture",
        "document",
    ]
    assert body["request_context"] == {"query": "如何画结构图？"}
    assert body["paper"]["summary"] == "一句话总结"
    assert "metadata_json" not in body["paper"]
    assert "completion_summary" not in body["paper"]
    assert body["full_analysis"] == {"claims": [{"claim": "核心论断", "page": 3}]}
    assert body["completion_summary_structured"]["user_attitude"] == "用户认为值得复现。"
    assert body["metadata"] == {
        "personal_comment": "优先复现结构图。",
        "suggested_tags": ["Agent"],
    }
    assert "manual_analysis" not in body["metadata"]
    assert "bibliographic_provenance" not in body["metadata"]
    assert "pdf_metadata" not in body["metadata"]
    assert body["tags"] == [{"id": tag_id, "name": tag_name, "color": "#123456"}]
    assert body["nodes"][0] == {
        "id": node_id,
        "name": node_name,
        "core_question": "如何复用论文上下文？",
        "reason": "直接相关",
        "is_key": True,
    }
    assert body["annotations"][0]["page_number"] == 3
    assert body["reading_progress"]["page_number"] == 4
    assert body["conversation"]["has_codex_session"] is True
    assert body["conversation"]["message_count"] == 2
    assert [item["role"] for item in body["conversation"]["recent_messages"]] == [
        "user",
        "assistant",
    ]
    assert body["architecture"]["status"] == "ready"
    assert body["architecture"]["revision"] == 4
    assert body["architecture"]["stale"] is True
    assert body["document"]["has_pdf"] is True
    assert body["document"]["pdf_version"]
    assert body["document"]["full_text_included"] is False
    assert "pdf" not in body
    assert "pages" not in body


def test_paper_agent_context_is_read_only_and_returns_404(client: TestClient) -> None:
    paper_id = _create_paper(client)["id"]

    response = client.get(f"/api/papers/{paper_id}/agent-context")
    assert response.status_code == 200
    assert response.json()["reading_progress"]["saved"] is False
    with SessionLocal() as session:
        assert session.get(ReadingProgress, paper_id) is None

    missing = client.get(f"/api/papers/{uuid4()}/agent-context")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Paper not found"}


def test_paper_agent_context_cli_calls_composed_endpoint(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        captured.update({"method": method, "path": path, **kwargs})
        return {"schema_version": 1}

    monkeypatch.setattr(cli.client, "request", fake_request)

    cli.paper_agent_context("paper-1", "structure")

    assert captured == {
        "method": "GET",
        "path": "/api/papers/paper-1/agent-context",
        "params": {"query": "structure"},
    }
    assert '"schema_version": 1' in capsys.readouterr().out


def test_paper_agent_context_cli_uses_incremental_sync_for_conversation(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        captured.update({"method": method, "path": path, **kwargs})
        return {"schema_version": 2, "sync": {"mode": "unchanged"}}

    monkeypatch.setattr(cli.client, "request", fake_request)

    cli.paper_agent_context("paper-1", None, "conversation-1")

    assert captured == {
        "method": "POST",
        "path": "/api/papers/paper-1/agent-context/sync",
        "json": {"conversation_id": "conversation-1", "query": None},
    }
    assert '"mode": "unchanged"' in capsys.readouterr().out


def test_paper_agent_context_sync_is_incremental_and_thread_bound(
    client: TestClient,
) -> None:
    paper_id = _create_paper(client)["id"]
    with SessionLocal() as session:
        conversation = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="论文问答",
            codex_session_id="thread-incremental",
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    endpoint = f"/api/papers/{paper_id}/agent-context/sync"
    payload = {"conversation_id": conversation_id}

    first = client.post(endpoint, json=payload)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["sync"]["mode"] == "full"
    assert set(first_body["context"]) == set(first_body["sync"]["changed_sections"])
    assert "conversation" not in first_body["context"]

    unchanged = client.post(endpoint, json=payload)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["sync"]["mode"] == "unchanged"
    assert unchanged.json()["context"] == {}
    assert len(unchanged.content) < 400
    with SessionLocal() as session:
        assert (
            paper_agent_context_sync_required(
                session,
                session.get_one(Paper, paper_id),
                session.get_one(Conversation, conversation_id),
            )
            is False
        )

    with SessionLocal() as session:
        session.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content="这条消息已经存在于 Codex thread，不应再次同步。",
            )
        )
        session.commit()

    after_message = client.post(endpoint, json=payload)
    assert after_message.status_code == 200, after_message.text
    assert after_message.json()["sync"]["mode"] == "unchanged"

    with SessionLocal() as session:
        session.add(
            ReadingProgress(
                paper_id=paper_id,
                page_number=4,
                scroll_offset=0.6,
                zoom=1.2,
            )
        )
        session.commit()

    after_scroll = client.post(endpoint, json=payload)
    assert after_scroll.status_code == 200, after_scroll.text
    assert after_scroll.json()["sync"]["mode"] == "unchanged"

    with SessionLocal() as session:
        paper = session.get_one(Paper, paper_id)
        paper.problem = "增量更新后的研究问题"
        session.commit()
        assert (
            paper_agent_context_sync_required(
                session,
                paper,
                session.get_one(Conversation, conversation_id),
            )
            is True
        )

    paper_delta = client.post(endpoint, json=payload)
    assert paper_delta.status_code == 200, paper_delta.text
    assert paper_delta.json()["sync"]["mode"] == "delta"
    assert paper_delta.json()["sync"]["changed_sections"] == ["paper"]
    assert paper_delta.json()["context"]["paper"]["problem"] == "增量更新后的研究问题"

    with SessionLocal() as session:
        session.add(
            Annotation(
                paper_id=paper_id,
                kind="note",
                page_number=2,
                selected_text="incremental evidence",
                content="只同步 annotations 分区。",
            )
        )
        session.commit()

    annotation_delta = client.post(endpoint, json=payload)
    assert annotation_delta.status_code == 200, annotation_delta.text
    assert annotation_delta.json()["sync"]["changed_sections"] == ["annotations"]

    with SessionLocal() as session:
        conversation = session.get_one(Conversation, conversation_id)
        conversation.codex_session_id = "thread-recovered"
        session.commit()

    recovered = client.post(endpoint, json=payload)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["sync"]["mode"] == "full"
    with SessionLocal() as session:
        cursor = session.get_one(PaperAgentContextSync, conversation_id)
        assert cursor.codex_session_id == "thread-recovered"


def test_streamed_paper_turn_skips_sync_tool_when_context_is_current(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations

    paper_id = _create_paper(client)["id"]
    with SessionLocal() as session:
        conversation = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="论文问答",
            codex_session_id="thread-current",
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    synced = client.post(
        f"/api/papers/{paper_id}/agent-context/sync",
        json={"conversation_id": conversation_id},
    )
    assert synced.status_code == 200, synced.text
    captured: dict[str, str] = {}

    def fake_stream(prompt: str, **_kwargs):
        captured["prompt"] = prompt
        yield {"type": "thread", "thread_id": "thread-current"}
        yield {"type": "delta", "delta": "继续回答"}

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)
    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "继续解释这个结论"},
    )
    assert response.status_code == 200, response.text
    command = (
        f"frontier paper agent-context {paper_id} --conversation-id {conversation_id}"
    )
    assert f"Do not call `{command}` in this turn" in captured["prompt"]


def test_paper_agent_context_applies_hard_analysis_and_annotation_budgets(
    client: TestClient,
) -> None:
    paper_id = _create_paper(client)["id"]
    with SessionLocal() as session:
        paper = session.get_one(Paper, paper_id)
        paper.full_analysis = {
            "claims": [
                {"claim": f"claim-{index}-" + "x" * 1_000, "page": index + 1}
                for index in range(40)
            ]
        }
        session.add_all(
            [
                Annotation(
                    paper_id=paper_id,
                    kind="note",
                    page_number=index + 1,
                    selected_text="s" * 3_000,
                    content="c" * 3_000,
                )
                for index in range(12)
            ]
        )
        session.commit()

    response = client.get(f"/api/papers/{paper_id}/agent-context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["context_limits"]["full_analysis"]["truncated"] is True
    assert body["context_limits"]["annotations"]["truncated"] is True
    assert len(json.dumps(body["full_analysis"], ensure_ascii=False)) <= 24_100
    assert len(json.dumps(body["annotations"], ensure_ascii=False)) <= 16_100
    assert len(body["annotations"]) < 12
