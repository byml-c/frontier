from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..agent_runner import (
    _parse_citations,
    build_conversation_recovery_prompt,
    build_prompt,
    enqueue_task,
    run_task,
)
from ..config import settings
from ..database import SessionLocal, get_session
from ..interactive_stream import encode_stream_event, stream_codex_prompt
from ..models import Conversation, Message, Paper, TaskRun, utcnow
from ..schemas import ConversationCreate, MessageCreate
from ..services.common import model_dict
from ..services.task_control import cancelled_activities

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conversation_or_404(session: Session, conversation_id: str) -> Conversation:
    item = session.get(Conversation, conversation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return item


def _serialize(session: Session, conversation: Conversation) -> dict:
    payload = model_dict(conversation)
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
    ).all()
    payload["messages"] = [model_dict(message) for message in messages]
    return payload


def _completed_activities(activity_log: list[dict], *, failed: bool = False) -> list[dict]:
    """Return a durable public activity snapshot without stale running states."""
    return [
        {
            **activity,
            "status": (
                "failed" if failed and activity["status"] == "running"
                else "completed" if activity["status"] == "running"
                else activity["status"]
            ),
        }
        for activity in activity_log
    ]


def _failed_activities(activity_log: list[dict], detail: str) -> list[dict]:
    activities = _completed_activities(activity_log, failed=True)
    if not any(activity["status"] == "failed" for activity in activities):
        activities.append(
            {
                "id": "agent-stream",
                "kind": "status",
                "label": "Agent 响应未完成",
                "detail": detail,
                "status": "failed",
            }
        )
    return activities


def _persist_stream_snapshot(
    task_id: str,
    assistant_id: str,
    *,
    content: str,
    activities: list[dict],
    stream_status: str,
    thread_id: str | None = None,
    error: str | None = None,
) -> None:
    """Persist enough state for a conversation turn to survive a browser refresh."""
    snapshot = {
        "response": content,
        "activities": activities,
        "stream_status": stream_status,
    }
    with SessionLocal() as snapshot_session:
        task = snapshot_session.get(TaskRun, task_id)
        assistant = snapshot_session.get(Message, assistant_id)
        if task and task.status == "cancelled":
            return
        if task:
            task.result_json = snapshot
            if thread_id:
                task.codex_session_id = thread_id
            if error:
                task.error = error
        if assistant:
            assistant.content = content
            assistant.context_json = {
                **(assistant.context_json or {}),
                "task_id": task_id,
                "stream_status": stream_status,
                "activities": activities,
                **({"stream_error": error} if error else {}),
            }
        snapshot_session.commit()


def mark_orphaned_conversation_streams() -> int:
    """Close conversation drafts whose request-owned runner vanished on restart."""
    interruption = "服务在 Agent 回复完成前重启；已保留此前生成的内容和调用记录。"
    updated = 0
    with SessionLocal() as session:
        messages = session.scalars(select(Message).where(Message.role == "assistant")).all()
        for message in messages:
            context = message.context_json or {}
            if context.get("stream_status") not in {"queued", "running"}:
                continue
            task_id = context.get("task_id")
            task = session.get(TaskRun, task_id) if isinstance(task_id, str) else None
            if not task or task.status not in {"queued", "running"}:
                continue
            raw_activities = context.get("activities")
            activities = (
                [item for item in raw_activities if isinstance(item, dict) and item.get("status")]
                if isinstance(raw_activities, list)
                else []
            )
            failed_activities = _failed_activities(activities, interruption)
            message.context_json = {
                **context,
                "stream_status": "interrupted",
                "stream_error": interruption,
                "activities": failed_activities,
            }
            task.status = "failed"
            task.error = interruption
            task.finished_at = utcnow()
            task.result_json = {
                "response": message.content,
                "activities": failed_activities,
                "stream_status": "interrupted",
            }
            updated += 1
        if updated:
            session.commit()
    return updated


@router.get("")
def list_conversations(
    session: Annotated[Session, Depends(get_session)],
    kind: str | None = None,
    reference_id: str | None = None,
) -> list[dict]:
    statement = select(Conversation).order_by(Conversation.updated_at.desc())
    if kind:
        statement = statement.where(Conversation.kind == kind)
    if reference_id:
        statement = statement.where(Conversation.reference_id == reference_id)
    return [model_dict(item) for item in session.scalars(statement)]


