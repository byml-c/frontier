from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Paper, TaskRun
from .common import log_operation

METADATA_ENRICHMENT_KEY = "metadata_enrichment"
PAPER_METADATA_ENRICHMENT_KIND = "paper_metadata_enrichment"
METADATA_BATCH_SIZE = 10


def _active_task(session: Session, task_id: object) -> TaskRun | None:
    if not isinstance(task_id, str):
        return None
    task = session.get(TaskRun, task_id)
    if task is None or task.status not in {"queued", "running"}:
        return None
    return task


def queue_metadata_enrichment(
    session: Session,
    paper_ids: list[str],
    *,
    reason: str = "ingest",
) -> list[TaskRun]:
    """Queue bounded metadata-Agent work for newly ingested papers."""
    if not settings.enable_codex_runs:
        return []
    candidates: list[Paper] = []
    for paper_id in dict.fromkeys(paper_ids):
        paper = session.get(Paper, paper_id)
        if paper is None:
            continue
        state = (paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY)
        if isinstance(state, dict):
            active = _active_task(session, state.get("task_id"))
            if active is not None:
                continue
        candidates.append(paper)
    tasks: list[TaskRun] = []
    queued = session.scalar(
        select(TaskRun)
        .where(
            TaskRun.kind == PAPER_METADATA_ENRICHMENT_KIND,
            TaskRun.status == "queued",
        )
        .order_by(TaskRun.created_at.desc())
        .limit(1)
    )
    if queued is not None:
        existing_ids = [
            str(value)
            for value in (queued.payload_json or {}).get("paper_ids", [])
            if isinstance(value, str)
        ]
        room = max(0, METADATA_BATCH_SIZE - len(existing_ids))
        if room:
            extra = candidates[:room]
            candidates = candidates[room:]
            if extra:
                queued.payload_json = {
                    **(queued.payload_json or {}),
                    "paper_ids": [*existing_ids, *(paper.id for paper in extra)],
                }
                now = datetime.now(UTC).isoformat()
                for paper in extra:
                    paper.metadata_json = {
                        **(paper.metadata_json or {}),
                        METADATA_ENRICHMENT_KEY: {
                            "status": "queued",
                            "task_id": queued.id,
                            "requested_at": now,
                            "reason": reason,
                            "error": None,
                        },
                    }
                    log_operation(
                        session,
                        actor="system",
                        action="paper.metadata_enrichment_requested",
                        entity_type="paper",
                        entity_id=paper.id,
                        task_id=queued.id,
                        detail={"reason": reason, "batched": True},
                    )
                tasks.append(queued)
    for start in range(0, len(candidates), METADATA_BATCH_SIZE):
        batch = candidates[start : start + METADATA_BATCH_SIZE]
        task = TaskRun(
            kind=PAPER_METADATA_ENRICHMENT_KIND,
            payload_json={
                "paper_ids": [paper.id for paper in batch],
                "reason": reason,
            },
        )
        session.add(task)
        session.flush()
        now = datetime.now(UTC).isoformat()
        for paper in batch:
            paper.metadata_json = {
                **(paper.metadata_json or {}),
                METADATA_ENRICHMENT_KEY: {
                    "status": "queued",
                    "task_id": task.id,
                    "requested_at": now,
                    "reason": reason,
                    "error": None,
                },
            }
            log_operation(
                session,
                actor="system",
                action="paper.metadata_enrichment_requested",
                entity_type="paper",
                entity_id=paper.id,
                task_id=task.id,
                detail={"reason": reason},
            )
        tasks.append(task)
    session.commit()
    return tasks


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
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            METADATA_ENRICHMENT_KEY: {
                **state,
                "status": "failed",
                "error": error,
                "updated_at": now,
            },
        }
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
