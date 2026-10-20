from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Paper, TaskRun
from .common import log_operation
from .task_dispatcher import submit_task

METADATA_ENRICHMENT_KEY = "metadata_enrichment"
PAPER_METADATA_ENRICHMENT_KIND = "paper_metadata_enrichment"
METADATA_BATCH_SIZE = 10
_COORDINATOR_POLL_SECONDS = 2.0
_coordinator_lock = threading.Lock()
_coordinator_thread: threading.Thread | None = None
_coordinator_stopping = False
logger = logging.getLogger(__name__)


def _active_task(session: Session, task_id: object) -> TaskRun | None:
    if not isinstance(task_id, str):
        return None
    task = session.get(TaskRun, task_id)
    if task is None or task.status not in {"queued", "running"}:
        return None
    return task


def _set_state(paper: Paper, **values: object) -> None:
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        METADATA_ENRICHMENT_KEY: {
            **dict((paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY) or {}),
            **values,
        },
    }


def queue_metadata_enrichment(
    session: Session,
    paper_ids: list[str],
    *,
    reason: str = "ingest",
) -> list[TaskRun]:
    """Mark papers pending; the coordinator creates one bounded task at a time."""
    if not settings.enable_codex_runs:
        return []
    now = datetime.now(UTC).isoformat()
    marked = 0
    for paper_id in dict.fromkeys(paper_ids):
        paper = session.get(Paper, paper_id)
        if paper is None:
            continue
        state = (paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY)
        if isinstance(state, dict):
            if state.get("status") in {"pending", "queued", "running", "completed"}:
                continue
            if _active_task(session, state.get("task_id")) is not None:
                continue
        _set_state(
            paper,
            status="pending",
            task_id=None,
            requested_at=now,
            reason=reason,
            error=None,
        )
        marked += 1
    if marked:
        log_operation(
            session,
            actor="system",
            action="paper.metadata_enrichment_queued",
            entity_type="metadata_batch",
            detail={"paper_count": marked, "reason": reason},
        )
    session.commit()
    return []


def _pending_papers(session: Session, *, limit: int = METADATA_BATCH_SIZE) -> list[Paper]:
    statement = (
        select(Paper)
        .where(
            Paper.metadata_json["metadata_enrichment"]["status"].as_string() == "pending"
        )
        .order_by(Paper.created_at.asc())
        .limit(limit)
    )
    papers = list(session.scalars(statement).all())
    if papers:
        return papers
    # JSON-path extraction is dialect-sensitive; retain a bounded fallback.
    recent = session.scalars(select(Paper).order_by(Paper.created_at.desc()).limit(500)).all()
    return [
        paper
        for paper in recent
        if ((paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY) or {}).get("status")
        == "pending"
    ][:limit]


def _reconcile_queued(session: Session) -> None:
    """Release papers whose metadata task already reached a terminal state."""
    queued = list(
        session.scalars(
            select(Paper)
            .where(
                Paper.metadata_json["metadata_enrichment"]["status"].as_string() == "queued"
            )
            .order_by(Paper.created_at.asc())
            .limit(500)
        ).all()
    )
    changed = False
    for paper in queued:
        state = dict((paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY) or {})
        task = session.get(TaskRun, state.get("task_id")) if state.get("task_id") else None
        if task is not None and task.status in {"queued", "running"}:
            continue
        _set_state(
            paper,
            status="completed" if task is not None and task.status == "completed" else "failed",
            task_id=None,
            error=None if task is not None and task.status == "completed" else "Agent 任务已结束",
        )
        changed = True
    if changed:
        session.commit()


def dispatch_next_metadata_batch(session: Session) -> TaskRun | None:
    """Create at most one queued metadata task when no metadata task is active."""
    if not settings.enable_codex_runs:
        return None
    _reconcile_queued(session)
    active_count = int(
        session.scalar(
            select(func.count(TaskRun.id)).where(
                TaskRun.kind == PAPER_METADATA_ENRICHMENT_KIND,
                TaskRun.status.in_({"queued", "running"}),
            )
        )
        or 0
    )
    if active_count >= settings.metadata_agent_max_concurrent_runs:
        return None
    papers = _pending_papers(session)
    if not papers:
        return None
    task = TaskRun(
        kind=PAPER_METADATA_ENRICHMENT_KIND,
        payload_json={
            "paper_ids": [paper.id for paper in papers],
            "reason": "coordinator",
        },
    )
    session.add(task)
    session.flush()
    now = datetime.now(UTC).isoformat()
    for paper in papers:
        _set_state(
            paper,
            status="queued",
            task_id=task.id,
            requested_at=now,
            error=None,
        )
    session.commit()
    if submit_task(task.id, fail_on_full=False, lane="metadata"):
        return task
    # Keep the work pending when the shared Agent queue is occupied.
    for paper in papers:
        _set_state(paper, status="pending", task_id=None)
    session.delete(task)
    session.commit()
    return None


def mark_metadata_enrichment_failed(session: Session, task: TaskRun, error: str) -> None:
    paper_ids = [
        str(value)
        for value in (task.payload_json or {}).get("paper_ids", [])
        if isinstance(value, str)
    ]
    now = datetime.now(UTC).isoformat()
    for paper_id in paper_ids:
        paper = session.get(Paper, paper_id)
        if paper is None:
            continue
        state = dict((paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY) or {})
        if state.get("task_id") != task.id:
            continue
        _set_state(paper, status="failed", error=error, updated_at=now)
    session.commit()


def metadata_enrichment_task_for_paper(session: Session, paper_id: str) -> TaskRun | None:
    paper = session.get(Paper, paper_id)
    if paper is None:
        return None
    state = (paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY)
    if not isinstance(state, dict):
        return None
    task_id = state.get("task_id")
    return session.get(TaskRun, task_id) if isinstance(task_id, str) else None


def papers_awaiting_metadata(session: Session, *, limit: int = 100) -> list[Paper]:
    statement = select(Paper).where(Paper.status == "candidate").limit(limit)
    return [paper for paper in session.scalars(statement) if not paper.research_group_id]


def _coordinator_loop() -> None:
    global _coordinator_thread
    try:
        while True:
            with _coordinator_lock:
                if _coordinator_stopping:
                    return
            try:
                with SessionLocal() as session:
                    for _ in range(settings.metadata_agent_max_concurrent_runs):
                        if dispatch_next_metadata_batch(session) is None:
                            break
            except Exception:
                logger.exception("Metadata enrichment coordinator failed")
            with _coordinator_lock:
                if _coordinator_stopping:
                    return
            threading.Event().wait(_COORDINATOR_POLL_SECONDS)
    finally:
        with _coordinator_lock:
            if _coordinator_thread is threading.current_thread():
                _coordinator_thread = None


def start_metadata_coordinator() -> None:
    global _coordinator_thread, _coordinator_stopping
    if not settings.enable_codex_runs:
        return
    with _coordinator_lock:
        if _coordinator_thread is not None and _coordinator_thread.is_alive():
            return
        _coordinator_stopping = False
        _coordinator_thread = threading.Thread(
            target=_coordinator_loop,
            name="paper-metadata-coordinator",
            daemon=True,
        )
        _coordinator_thread.start()


def stop_metadata_coordinator() -> None:
    global _coordinator_stopping
    with _coordinator_lock:
        _coordinator_stopping = True
        thread = _coordinator_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2.0)