@router.post("")
def create_conversation(
    payload: ConversationCreate, session: Annotated[Session, Depends(get_session)]
) -> dict:
    if payload.kind == "paper" and payload.reference_id:
        existing = session.scalar(
            select(Conversation).where(
                Conversation.kind == "paper",
                Conversation.reference_id == payload.reference_id,
                Conversation.status == "active",
            )
        )
        if existing:
            return _serialize(session, existing)
        if session.get(Paper, payload.reference_id) is None:
            raise HTTPException(status_code=404, detail="Paper not found")
    item = Conversation(**payload.model_dump())
    session.add(item)
    session.commit()
    return _serialize(session, item)


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str, session: Annotated[Session, Depends(get_session)]
) -> dict:
    return _serialize(session, _conversation_or_404(session, conversation_id))


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    conversation = _conversation_or_404(session, conversation_id)
    context = dict(payload.context_json)
    if payload.selected_text:
        context["selected_text"] = payload.selected_text
    if payload.page_number:
        context["page_number"] = payload.page_number
    message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        context_json=context,
    )
    session.add(message)
    session.commit()
    task_kind = "paper_chat" if conversation.kind == "paper" else "global_chat"
    task_payload = {
        "conversation_id": conversation.id,
        "message": payload.content,
        "context": context,
    }
    if conversation.kind == "paper":
        task_payload["paper_id"] = conversation.reference_id
    task_id = enqueue_task(task_kind, task_payload)
    message.context_json = {**context, "task_id": task_id}
    session.commit()
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, task_id)
    return {"message": model_dict(message), "task_id": task_id, "status": "queued"}


