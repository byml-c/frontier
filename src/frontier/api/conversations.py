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
from ..services.architecture_scene import release_agent_lock_for_task
from ..services.common import model_dict
from ..services.paper_conversations import get_or_create_paper_conversation
from ..services.task_control import active_task_for_conversation, cancelled_activities

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
                "failed"
                if failed and activity["status"] == "running"
                else "completed"
                if activity["status"] == "running"
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


def _runtime_usage_payload(
    runtime: dict[str, str] | None,
    usage: dict[str, int] | None,
) -> dict[str, dict]:
    payload: dict[str, dict] = {}
    if runtime:
        payload["runtime"] = dict(runtime)
    if usage:
        payload["usage"] = dict(usage)
    return payload


def _persist_cancelled_turn(
    task: TaskRun | None,
    assistant: Message | None,
    *,
    task_id: str,
    content: str,
    activities: list[dict],
    runtime: dict[str, str],
    usage: dict[str, int],
) -> None:
    """Keep the last observable runtime snapshot when cancellation wins a race."""
    payload = {
        "response": content,
        "activities": activities,
        "stream_status": "cancelled",
        **_runtime_usage_payload(runtime, usage),
    }
    if task:
        task.result_json = payload
    if assistant:
        assistant.content = content
        assistant.context_json = {
            **(assistant.context_json or {}),
            "task_id": task_id,
            "stream_status": "cancelled",
            "activities": activities,
            **_runtime_usage_payload(runtime, usage),
        }


