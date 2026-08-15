from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from frontier.api import system
from frontier.database import SessionLocal
from frontier.models import Conversation, Message, TaskRun


def _create_paper(client: TestClient) -> str:
    response = client.post(
        "/api/papers",
        json={
            "title": f"Finish Reading Main Agent {uuid4()}",
            "abstract": "A paper used to verify main Agent thread reuse.",
            "source": "manual",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]["id"]


def test_finish_reading_task_creates_main_conversation_and_rejects_conflicts(
    client: TestClient,
) -> None:
    paper_id = _create_paper(client)
    payload = {"kind": "finish_reading", "payload_json": {"paper_id": paper_id}}

    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 200, response.text

    with SessionLocal() as session:
        task = session.get_one(TaskRun, response.json()["id"])
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.kind == "paper",
                Conversation.reference_id == paper_id,
                Conversation.status == "active",
            )
        )
        assert conversation is not None
        assert task.payload_json == {
            "paper_id": paper_id,
            "conversation_id": conversation.id,
        }

    queued_conflict = client.post("/api/tasks", json=payload)
    assert queued_conflict.status_code == 409
    assert queued_conflict.json() == {"detail": "该论文的 Agent 正在处理另一项任务"}

    stream_conflict = client.post("/api/tasks/stream", json=payload)
    assert stream_conflict.status_code == 409
    assert stream_conflict.json() == {"detail": "该论文的 Agent 正在处理另一项任务"}


@pytest.mark.parametrize("initial_thread", ["thread-existing", None])
def test_finish_reading_stream_reuses_or_starts_main_thread(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    initial_thread: str | None,
) -> None:
    paper_id = _create_paper(client)
    with SessionLocal() as session:
        conversation = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="论文问答",
            codex_session_id=initial_thread,
        )
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="之前的问题",
                ),
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="之前的回答",
                ),
            ]
        )
        session.commit()
        conversation_id = conversation.id

    captured: dict[str, object] = {}
    returned_thread = f"thread-returned-{uuid4()}"

    def fake_stream(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        yield {"type": "thread", "thread_id": returned_thread}
        yield {"type": "delta", "delta": "精读总结已整理。"}

    monkeypatch.setattr(system.settings, "enable_codex_runs", True)
    monkeypatch.setattr(system, "stream_codex_prompt", fake_stream)

    response = client.post(
        "/api/tasks/stream",
        json={"kind": "finish_reading", "payload_json": {"paper_id": paper_id}},
    )
    assert response.status_code == 200, response.text
    assert '"type": "done"' in response.text
    assert "Prepare the completion summary" in str(captured["prompt"])
    assert captured["session_id"] == initial_thread
    if initial_thread:
        recovery_prompt = str(captured["recovery_prompt"])
        assert "previous Codex thread is unavailable" in recovery_prompt
        assert "之前的问题" in recovery_prompt
        assert "之前的回答" in recovery_prompt
    else:
        assert "recovery_prompt" not in captured
        assert "previous Codex thread is unavailable" not in str(captured["prompt"])

    with SessionLocal() as session:
        conversation = session.get_one(Conversation, conversation_id)
        assert conversation.codex_session_id == returned_thread
        task = session.scalar(
            select(TaskRun)
            .where(TaskRun.payload_json["conversation_id"].as_string() == conversation_id)
            .order_by(TaskRun.created_at.desc())
            .limit(1)
        )
        assert task is not None
        assert task.status == "completed"
        assert task.codex_session_id == returned_thread


@pytest.mark.parametrize("endpoint", ["/api/tasks", "/api/tasks/stream"])
def test_finish_reading_task_requires_an_existing_paper(
    client: TestClient,
    endpoint: str,
) -> None:
    missing_id = client.post(
        endpoint,
        json={"kind": "finish_reading", "payload_json": {}},
    )
    assert missing_id.status_code == 422

    unknown_paper = client.post(
        endpoint,
        json={
            "kind": "finish_reading",
            "payload_json": {"paper_id": str(uuid4())},
        },
    )
    assert unknown_paper.status_code == 404
    assert unknown_paper.json() == {"detail": "Paper not found"}


def test_other_streaming_tasks_keep_their_original_codex_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_stream(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        yield {"type": "thread", "thread_id": "global-thread"}
        yield {"type": "delta", "delta": "完成"}

    monkeypatch.setattr(system.settings, "enable_codex_runs", True)
    monkeypatch.setattr(system, "stream_codex_prompt", fake_stream)

    response = client.post(
        "/api/tasks/stream",
        json={
            "kind": "global_chat",
            "payload_json": {"message": "检查任务", "context": {}},
        },
    )
    assert response.status_code == 200, response.text
    assert "session_id" not in captured
    assert "recovery_prompt" not in captured
    assert isinstance(captured["run_id"], str)