@router.post("/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: str,
    payload: MessageCreate,
    session: Annotated[Session, Depends(get_session)],
) -> StreamingResponse:
    conversation = _conversation_or_404(session, conversation_id)
    context = dict(payload.context_json)
    if payload.selected_text:
        context["selected_text"] = payload.selected_text
    if payload.page_number:
        context["page_number"] = payload.page_number
    message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        context_json=context,
    )
    session.add(message)
    session.flush()
    task_kind = "paper_chat" if conversation.kind == "paper" else "global_chat"
    task_payload = {
        "conversation_id": conversation.id,
        "message": payload.content,
        "context": context,
    }
    if conversation.kind == "paper":
        task_payload["paper_id"] = conversation.reference_id
    task = TaskRun(kind=task_kind, payload_json=task_payload)
    session.add(task)
    session.flush()
    message.context_json = {**context, "task_id": task.id}
    session_id = conversation.codex_session_id
    paper_id = conversation.reference_id if conversation.kind == "paper" else None
    prompt = build_prompt(task)
    recovery_prompt = build_conversation_recovery_prompt(
        session,
        conversation.id,
        prompt,
        exclude_message_id=message.id,
    )
    assistant = Message(
        conversation_id=conversation_id,
        role="assistant",
        content="",
        context_json={
            "task_id": task.id,
            "stream_status": "queued",
            "activities": [],
        },
    )
    session.add(assistant)
    session.commit()
    assistant_id = assistant.id

    def generate():  # type: ignore[no-untyped-def]
        activity_log: list[dict] = []
        content = ""
        thread_id = session_id
        last_snapshot_at = 0.0
        last_snapshot_length = 0
        terminal = False

        def persist_snapshot(
            stream_status: str = "running",
            *,
            force: bool = False,
            error: str | None = None,
            activities: list[dict] | None = None,
        ) -> None:
            nonlocal last_snapshot_at, last_snapshot_length
            now = time.monotonic()
            if not force and now - last_snapshot_at < 0.35 and len(content) - last_snapshot_length < 384:
                return
            _persist_stream_snapshot(
                task.id,
                assistant_id,
                content=content,
                activities=activity_log if activities is None else activities,
                stream_status=stream_status,
                thread_id=thread_id,
                error=error,
            )
            last_snapshot_at = now
            last_snapshot_length = len(content)

        def activity_event(**event: object) -> str:
            normalized = {
                "id": str(event.get("activity_id") or f"activity-{len(activity_log)}"),
                "kind": str(event.get("kind") or "status"),
                "label": str(event.get("label") or "Agent 正在处理"),
                "status": str(event.get("status") or "running"),
            }
            if event.get("detail"):
                normalized["detail"] = str(event["detail"])
            index = next(
                (i for i, item in enumerate(activity_log) if item["id"] == normalized["id"]),
                None,
            )
            if index is None:
                activity_log.append(normalized)
            else:
                activity_log[index] = normalized
            persist_snapshot(
                force=(
                    normalized["status"] != "running"
                    or normalized["kind"] in {"tool", "status"}
                )
            )
            return encode_stream_event("activity", **event)

        yield encode_stream_event("meta", task_id=task.id, message=model_dict(message))
        yield activity_event(
            activity_id="agent-startup",
            kind="status",
            label="正在启动 Agent",
            status="running",
        )
        try:
            if not settings.enable_codex_runs:
                raise RuntimeError("Agent 运行已在服务器设置中停用")
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running and running.status == "cancelled":
                    raise RuntimeError("Agent 工作流已由用户中断")
                if running:
                    running.status = "running"
                    running.started_at = utcnow()
                    runner_session.commit()
            for event in stream_codex_prompt(
                recovery_prompt if session_id is None else prompt,
                session_id=session_id,
                recovery_prompt=recovery_prompt,
                run_id=task.id,
            ):
                if event["type"] == "thread":
                    thread_id = event["thread_id"]
                    with SessionLocal() as runner_session:
                        current_conversation = runner_session.get(Conversation, conversation_id)
                        running = runner_session.get(TaskRun, task.id)
                        if current_conversation:
                            current_conversation.codex_session_id = thread_id
                        if running:
                            running.codex_session_id = thread_id
                        runner_session.commit()
                    yield activity_event(
                        activity_id="agent-startup",
                        kind="status",
                        label="Agent 已连接",
                        status="completed",
                    )
                elif event["type"] == "delta":
                    content += event["delta"]
                    persist_snapshot()
                    yield encode_stream_event("delta", delta=event["delta"])
                elif event["type"] == "activity":
                    yield activity_event(
                        **{key: value for key, value in event.items() if key != "type"}
                    )
            with SessionLocal() as runner_session:
                current_conversation = runner_session.get(Conversation, conversation_id)
                running = runner_session.get(TaskRun, task.id)
                assistant = runner_session.get(Message, assistant_id)
                if running and running.status == "cancelled":
                    terminal = True
                    yield encode_stream_event("cancelled", task_id=task.id)
                    return
                completed_activities = _completed_activities(activity_log)
                if not assistant:
                    raise RuntimeError("Agent 回复草稿不存在")
                transition = runner_session.execute(
                    update(TaskRun)
                    .where(TaskRun.id == task.id, TaskRun.status == "running")
                    .values(
                        status="completed",
                        codex_session_id=thread_id,
                        result_json={
                            "response": content,
                            "activities": completed_activities,
                            "stream_status": "completed",
                        },
                        finished_at=utcnow(),
                    )
                )
                if transition.rowcount != 1:
                    runner_session.rollback()
                    current = runner_session.get(TaskRun, task.id)
                    if current and current.status == "cancelled":
                        terminal = True
                        yield encode_stream_event("cancelled", task_id=task.id)
                        return
                    raise RuntimeError("Agent 任务状态已变化，未覆盖其终态")
                assistant.content = content
                assistant.citations_json = _parse_citations(content, paper_id)
                assistant.context_json = {
                    "task_id": task.id,
                    "stream_status": "completed",
                    "activities": completed_activities,
                }
                if current_conversation:
                    current_conversation.codex_session_id = thread_id
                runner_session.commit()
                assistant_payload = model_dict(assistant)
            terminal = True
            yield encode_stream_event("done", message=assistant_payload)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                cancelled = bool(running and running.status == "cancelled")
                if cancelled:
                    cancelled_activity_log = cancelled_activities(activity_log)
                    cancelled_assistant = runner_session.get(Message, assistant_id)
                    if cancelled_assistant:
                        cancelled_assistant.content = content
                        cancelled_assistant.context_json = {
                            **(cancelled_assistant.context_json or {}),
                            "task_id": task.id,
                            "stream_status": "cancelled",
                            "activities": cancelled_activity_log,
                        }
                    if running:
                        running.result_json = {
                            "response": content,
                            "activities": cancelled_activity_log,
                            "stream_status": "cancelled",
                        }
                    runner_session.commit()
                else:
                    failed_activities = _failed_activities(activity_log, str(exc))
                if running and not cancelled:
                    running.status = "failed"
                    running.error = str(exc)
                    running.result_json = {
                        "response": content,
                        "activities": failed_activities,
                        "stream_status": "failed",
                    }
                    running.finished_at = utcnow()
                failed_assistant = runner_session.get(Message, assistant_id)
                if failed_assistant and not cancelled:
                    failed_assistant.content = content
                    failed_assistant.context_json = {
                        "task_id": task.id,
                        "stream_status": "failed",
                        "stream_error": str(exc),
                        "activities": failed_activities,
                    }
                runner_session.commit()
            terminal = True
            if cancelled:
                yield encode_stream_event("cancelled", task_id=task.id)
            else:
                yield encode_stream_event("error", message=str(exc))
        finally:
            if not terminal:
                interruption = "浏览器连接已中断；已保留此前生成的内容和调用记录。"
                interrupted_activities = _failed_activities(activity_log, interruption)
                with SessionLocal() as runner_session:
                    running = runner_session.get(TaskRun, task.id)
                    cancelled = bool(running and running.status == "cancelled")
                    if running and not cancelled and running.status in {"queued", "running"}:
                        running.status = "failed"
                        running.error = interruption
                        running.result_json = {
                            "response": content,
                            "activities": interrupted_activities,
                            "stream_status": "interrupted",
                        }
                        running.finished_at = utcnow()
                    interrupted_assistant = runner_session.get(Message, assistant_id)
                    if interrupted_assistant and not cancelled:
                        interrupted_assistant.content = content
                        interrupted_assistant.context_json = {
                            "task_id": task.id,
                            "stream_status": "interrupted",
                            "stream_error": interruption,
                            "activities": interrupted_activities,
                        }
                    runner_session.commit()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
