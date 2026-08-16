from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import (
    ArchitectureDocument,
    Brief,
    Conversation,
    Message,
    OperationLog,
    Paper,
    TaskRun,
)
from frontier.services.task_recovery import recover_interrupted_tasks


def _paper(title: str) -> Paper:
    return Paper(title=title, normalized_title=title.casefold())


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def test_recovers_stale_daily_queued_and_running_tasks(session: Session) -> None:
    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(hours=1)
    queued = TaskRun(kind="daily_brief", status="queued", created_at=old)
    running = TaskRun(
        kind="daily_brief",
        status="running",
        created_at=old,
        started_at=old,
    )
    running_without_started_at = TaskRun(
        kind="daily_brief",
        status="running",
        created_at=cutoff + timedelta(minutes=1),
    )
    session.add_all([queued, running, running_without_started_at])
    session.commit()
    task_ids = [queued.id, running.id, running_without_started_at.id]

    assert recover_interrupted_tasks(session, started_before=cutoff) == 3
    recovered = [session.get(TaskRun, task_id) for task_id in task_ids]
    assert all(task is not None and task.status == "failed" for task in recovered)
    assert recovered[0] is not None and "排队任务" in str(recovered[0].error)
    assert recovered[1] is not None and "异常退出或重启" in str(recovered[1].error)
    assert all(task is not None and task.finished_at is not None for task in recovered)
    logs = session.scalars(select(OperationLog).where(OperationLog.task_id.in_(task_ids))).all()
    assert len(logs) == 3
    assert all(log.action == "task.interrupted" for log in logs)


def test_respects_startup_cutoff_and_is_idempotent(session: Session) -> None:
    cutoff = datetime.now(UTC)
    fresh_queued = TaskRun(
        kind="daily_brief",
        status="queued",
        created_at=cutoff + timedelta(seconds=1),
    )
    fresh_running = TaskRun(
        kind="daily_brief",
        status="running",
        created_at=cutoff - timedelta(hours=1),
        started_at=cutoff + timedelta(seconds=1),
    )
    session.add_all([fresh_queued, fresh_running])
    session.commit()

    assert recover_interrupted_tasks(session, started_before=cutoff) == 0
    assert fresh_queued.status == "queued"
    assert fresh_running.status == "running"

    stale = TaskRun(
        kind="daily_brief",
        status="queued",
        created_at=cutoff - timedelta(seconds=1),
    )
    session.add(stale)
    session.commit()
    assert recover_interrupted_tasks(session, started_before=cutoff) == 1
    assert recover_interrupted_tasks(session, started_before=cutoff) == 0
    assert (
        session.scalar(
            select(OperationLog)
            .where(OperationLog.task_id == stale.id)
            .with_only_columns(OperationLog.id)
        )
        is not None
    )


def test_conversation_recovery_preserves_partial_snapshot_and_exact_task_match(
    session: Session,
) -> None:
    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(minutes=5)
    conversation = Conversation(kind="global", title="Recovery")
    task = TaskRun(
        kind="global_chat",
        status="running",
        created_at=old,
        started_at=old,
        payload_json={"conversation_id": "not-used-for-matching"},
        result_json={
            "response": "旧快照",
            "runtime": {"model": "gpt-5.4"},
            "usage": {"current_tokens": 120},
            "custom": "preserved",
        },
    )
    other = TaskRun(
        kind="global_chat",
        status="completed",
        payload_json={"conversation_id": "not-used-for-matching"},
    )
    session.add_all([conversation, task, other])
    session.flush()
    matching = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="重启前的部分回答",
        context_json={
            "task_id": task.id,
            "stream_status": "running",
            "activities": [{"id": "tool", "status": "running"}],
            "runtime": {"model": "gpt-5.4", "reasoning_effort": "high"},
            "usage": {"current_tokens": 256},
        },
    )
    unrelated = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="另一轮",
        context_json={"task_id": other.id, "stream_status": "running"},
    )
    session.add_all([matching, unrelated])
    session.commit()

    assert recover_interrupted_tasks(session, started_before=cutoff) == 1
    session.refresh(task)
    session.refresh(matching)
    session.refresh(unrelated)
    assert task.result_json["response"] == "重启前的部分回答"
    assert task.result_json["runtime"]["reasoning_effort"] == "high"
    assert task.result_json["usage"]["current_tokens"] == 256
    assert task.result_json["custom"] == "preserved"
    assert task.result_json["stream_status"] == "interrupted"
    assert matching.context_json["stream_status"] == "interrupted"
    assert matching.context_json["activities"][0]["status"] == "failed"
    assert unrelated.context_json["stream_status"] == "running"


def test_recovers_manual_analysis_derived_state(session: Session) -> None:
    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(minutes=5)
    paper = _paper("Manual recovery")
    task = TaskRun(
        kind="manual_paper_analysis",
        status="running",
        created_at=old,
        started_at=old,
    )
    session.add_all([paper, task])
    session.flush()
    task.payload_json = {"paper_id": paper.id}
    paper.metadata_json = {
        "manual_analysis": {"status": "processing", "task_id": task.id},
        "keep": True,
    }
    session.commit()

    assert recover_interrupted_tasks(session, started_before=cutoff) == 1
    session.refresh(paper)
    state = paper.metadata_json["manual_analysis"]
    assert state["status"] == "failed"
    assert state["task_id"] == task.id
    assert "异常退出或重启" in state["error"]
    assert paper.metadata_json["keep"] is True


def test_recovers_architecture_document_and_agent_lock(session: Session) -> None:
    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(minutes=5)
    paper = _paper("Architecture recovery")
    task = TaskRun(
        kind="architecture_generation",
        status="running",
        created_at=old,
        started_at=old,
    )
    session.add_all([paper, task])
    session.flush()
    task.payload_json = {"paper_id": paper.id}
    document = ArchitectureDocument(
        paper_id=paper.id,
        status="generating",
        scene_json={"root": {"id": "partial"}},
        generation_task_id=task.id,
        lock_owner_type="agent",
        lock_owner_id=task.id,
    )
    session.add(document)
    session.commit()

    assert recover_interrupted_tasks(session, started_before=cutoff) == 1
    session.refresh(document)
    assert document.status == "ready"
    assert document.generation_task_id is None
    assert "异常退出或重启" in str(document.error)
    assert document.lock_owner_type is None
    assert document.lock_owner_id is None
    assert document.lock_expires_at is None


def test_published_daily_is_recovered_as_completed(session: Session) -> None:
    cutoff = datetime.now(UTC)
    old = cutoff - timedelta(minutes=5)
    task = TaskRun(
        kind="daily_brief",
        status="running",
        created_at=old,
        started_at=old,
        payload_json={"date": "2035-01-01"},
    )
    session.add(task)
    session.flush()
    brief = Brief(
        brief_date=datetime(2035, 1, 1, tzinfo=UTC).date(),
        title="Published before restart",
        status="published",
        published_at=old,
        task_id=task.id,
    )
    session.add(brief)
    session.commit()

    assert recover_interrupted_tasks(session, started_before=cutoff) == 1
    session.refresh(task)
    assert task.status == "completed"
    assert task.error is None
    assert task.result_json["recovered_published_brief_id"] == brief.id
    log = session.scalar(select(OperationLog).where(OperationLog.task_id == task.id))
    assert log is not None and log.action == "task.recovered_completed"
