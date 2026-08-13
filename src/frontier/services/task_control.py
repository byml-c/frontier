from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.sql import select, update

from ..interactive_stream import cancel_codex_run
from ..models import Conversation, Message, TaskRun, utcnow

ACTIVE_TASK_STATUSES = {"queued", "running"}


def cancelled_activities(values: object) -> list[dict]:
    """Turn durable in-flight activity rows into a neutral cancelled state."""
    if not isinstance(values, list):
        return []
    return [
        {
            **item,
            "status": "cancelled" if item.get("status") == "running" else item.get("status"),
        }
        for item in values
        if isinstance(item, dict) and item.get("status")
    ]


def cancel_task_run(session: Session, task: TaskRun) -> bool | None:
    """Persist user cancellation before terminating its process.

    Persisting first makes cancellation win races with the stream snapshot and
    completion paths. The process registry also remembers cancellation that
    arrives just before Codex has been spawned.
    """
    result = dict(task.result_json or {})
    response = str(result.get("response") or "")
    activities = cancelled_activities(result.get("activities"))
    conversation_id = task.payload_json.get("conversation_id")

    if isinstance(conversation_id, str):
        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
        ).all()
        assistant = next(
            (
                message
                for message in messages
                if (message.context_json or {}).get("task_id") == task.id
            ),
            None,
        )
        if assistant:
            context = dict(assistant.context_json or {})
            response = assistant.content or response
            activities = cancelled_activities(context.get("activities")) or activities
            assistant.context_json = {
                **context,
                "task_id": task.id,
                "stream_status": "cancelled",
                "activities": activities,
            }
            assistant.context_json.pop("stream_error", None)
        user_message = next(
            (
                message
                for message in messages
                if message.role == "user" and (message.context_json or {}).get("task_id") == task.id
            ),
            None,
        )
        if user_message:
            user_message.context_json = {
                **(user_message.context_json or {}),
                "turn_cancelled": True,
            }
        conversation = session.get(Conversation, conversation_id)
        if conversation:
            # A cancelled prompt may already be inside the Codex thread. Force the
            # next turn to rebuild from history, where this turn is explicitly skipped.
            conversation.codex_session_id = None

    result_json = {
        **result,
        "response": response,
        "activities": activities,
        "stream_status": "cancelled",
    }
    transition = session.execute(
        update(TaskRun)
        .where(TaskRun.id == task.id, TaskRun.status.in_(ACTIVE_TASK_STATUSES))
        .values(
            status="cancelled",
            error=None,
            result_json=result_json,
            finished_at=utcnow(),
        )
    )
    if transition.rowcount != 1:
        session.rollback()
        session.refresh(task)
        return None
    session.commit()
    return cancel_codex_run(task.id)
