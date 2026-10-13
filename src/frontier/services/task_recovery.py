from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import ArchitectureDocument, Brief, Message, Paper, TaskRun, utcnow
from .architecture_scene import release_agent_lock_for_task
from .common import log_operation
from .manual_recommendations import set_manual_analysis_state
from .podcast_generation import mark_podcast_task_failed

ACTIVE_TASK_STATUSES = {"queued", "running"}


def _interruption_error(previous_status: str) -> str:
    if previous_status == "queued":
        return "服务重启时发现旧实例遗留的排队任务；任务尚未开始执行，已标记为失败。"
    return "服务上次异常退出或重启时 Agent 仍在运行；任务已中断并标记为失败。"


def _interrupted_activities(values: object, error: str) -> list[dict]:
    activities: list[dict] = []
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, dict) or not value.get("status"):
                continue
            item = dict(value)
            if item["status"] in ACTIVE_TASK_STATUSES:
                item["status"] = "failed"
            activities.append(item)
    if not any(item.get("status") == "failed" for item in activities):
        activities.append(
            {
                "id": "agent-stream-interrupted",
                "kind": "status",
                "label": "Agent 响应未完成",
                "detail": error,
                "status": "failed",
            }
        )
    return activities


def _drafts_by_task(session: Session, task_ids: set[str]) -> dict[str, list[Message]]:
    drafts: dict[str, list[Message]] = defaultdict(list)
    if not task_ids:
        return drafts
    messages = session.scalars(
        select(Message).where(Message.role == "assistant").order_by(Message.created_at)
    ).all()
    for message in messages:
        task_id = (message.context_json or {}).get("task_id")
        if isinstance(task_id, str) and task_id in task_ids:
            drafts[task_id].append(message)
    return drafts


def _sync_conversation_drafts(
    task: TaskRun,
    drafts: Iterable[Message],
    error: str,
) -> None:
    result = dict(task.result_json) if isinstance(task.result_json, dict) else {}
    response = result.get("response")
    activities_source: object = result.get("activities")
    runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else None
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None

    for draft in drafts:
        context = dict(draft.context_json or {})
        draft_activities = context.get("activities")
        activities = _interrupted_activities(
            draft_activities if isinstance(draft_activities, list) else activities_source,
            error,
        )
        draft_runtime = context.get("runtime")
        draft_usage = context.get("usage")
        if isinstance(draft_runtime, dict):
            runtime = {**(runtime or {}), **draft_runtime}
        if isinstance(draft_usage, dict):
            usage = {**(usage or {}), **draft_usage}
        if draft.content:
            response = draft.content
        draft.context_json = {
            **context,
            "task_id": task.id,
            "stream_status": "interrupted",
            "stream_error": error,
            "activities": activities,
            **({"runtime": runtime} if runtime is not None else {}),
            **({"usage": usage} if usage is not None else {}),
        }
        activities_source = activities

    activities = _interrupted_activities(activities_source, error)
    task.result_json = {
        **result,
        **({"response": response} if response is not None else {}),
        "activities": activities,
        "stream_status": "interrupted",
        **({"runtime": runtime} if runtime is not None else {}),
        **({"usage": usage} if usage is not None else {}),
    }


def _recover_architecture(session: Session, task: TaskRun, error: str) -> None:
    documents = session.scalars(
        select(ArchitectureDocument).where(ArchitectureDocument.generation_task_id == task.id)
    ).all()
    for document in documents:
        document.status = "ready" if document.scene_json else "failed"
        document.error = error
        document.generation_task_id = None
    release_agent_lock_for_task(session, task.id)


def recover_interrupted_tasks(session: Session, started_before: datetime) -> int:
    """Fail active tasks left by an earlier service instance.

    ``started_before`` is the current process' startup boundary. Queued tasks
    are compared by creation time. Running tasks use their actual start time;
    legacy running rows without one are necessarily incomplete and are also
    recovered.
    """
    tasks = session.scalars(
        select(TaskRun)
        .where(
            or_(
                and_(
                    TaskRun.status == "queued",
                    TaskRun.created_at < started_before,
                ),
                and_(
                    TaskRun.status == "running",
                    or_(
                        TaskRun.started_at.is_(None),
                        TaskRun.started_at < started_before,
                    ),
                ),
            )
        )
        .order_by(TaskRun.created_at)
    ).all()
    if not tasks:
        return 0

    drafts = _drafts_by_task(session, {task.id for task in tasks})
    recovered_at = utcnow()
    for task in tasks:
        previous_status = task.status
        published_brief = (
            session.scalar(
                select(Brief).where(
                    Brief.task_id == task.id,
                    Brief.status == "published",
                )
            )
            if task.kind == "daily_brief"
            else None
        )
        if published_brief is not None:
            task.status = "completed"
            task.error = None
            task.finished_at = recovered_at
            task.result_json = {
                **(task.result_json or {}),
                "stream_status": "completed",
                "recovered_published_brief_id": published_brief.id,
            }
            log_operation(
                session,
                actor="system",
                action="task.recovered_completed",
                entity_type="task",
                entity_id=task.id,
                task_id=task.id,
                detail={
                    "previous_status": previous_status,
                    "recovery_reason": "published_brief_committed",
                    "brief_id": published_brief.id,
                    "started_before": started_before.isoformat(),
                },
            )
            continue
        error = _interruption_error(previous_status)
        _sync_conversation_drafts(task, drafts.get(task.id, ()), error)
        task.status = "failed"
        task.error = error
        task.finished_at = recovered_at

        if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
            set_manual_analysis_state(session, task, "failed", error=error)
        elif task.kind == "paper_abstract_translation":
            paper_id = (task.payload_json or {}).get("paper_id")
            paper = session.get(Paper, paper_id) if isinstance(paper_id, str) else None
            if paper is not None:
                metadata = dict(paper.metadata_json or {})
                metadata["abstract_translation"] = {
                    **dict(metadata.get("abstract_translation") or {}),
                    "status": "failed",
                    "task_id": task.id,
                    "error": error,
                }
                paper.metadata_json = metadata
        if task.kind == "architecture_generation":
            _recover_architecture(session, task, error)
        elif task.kind == "podcast_episode_render":
            mark_podcast_task_failed(session, task, error)
        else:
            release_agent_lock_for_task(session, task.id)

        log_operation(
            session,
            actor="system",
            action="task.interrupted",
            entity_type="task",
            entity_id=task.id,
            task_id=task.id,
            detail={
                "previous_status": previous_status,
                "recovery_reason": "startup_recovery",
                "started_before": started_before.isoformat(),
            },
        )

    session.commit()
    return len(tasks)


__all__ = ["recover_interrupted_tasks"]
