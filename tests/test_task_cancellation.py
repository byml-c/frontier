from __future__ import annotations

import threading
import time

import pytest

from frontier import interactive_stream
from frontier.database import SessionLocal
from frontier.models import Message, TaskRun
from frontier.services import agent_runtime


def test_cancel_conversation_task_persists_partial_response(client, monkeypatch) -> None:
    from frontier.services import task_control

    monkeypatch.setattr(task_control, "cancel_codex_run", lambda _task_id: True)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Cancellation"}
    ).json()
    with SessionLocal() as session:
        task = TaskRun(
            kind="global_chat",
            status="running",
            payload_json={"conversation_id": conversation["id"]},
            result_json={"response": "部分回答"},
        )
        session.add(task)
        session.flush()
        session.add(
            Message(
                conversation_id=conversation["id"],
                role="user",
                content="发错的消息",
                context_json={"task_id": task.id},
            )
        )
        session.add(
            Message(
                conversation_id=conversation["id"],
                role="assistant",
                content="部分回答",
                context_json={
                    "task_id": task.id,
                    "stream_status": "running",
                    "activities": [
                        {
                            "id": "tool-1",
                            "kind": "tool",
                            "label": "正在读取论文",
                            "status": "running",
                        }
                    ],
                },
            )
        )
        session.commit()
        task_id = task.id

    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["process_stopped"] is True

    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assistant = detail["messages"][-1]
    assert assistant["content"] == "部分回答"
    assert assistant["context_json"]["stream_status"] == "cancelled"
    assert assistant["context_json"]["activities"][0]["status"] == "cancelled"
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["result_json"]["stream_status"] == "cancelled"
    user = next(message for message in detail["messages"] if message["role"] == "user")
    assert user["context_json"]["turn_cancelled"] is True

    repeated = client.post(f"/api/tasks/{task_id}/cancel")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"


def test_cancel_rejects_completed_task(client) -> None:
    with SessionLocal() as session:
        task = TaskRun(kind="global_chat", status="completed", payload_json={})
        session.add(task)
        session.commit()
        task_id = task.id

    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 409


def test_cancelled_turn_is_excluded_when_a_new_thread_is_rebuilt(client) -> None:
    from frontier.agent_runner import build_conversation_recovery_prompt

    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Recovery"}
    ).json()
    with SessionLocal() as session:
        session.add_all(
            [
                Message(
                    conversation_id=conversation["id"],
                    role="user",
                    content="需要保留的问题",
                    context_json={"path": "/graph", "task_id": "kept"},
                ),
                Message(
                    conversation_id=conversation["id"],
                    role="user",
                    content="发错的问题",
                    context_json={"task_id": "cancelled", "turn_cancelled": True},
                ),
            ]
        )
        session.commit()
        prompt = build_conversation_recovery_prompt(
            session,
            conversation["id"],
            "当前请求",
        )

    assert "需要保留的问题" in prompt
    assert '"path": "/graph"' in prompt
    assert "发错的问题" not in prompt
    assert '"task_id"' not in prompt


def test_process_registry_handles_cancel_before_process_attach(monkeypatch) -> None:
    from frontier import interactive_stream

    stopped: list[object] = []
    process = object()
    monkeypatch.setattr(
        interactive_stream,
        "_stop_process",
        lambda value: stopped.append(value) if value is not None else None,
    )

    assert interactive_stream.cancel_codex_run("early-cancel") is False
    with pytest.raises(interactive_stream.CodexRunCancelled):
        interactive_stream.register_codex_run("early-cancel", process)  # type: ignore[arg-type]
    interactive_stream.unregister_codex_run("early-cancel", process)  # type: ignore[arg-type]
    assert stopped == [process]


def test_process_registry_stops_only_the_target_run(monkeypatch) -> None:
    from frontier import interactive_stream

    stopped: list[object] = []
    first = object()
    second = object()
    monkeypatch.setattr(interactive_stream, "_stop_process", stopped.append)
    interactive_stream.register_codex_run("first-run", first)  # type: ignore[arg-type]
    interactive_stream.register_codex_run("second-run", second)  # type: ignore[arg-type]

    assert interactive_stream.cancel_codex_run("first-run") is True
    assert stopped == [first]
    interactive_stream.unregister_codex_run("first-run", first)  # type: ignore[arg-type]
    interactive_stream.unregister_codex_run("second-run", second)  # type: ignore[arg-type]


def test_stream_cancel_wakes_root_slot_waiter_before_worker_start(monkeypatch) -> None:
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(agent_runtime, "_ROOT_AGENT_SLOTS", slots)
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            list(
                interactive_stream.stream_codex_prompt(
                    "queued",
                    run_id="queued-before-root-slot",
                    timeout_seconds=5,
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    waiter = threading.Thread(target=consume)
    waiter.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with interactive_stream._RUNS_LOCK:
            registered = "queued-before-root-slot" in interactive_stream._ACTIVE_RUNS
        if registered:
            break
        time.sleep(0.005)
    else:
        pytest.fail("run did not register before waiting for the root slot")

    assert interactive_stream.cancel_codex_run("queued-before-root-slot") is True
    waiter.join(timeout=1)
    slots.release()

    assert not waiter.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], interactive_stream.CodexRunCancelled)
