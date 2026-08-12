from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
    session.commit()
    session_id = conversation.codex_session_id
    paper_id = conversation.reference_id if conversation.kind == "paper" else None
    prompt = build_prompt(task)
    recovery_prompt = build_conversation_recovery_prompt(
        session,
        conversation.id,
        prompt,
        exclude_message_id=message.id,
    )

    def generate():  # type: ignore[no-untyped-def]
        yield encode_stream_event("meta", task_id=task.id, message=model_dict(message))
        yield encode_stream_event(
            "activity",
            activity_id="agent-startup",
            kind="status",
            label="正在启动 Agent",
            status="running",
        )
        content = ""
        thread_id = session_id
        try:
            if not settings.enable_codex_runs:
                raise RuntimeError("Agent 运行已在服务器设置中停用")
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "running"
                    running.started_at = utcnow()
                    runner_session.commit()
            for event in stream_codex_prompt(
                prompt,
                session_id=session_id,
                recovery_prompt=recovery_prompt,
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
                    yield encode_stream_event(
                        "activity",
                        activity_id="agent-startup",
                        kind="status",
                        label="Agent 已连接",
                        status="completed",
                    )
                elif event["type"] == "delta":
                    content += event["delta"]
                    yield encode_stream_event("delta", delta=event["delta"])
                elif event["type"] == "activity":
                    yield encode_stream_event(
                        "activity", **{key: value for key, value in event.items() if key != "type"}
                    )
            with SessionLocal() as runner_session:
                current_conversation = runner_session.get(Conversation, conversation_id)
                running = runner_session.get(TaskRun, task.id)
                assistant = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content,
                    citations_json=_parse_citations(content, paper_id),
                )
                runner_session.add(assistant)
                runner_session.flush()
                if current_conversation:
                    current_conversation.codex_session_id = thread_id
                if running:
                    running.status = "completed"
                    running.codex_session_id = thread_id
                    running.result_json = {"response": content}
                    running.finished_at = utcnow()
                runner_session.commit()
                assistant_payload = model_dict(assistant)
            yield encode_stream_event("done", message=assistant_payload)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "failed"
                    running.error = str(exc)
                    running.finished_at = utcnow()
                    runner_session.commit()
            yield encode_stream_event("error", message=str(exc))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
