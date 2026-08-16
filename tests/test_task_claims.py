from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from frontier.api import conversations, system
from frontier.database import SessionLocal
from frontier.models import Brief, Conversation, Message, TaskRun


def test_conversation_message_claim_is_atomic(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)
    monkeypatch.setattr(conversations, "submit_task", lambda _task_id: True)
    conversation = client.post(
        "/api/conversations",
        json={"kind": "global", "title": f"atomic-{uuid4()}"},
    ).json()
    path = f"/api/conversations/{conversation['id']}/messages"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda index: client.post(path, json={"content": f"message-{index}"}), range(2))
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation["id"],
                Message.role == "user",
            )
        ) == 1
        assert session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.payload_json["conversation_id"].as_string() == conversation["id"]
            )
        ) == 1

    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    successful = next(response for response in responses if response.status_code == 200).json()
    assert detail["active_task"] == {"id": successful["task_id"], "status": "queued"}


def test_codex_disabled_rejects_message_before_mutation(
    client: TestClient,
    monkeypatch,
) -> None:
    conversation = client.post(
        "/api/conversations",
        json={"kind": "global", "title": f"disabled-{uuid4()}"},
    ).json()
    monkeypatch.setattr(conversations.settings, "enable_codex_runs", False)

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "must not be persisted"},
    )
    streamed = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "must not be streamed"},
    )
    assert response.status_code == streamed.status_code == 503
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(Message.id)).where(Message.conversation_id == conversation["id"])
        ) == 0
        assert session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.payload_json["conversation_id"].as_string() == conversation["id"]
            )
        ) == 0


def test_stream_message_preparation_uses_the_same_conversation_claim(
    client: TestClient,
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_stream(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        yield {"type": "delta", "delta": "done"}

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)
    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    conversation = client.post(
        "/api/conversations",
        json={"kind": "global", "title": f"stream-claim-{uuid4()}"},
    ).json()
    path = f"/api/conversations/{conversation['id']}/messages/stream"
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(client.post, path, json={"content": "first"})
        assert started.wait(timeout=5)
        conflict = client.post(path, json={"content": "second"})
        release.set()
        first = first_future.result(timeout=5)

    assert first.status_code == 200
    assert conflict.status_code == 409
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.payload_json["conversation_id"].as_string() == conversation["id"]
            )
        ) == 1


def test_daily_entries_share_one_claim_and_published_brief_is_final(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(system.settings, "enable_codex_runs", True)
    monkeypatch.setattr(system, "submit_task", lambda _task_id: True)
    target = date(2088, 6, 17)
    first = client.post(
        "/api/tasks",
        json={"kind": "daily_brief", "payload_json": {"date": target.isoformat()}},
    )
    second = client.post("/api/tasks/run-daily", params={"target_date": target.isoformat()})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    with SessionLocal() as session:
        task = session.get_one(TaskRun, first.json()["id"])
        task.status = "completed"
        session.add(
            Brief(
                brief_date=target,
                title="Published is authoritative",
                status="published",
                task_id=task.id,
            )
        )
        session.commit()

    blocked = client.post("/api/tasks/run-daily", params={"target_date": target.isoformat()})
    assert blocked.status_code == 409
    with SessionLocal() as session:
        assert session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.kind == "daily_brief",
                TaskRun.payload_json["date"].as_string() == target.isoformat(),
            )
        ) == 1


def test_codex_disabled_rejects_task_mutations_before_queueing(
    client: TestClient,
    monkeypatch,
) -> None:
    with SessionLocal() as session:
        old = TaskRun(kind="graph_maintenance", status="failed")
        session.add(old)
        session.commit()
        old_id = old.id
        before = session.scalar(select(func.count(TaskRun.id)))
    monkeypatch.setattr(system.settings, "enable_codex_runs", False)

    created = client.post(
        "/api/tasks",
        json={"kind": "graph_maintenance", "payload_json": {}},
    )
    daily = client.post(
        "/api/tasks/run-daily",
        params={"target_date": "2087-03-04"},
    )
    retried = client.post(f"/api/tasks/{old_id}/retry")
    assert {created.status_code, daily.status_code, retried.status_code} == {503}
    with SessionLocal() as session:
        assert session.scalar(select(func.count(TaskRun.id))) == before


def test_retry_is_idempotent_and_honors_conversation_claim(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(system.settings, "enable_codex_runs", True)
    monkeypatch.setattr(system, "submit_task", lambda _task_id: True)
    with SessionLocal() as session:
        old = TaskRun(kind="graph_maintenance", status="failed", payload_json={})
        session.add(old)
        session.commit()
        old_id = old.id

    first = client.post(f"/api/tasks/{old_id}/retry")
    second = client.post(f"/api/tasks/{old_id}/retry")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    with SessionLocal() as session:
        conversation = Conversation(kind="global", title=f"retry-conflict-{uuid4()}")
        session.add(conversation)
        session.flush()
        old_chat = TaskRun(
            kind="global_chat",
            status="failed",
            payload_json={"conversation_id": conversation.id},
        )
        active = TaskRun(
            kind="global_chat",
            payload_json={"conversation_id": conversation.id},
        )
        session.add_all([old_chat, active])
        session.commit()
        old_chat_id = old_chat.id

    conflict = client.post(f"/api/tasks/{old_chat_id}/retry")
    assert conflict.status_code == 409