def _persist_stream_snapshot(
    task_id: str,
    assistant_id: str,
    *,
    content: str,
    activities: list[dict],
    stream_status: str,
    thread_id: str | None = None,
    error: str | None = None,
    runtime: dict[str, str] | None = None,
    usage: dict[str, int] | None = None,
) -> None:
    """Persist enough state for a conversation turn to survive a browser refresh."""
    snapshot = {
        "response": content,
        "activities": activities,
        "stream_status": stream_status,
        **_runtime_usage_payload(runtime, usage),
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
                **_runtime_usage_payload(runtime, usage),
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
                **_runtime_usage_payload(
                    context.get("runtime") if isinstance(context.get("runtime"), dict) else None,
                    context.get("usage") if isinstance(context.get("usage"), dict) else None,
                ),
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
        if session.get(Paper, payload.reference_id) is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        item = get_or_create_paper_conversation(
            session,
            payload.reference_id,
            title=payload.title,
        )
        session.commit()
        return _serialize(session, item)
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
    if active_task_for_conversation(session, conversation.id):
        raise HTTPException(status_code=409, detail="该对话的 Agent 正在处理另一项任务")
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
    if active_task_for_conversation(session, conversation.id):
        raise HTTPException(status_code=409, detail="该对话的 Agent 正在处理另一项任务")
    task_kind = "paper_chat" if conversation.kind == "paper" else "global_chat"
    message: Message | None = None
    assistant: Message
    initial_content = ""
    assistant_context_base: dict = {}
    if payload.retry_assistant_id:
        assistant = session.get(Message, payload.retry_assistant_id)
        latest_assistant = session.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        if (
            not assistant
            or assistant.conversation_id != conversation.id
            or assistant.role != "assistant"
        ):
            raise HTTPException(status_code=404, detail="未找到需要继续的回答")
        if not latest_assistant or latest_assistant.id != assistant.id:
            raise HTTPException(status_code=409, detail="只能继续最后一条未完成回答")
        previous_context = dict(assistant.context_json or {})
        previous_status = previous_context.get("stream_status")
        if previous_status not in {"failed", "interrupted"}:
            raise HTTPException(status_code=409, detail="这条回答当前不能继续")
        previous_task_id = previous_context.get("task_id")
        previous_task = (
            session.get(TaskRun, previous_task_id)
            if isinstance(previous_task_id, str)
            else None
        )
        initial_content = assistant.content.rstrip()
        previous_activities = previous_context.get("activities")
        activity_summary = "\n".join(
            f"- [{item.get('status', 'unknown')}] {item.get('label', '')}"
            + (f": {item.get('detail')}" if item.get("detail") else "")
            for item in (
                previous_activities if isinstance(previous_activities, list) else []
            )
            if isinstance(item, dict) and item.get("label")
        )[-6_000:]
        continuation_request = (
            "Continue the previous unfinished answer from exactly where the persisted partial "
            "response stopped. Do not repeat the existing response or redo completed tool calls "
            "unless verification is necessary. Finish the original request in concise Simplified "
            "Chinese.\n\nPersisted partial response:\n"
            f"{initial_content[-12_000:] or '（尚未生成正文）'}\n\n"
            "Persisted activity log (it may be incomplete):\n"
            f"{activity_summary or '（没有已保存的工具活动）'}"
        )
        context = dict((previous_task.payload_json or {}).get("context", {})) if previous_task else {}
        task_payload = {
            "conversation_id": conversation.id,
            "message": continuation_request,
            "context": context,
            "retry_of": previous_task_id,
            "retry_assistant_id": assistant.id,
        }
        retry_history = previous_context.get("retry_history")
        assistant_context_base = {
            "retry_history": [
                *(
                    [item for item in retry_history if isinstance(item, dict)]
                    if isinstance(retry_history, list)
                    else []
                ),
                {
                    "task_id": previous_task_id,
                    "stream_status": previous_status,
                    "stream_error": previous_context.get("stream_error"),
                },
            ],
            "retry_of": previous_task_id,
        }
    else:
        if not payload.content.strip():
            raise HTTPException(status_code=422, detail="消息不能为空")
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
    if message:
        message.context_json = {**context, "task_id": task.id}
    session_id = conversation.codex_session_id
    paper_id = conversation.reference_id if conversation.kind == "paper" else None
    prompt = build_prompt(task)
    recovery_prompt = build_conversation_recovery_prompt(
        session,
        conversation.id,
        prompt,
        exclude_message_id=message.id if message else None,
    )
    if not payload.retry_assistant_id:
        assistant = Message(
            conversation_id=conversation_id,
            role="assistant",
            content="",
            context_json={},
        )
        session.add(assistant)
    assistant.context_json = {
        **assistant_context_base,
        "task_id": task.id,
        "stream_status": "queued",
        "activities": [],
    }
    session.commit()
    assistant_id = assistant.id

    def generate():  # type: ignore[no-untyped-def]
        activity_log: list[dict] = []
        content = initial_content
        continuation_started = False
        thread_id = session_id
        last_snapshot_at = 0.0
        last_snapshot_length = 0
        terminal = False
        runtime: dict[str, str] = {}
        usage: dict[str, int] = {}

        def persist_snapshot(
            stream_status: str = "running",
            *,
            force: bool = False,
            error: str | None = None,
            activities: list[dict] | None = None,
        ) -> None:
            nonlocal last_snapshot_at, last_snapshot_length
            now = time.monotonic()
            if (
                not force
                and now - last_snapshot_at < 0.35
                and len(content) - last_snapshot_length < 384
            ):
                return
            _persist_stream_snapshot(
                task.id,
                assistant_id,
                content=content,
                activities=activity_log if activities is None else activities,
                stream_status=stream_status,
                thread_id=thread_id,
                error=error,
                runtime=runtime,
                usage=usage,
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
                    normalized["status"] != "running" or normalized["kind"] in {"tool", "status"}
                )
            )
            return encode_stream_event("activity", **event)

        meta = {"task_id": task.id, "retry_assistant_id": payload.retry_assistant_id}
        if message:
            meta["message"] = model_dict(message)
        yield encode_stream_event("meta", **meta)
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
                elif event["type"] == "runtime":
                    runtime = {
                        key: value
                        for key, value in event.items()
                        if key in {"model", "model_provider", "reasoning_effort"}
                        and isinstance(value, str)
                    }
                    persist_snapshot(force=True)
                    yield encode_stream_event("runtime", **runtime)
                elif event["type"] == "usage":
                    usage = {
                        key: value
                        for key, value in event.items()
                        if key in {"current_tokens", "context_window", "total_tokens"}
                        and isinstance(value, int)
                        and not isinstance(value, bool)
                    }
                    persist_snapshot(force=True)
                    yield encode_stream_event("usage", **usage)
                elif event["type"] == "delta":
                    delta = event["delta"]
                    if initial_content and not continuation_started:
                        if not content.endswith("\n") and not delta.startswith("\n"):
                            content += "\n\n"
                        continuation_started = True
                    content += delta
                    persist_snapshot()
                    yield encode_stream_event("delta", delta=delta)
                elif event["type"] == "activity":
                    yield activity_event(
                        **{key: value for key, value in event.items() if key != "type"}
                    )
                elif event["type"] in {
                    "architecture_command",
                    "architecture_scene_updated",
                }:
                    yield encode_stream_event(
                        event["type"],
                        **{key: value for key, value in event.items() if key != "type"},
                    )
            with SessionLocal() as runner_session:
                current_conversation = runner_session.get(Conversation, conversation_id)
                running = runner_session.get(TaskRun, task.id)
                assistant = runner_session.get(Message, assistant_id)
                if running and running.status == "cancelled":
                    _persist_cancelled_turn(
                        running,
                        assistant,
                        task_id=task.id,
                        content=content,
                        activities=cancelled_activities(activity_log),
                        runtime=runtime,
                        usage=usage,
                    )
                    runner_session.commit()
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
                            **_runtime_usage_payload(runtime, usage),
                        },
                        finished_at=utcnow(),
                    )
                )
                if transition.rowcount != 1:
                    runner_session.rollback()
                    current = runner_session.get(TaskRun, task.id)
                    if current and current.status == "cancelled":
                        _persist_cancelled_turn(
                            current,
                            runner_session.get(Message, assistant_id),
                            task_id=task.id,
                            content=content,
                            activities=cancelled_activities(activity_log),
                            runtime=runtime,
                            usage=usage,
                        )
                        runner_session.commit()
                        terminal = True
                        yield encode_stream_event("cancelled", task_id=task.id)
                        return
                    raise RuntimeError("Agent 任务状态已变化，未覆盖其终态")
                assistant.content = content
                assistant.citations_json = _parse_citations(content, paper_id)
                assistant.context_json = {
                    **(assistant.context_json or {}),
                    "task_id": task.id,
                    "stream_status": "completed",
                    "activities": completed_activities,
                    **_runtime_usage_payload(runtime, usage),
                }
                if current_conversation:
                    current_conversation.codex_session_id = thread_id
                release_agent_lock_for_task(runner_session, task.id)
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
                    _persist_cancelled_turn(
                        running,
                        cancelled_assistant,
                        task_id=task.id,
                        content=content,
                        activities=cancelled_activity_log,
                        runtime=runtime,
                        usage=usage,
                    )
                    release_agent_lock_for_task(runner_session, task.id)
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
                        **_runtime_usage_payload(runtime, usage),
                    }
                    running.finished_at = utcnow()
                failed_assistant = runner_session.get(Message, assistant_id)
                if failed_assistant and not cancelled:
                    failed_assistant.content = content
                    failed_assistant.context_json = {
                        **(failed_assistant.context_json or {}),
                        "task_id": task.id,
                        "stream_status": "failed",
                        "stream_error": str(exc),
                        "activities": failed_activities,
                        **_runtime_usage_payload(runtime, usage),
                    }
                release_agent_lock_for_task(runner_session, task.id)
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
                    if cancelled:
                        _persist_cancelled_turn(
                            running,
                            runner_session.get(Message, assistant_id),
                            task_id=task.id,
                            content=content,
                            activities=cancelled_activities(activity_log),
                            runtime=runtime,
                            usage=usage,
                        )
                    elif running and running.status in {"queued", "running"}:
                        running.status = "failed"
                        running.error = interruption
                        running.result_json = {
                            "response": content,
                            "activities": interrupted_activities,
                            "stream_status": "interrupted",
                            **_runtime_usage_payload(runtime, usage),
                        }
                        running.finished_at = utcnow()
                    interrupted_assistant = runner_session.get(Message, assistant_id)
                    if interrupted_assistant and not cancelled:
                        interrupted_assistant.content = content
                        interrupted_assistant.context_json = {
                            **(interrupted_assistant.context_json or {}),
                            "task_id": task.id,
                            "stream_status": "interrupted",
                            "stream_error": interruption,
                            "activities": interrupted_activities,
                            **_runtime_usage_payload(runtime, usage),
                        }
                    release_agent_lock_for_task(runner_session, task.id)
                    runner_session.commit()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
