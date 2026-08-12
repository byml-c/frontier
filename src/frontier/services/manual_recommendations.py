from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Brief, BriefItem, Paper, TaskRun
from .common import log_operation


def _needs_analysis(paper: Paper) -> bool:
    return not all((paper.summary, paper.agent_comment, paper.recommendation_reason))


def _analysis_key(task: TaskRun) -> str:
    return "manual_analysis" if task.kind == "manual_paper_analysis" else "agent_metadata_analysis"


def current_analysis_state(paper: Paper) -> dict:
    """Return the newest user-visible analysis state across automatic and manual runs."""
    states = [
        value
        for key in ("manual_analysis", "agent_metadata_analysis")
        if isinstance((value := (paper.metadata_json or {}).get(key)), dict)
    ]
    if not states:
        return {}
    return max(
        states,
        key=lambda value: str(value.get("updated_at") or value.get("requested_at") or ""),
    )


def queue_paper_metadata_analysis(session: Session, paper: Paper) -> tuple[TaskRun, bool]:
    """Queue an explicit metadata generation without changing brief membership."""
    analysis = dict((paper.metadata_json or {}).get("agent_metadata_analysis", {}))
    if analysis.get("status") in {"queued", "processing"} and analysis.get("task_id"):
        active = session.get(TaskRun, analysis["task_id"])
        if active and active.status in {"queued", "running"}:
            return active, False
    task = TaskRun(kind="paper_metadata_analysis", payload_json={"paper_id": paper.id})
    session.add(task)
    session.flush()
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        "agent_metadata_analysis": {
            "status": "queued",
            "task_id": task.id,
            "requested_at": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            "error": None,
        },
    }
    log_operation(
        session,
        actor="user",
        action="paper.metadata_analysis_requested",
        entity_type="paper",
        entity_id=paper.id,
        task_id=task.id,
    )
    session.commit()
    return task, True


def stage_manual_recommendation(
    session: Session,
    paper: Paper,
    *,
    queue_analysis: bool = True,
) -> tuple[Brief, BriefItem, TaskRun | None, bool]:
    """Place a user-added paper in today's brief and queue missing analysis."""
    brief_date = datetime.now(ZoneInfo(settings.timezone)).date()
    brief = session.scalar(select(Brief).where(Brief.brief_date == brief_date))
    if brief is None:
        brief = Brief(
            brief_date=brief_date,
            title=f"{brief_date.isoformat()} 研究简报",
            introduction="今天值得关注的论文与研究方向变化。",
        )
        session.add(brief)
        session.flush()

    item = session.scalar(
        select(BriefItem).where(
            BriefItem.brief_id == brief.id,
            BriefItem.paper_id == paper.id,
        )
    )
    if item is None:
        last_rank = session.scalar(
            select(func.max(BriefItem.rank)).where(BriefItem.brief_id == brief.id)
        )
        item = BriefItem(
            brief_id=brief.id,
            paper_id=paper.id,
            rank=(last_rank or 0) + 1,
            tier="manual",
        )
        session.add(item)
        session.flush()
        if brief.status == "published":
            paper_ids = list(brief.snapshot_json.get("paper_ids", []))
            if paper.id not in paper_ids:
                brief.snapshot_json = {**brief.snapshot_json, "paper_ids": [*paper_ids, paper.id]}
        log_operation(
            session,
            actor="user",
            action="brief.manual_paper_added",
            entity_type="brief_item",
            entity_id=item.id,
            detail={"brief_id": brief.id, "paper_id": paper.id},
        )

    analysis = dict(paper.metadata_json.get("manual_analysis", {}))
    active_task = None
    if analysis.get("status") in {"queued", "processing"} and analysis.get("task_id"):
        active_task = session.get(TaskRun, analysis["task_id"])
        if active_task and active_task.status not in {"queued", "running"}:
            active_task = None

    task = active_task
    task_created = False
    if (
        queue_analysis
        and task is None
        and (_needs_analysis(paper) or analysis.get("status") == "failed")
    ):
        task = TaskRun(
            kind="manual_paper_analysis",
            payload_json={
                "paper_id": paper.id,
                "brief_id": brief.id,
                "date": brief_date.isoformat(),
            },
        )
        session.add(task)
        session.flush()
        task_created = True
        paper.metadata_json = {
            **paper.metadata_json,
            "manual_analysis": {
                "status": "queued",
                "task_id": task.id,
                "requested_at": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
                "error": None,
            },
        }

    session.commit()
    return brief, item, task, task_created


def set_manual_analysis_state(
    session: Session,
    task: TaskRun,
    status: str,
    *,
    error: str | None = None,
) -> None:
    paper_id = task.payload_json.get("paper_id")
    paper = session.get(Paper, paper_id) if paper_id else None
    if paper is None:
        return
    key = _analysis_key(task)
    current = dict((paper.metadata_json or {}).get(key, {}))
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        key: {
            **current,
            "status": status,
            "task_id": task.id,
            "updated_at": datetime.now(ZoneInfo(settings.timezone)).isoformat(),
            "error": error,
        },
    }
