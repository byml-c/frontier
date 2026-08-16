from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from functools import wraps

from sqlalchemy.orm import Session
from sqlalchemy.sql import select, update

from ..interactive_stream import cancel_codex_run, forget_codex_thread
from ..models import ArchitectureDocument, Brief, Conversation, Message, TaskRun, utcnow
from .architecture_scene import release_agent_lock_for_task

ACTIVE_TASK_STATUSES = {"queued", "running"}
_TASK_CLAIM_LOCK = threading.RLock()


@dataclass(frozen=True)
class TaskClaim:
    task: TaskRun | None
    created: bool
    published_brief_id: str | None = None
    conversation_conflict: bool = False


@contextmanager
def task_claim_transaction(session: Session) -> Iterator[None]:
    """Serialize task claims and commit their durable state as one transaction.

    SQLite cannot express the JSON-backed conversation/daily claim as a useful
    unique constraint. Frontier is a single service process, so this lock closes
    the check-then-insert race while the database transaction keeps all related
    rows (message, task, document lock) atomic.
    """
    with _TASK_CLAIM_LOCK:
        session.expire_all()
        try:
            yield
            session.commit()
        except BaseException:
            session.rollback()
            raise


def serialized_task_claim[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Keep an entire synchronous endpoint preparation phase inside one claim."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        session = kwargs.get("session")
        if not isinstance(session, Session):
            session = next((value for value in args if isinstance(value, Session)), None)
        if not isinstance(session, Session):
            raise TypeError("serialized_task_claim requires a SQLAlchemy Session")
        with task_claim_transaction(session):
            return function(*args, **kwargs)

    return wrapped


def active_task_for_conversation(session: Session, conversation_id: str) -> TaskRun | None:
    return session.scalar(
        select(TaskRun)
        .where(
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
            TaskRun.payload_json["conversation_id"].as_string() == conversation_id,
        )
        .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
        .limit(1)
    )


def retry_task_for(session: Session, task_id: str) -> TaskRun | None:
    """Return the durable successor for an idempotent retry request."""
    return session.scalar(
        select(TaskRun)
        .where(TaskRun.payload_json["retry_of"].as_string() == task_id)
        .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
        .limit(1)
    )


def _daily_claim(
    session: Session,
    target_date: date,
    *,
    payload: dict,
) -> TaskClaim:
    published = session.scalar(
        select(Brief).where(
            Brief.brief_date == target_date,
            Brief.status == "published",
        )
    )
    if published is not None:
        task = session.get(TaskRun, published.task_id) if published.task_id else None
        return TaskClaim(task=task, created=False, published_brief_id=published.id)

    target = target_date.isoformat()
    active = session.scalar(
        select(TaskRun)
        .where(
            TaskRun.kind == "daily_brief",
            TaskRun.payload_json["date"].as_string() == target,
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
        )
        .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
        .limit(1)
    )
    if active is not None:
        return TaskClaim(task=active, created=False)

    task = TaskRun(kind="daily_brief", payload_json={**payload, "date": target})
    session.add(task)
    session.flush()
    return TaskClaim(task=task, created=True)


def get_or_create_daily_task(
    session: Session,
    target_date: date,
    *,
    payload: dict | None = None,
) -> TaskClaim:
    """Atomically claim one active daily run unless that brief is published."""
    with task_claim_transaction(session):
        return _daily_claim(session, target_date, payload=payload or {})


def get_or_create_retry_task(session: Session, old: TaskRun) -> TaskClaim:
    """Create one retry successor, respecting conversation and daily claims."""
    with task_claim_transaction(session):
        current = session.get(TaskRun, old.id)
        if current is None:
            return TaskClaim(task=None, created=False)
        existing = retry_task_for(session, current.id)
        if existing is not None:
            return TaskClaim(task=existing, created=False)

        payload = {**(current.payload_json or {}), "retry_of": current.id}
        if current.kind == "daily_brief":
            raw_date = payload.get("date")
            if not isinstance(raw_date, str):
                raise ValueError("daily_brief requires an ISO date")
            return _daily_claim(
                session,
                date.fromisoformat(raw_date),
                payload=payload,
            )

        conversation_id = payload.get("conversation_id")
        if isinstance(conversation_id, str):
            active = active_task_for_conversation(session, conversation_id)
            if active is not None:
                return TaskClaim(
                    task=active,
                    created=False,
                    conversation_conflict=True,
                )

        task = TaskRun(kind=current.kind, payload_json=payload)
        session.add(task)
        session.flush()
        return TaskClaim(task=task, created=True)


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
    abandoned_thread_id: str | None = None

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
            abandoned_thread_id = conversation.codex_session_id
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
    if task.kind == "architecture_generation":
        document = session.scalar(
            select(ArchitectureDocument).where(
                ArchitectureDocument.generation_task_id == task.id
            )
        )
        if document:
            document.status = "ready" if document.scene_json else "missing"
            document.error = None
            document.generation_task_id = None
    release_agent_lock_for_task(session, task.id)
    session.commit()
    process_stopped = cancel_codex_run(task.id)
    forget_codex_thread(abandoned_thread_id)
    return process_stopped
