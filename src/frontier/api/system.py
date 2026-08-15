from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..agent_runner import (
    build_conversation_recovery_prompt,
    build_prompt,
    enqueue_task,
    run_task,
)
from ..config import settings
from ..database import SessionLocal, get_session
from ..interactive_stream import encode_stream_event, stream_codex_prompt
from ..models import AppSetting, Conversation, Memory, OperationLog, Paper, TaskRun, utcnow
from ..schemas import MemoryCreate, MemoryUpdate, SettingUpdate, TaskCreate
from ..services.backup import create_local_git_backup
from ..services.bootstrap import import_bibtex
from ..services.common import log_operation, model_dict
from ..services.ingest import create_or_update_paper, search_arxiv
from ..services.interest_evidence import DERIVED_INTEREST_PROFILE_ID
from ..services.paper_agent_context import paper_agent_context_sync_required
from ..services.paper_conversations import get_or_create_paper_conversation
from ..services.research_day import current_research_date
from ..services.service_checks import check_service
from ..services.task_control import (
    ACTIVE_TASK_STATUSES,
    active_task_for_conversation,
    cancel_task_run,
)
from ..services.translation_provider import stream_translation_prompt

router = APIRouter(prefix="/api", tags=["system"])

NUMERIC_SETTING_RANGES = {
    "daily_hour": (0, 23),
    "daily_minute": (0, 59),
    "abstract_limit": (1, 50),
    "fulltext_limit": (1, 19),
    "brief_limit": (1, 9),
    "featured_limit": (1, 4),
    "candidate_lookback_days": (1, 90),
}


def _validate_setting_value(key: str, value: object) -> None:
    if key in NUMERIC_SETTING_RANGES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail=f"{key} must be an integer")
        minimum, maximum = NUMERIC_SETTING_RANGES[key]
        if not minimum <= value <= maximum:
            raise HTTPException(
                status_code=422,
                detail=f"{key} must be between {minimum} and {maximum}",
            )
    if key == "log_level" and value not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise HTTPException(status_code=422, detail="Invalid log level")


def _validate_setting_relationships(values: dict[str, object]) -> None:
    if int(values.get("featured_limit", 4)) >= int(values.get("brief_limit", 9)):
        raise HTTPException(status_code=422, detail="featured_limit must be below brief_limit")


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.post("/service-checks/{service}")
def run_service_check(
    service: Literal["codex", "translation", "arxiv"],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    result = check_service(service)
    log = OperationLog(
        actor="user",
        action="service.checked",
        entity_type="service",
        entity_id=service,
        detail_json=result,
    )
    session.add(log)
    session.commit()
    return {**result, "checked_at": model_dict(log)["created_at"]}


@router.get("/service-checks")
def list_latest_service_checks(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, dict | None]:
    latest: dict[str, dict | None] = {}
    for service in ("codex", "translation", "arxiv"):
        log = session.scalar(
            select(OperationLog)
            .where(
                OperationLog.action == "service.checked",
                OperationLog.entity_type == "service",
                OperationLog.entity_id == service,
            )
            .order_by(OperationLog.created_at.desc())
            .limit(1)
        )
        latest[service] = (
            {**log.detail_json, "checked_at": model_dict(log)["created_at"]} if log else None
        )
    return latest


@router.get("/settings")
def list_settings(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    return [
        model_dict(item) for item in session.scalars(select(AppSetting).order_by(AppSetting.key))
    ]


@router.put("/settings/{key}")
def update_setting(
    key: str,
    payload: SettingUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _validate_setting_value(key, payload.value)
    values = {item.key: item.value_json for item in session.scalars(select(AppSetting))}
    values[key] = payload.value
    _validate_setting_relationships(values)
    item = session.get(AppSetting, key)
    if item is None:
        item = AppSetting(key=key, value_json=payload.value)
        session.add(item)
    else:
        item.value_json = payload.value
    log_operation(
        session,
        actor="user",
        action="setting.updated",
        entity_type="setting",
        entity_id=key,
    )
    session.commit()
    if key in {"daily_hour", "daily_minute"}:
        from ..scheduler import configure_jobs

        configure_jobs()
    return model_dict(item)


@router.put("/settings")
def update_settings(
    payload: dict[str, object],
    session: Annotated[Session, Depends(get_session)],
) -> list[dict]:
    current = {item.key: item.value_json for item in session.scalars(select(AppSetting))}
    values = {**current, **payload}
    for key, value in payload.items():
        _validate_setting_value(key, value)
    _validate_setting_relationships(values)
    for key, value in payload.items():
        item = session.get(AppSetting, key)
        if item is None:
            item = AppSetting(key=key, value_json=value)
            session.add(item)
        else:
            item.value_json = value
    log_operation(
        session,
        actor="user",
        action="settings.updated",
        entity_type="setting",
        detail={"keys": sorted(payload)},
    )
    session.commit()
    if {"daily_hour", "daily_minute"} & payload.keys():
        from ..scheduler import configure_jobs

        configure_jobs()
    return [
        model_dict(item) for item in session.scalars(select(AppSetting).order_by(AppSetting.key))
    ]


@router.get("/memories")
def list_memories(
    session: Annotated[Session, Depends(get_session)],
    kind: str | None = None,
    active: bool | None = None,
) -> list[dict]:
    statement = select(Memory).order_by(Memory.updated_at.desc())
    if kind:
        statement = statement.where(Memory.kind == kind)
    if active is not None:
        statement = statement.where(Memory.active == active)
    return [model_dict(item) for item in session.scalars(statement)]


@router.post("/memories")
def create_memory(payload: MemoryCreate, session: Annotated[Session, Depends(get_session)]) -> dict:
    item = Memory(**payload.model_dump())
    session.add(item)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="memory.created",
        entity_type="memory",
        entity_id=item.id,
    )
    session.commit()
    return model_dict(item)


@router.patch("/memories/{memory_id}")
def update_memory(
    memory_id: str,
    payload: MemoryUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = session.get(Memory, memory_id)
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    if item.id == DERIVED_INTEREST_PROFILE_ID:
        raise HTTPException(
            status_code=409,
            detail="The derived interest profile is maintained through the interest API",
        )
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    log_operation(
        session,
        actor="agent",
        action="memory.updated",
        entity_type="memory",
        entity_id=item.id,
    )
    session.commit()
    return model_dict(item)


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str, session: Annotated[Session, Depends(get_session)]) -> None:
    item = session.get(Memory, memory_id)
    if not item:
        raise HTTPException(status_code=404, detail="Memory not found")
    if item.id == DERIVED_INTEREST_PROFILE_ID:
        raise HTTPException(
            status_code=409,
            detail="The derived interest profile cannot be deleted directly",
        )
    session.delete(item)
    session.commit()


@router.get("/tasks")
def list_tasks(
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    statement = select(TaskRun).order_by(TaskRun.created_at.desc()).limit(limit)
    if status:
        statement = statement.where(TaskRun.status == status)
    return [model_dict(item) for item in session.scalars(statement)]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    item = session.get(TaskRun, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return model_dict(item)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = session.get(TaskRun, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    if item.status == "cancelled":
        return {**model_dict(item), "process_stopped": False}
    if item.status not in ACTIVE_TASK_STATUSES:
        raise HTTPException(status_code=409, detail="只有正在运行的任务可以中断")
    if item.kind == "translate":
        raise HTTPException(status_code=409, detail="翻译请求暂不支持任务级中断")
    process_stopped = cancel_task_run(session, item)
    if process_stopped is None:
        if item.status == "cancelled":
            return {**model_dict(item), "process_stopped": False}
        raise HTTPException(status_code=409, detail="任务已经结束，无法再中断")
    return {**model_dict(item), "process_stopped": process_stopped}


def _task_payload_with_paper_conversation(
    session: Session,
    payload: TaskCreate,
) -> dict:
    task_payload = dict(payload.payload_json)
    if payload.kind != "finish_reading":
        return task_payload

    paper_id = task_payload.get("paper_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise HTTPException(
            status_code=422,
            detail="finish_reading requires a non-empty paper_id",
        )
    paper_id = paper_id.strip()
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    conversation = get_or_create_paper_conversation(session, paper_id)
    session.commit()
    if active_task_for_conversation(session, conversation.id):
        raise HTTPException(status_code=409, detail="该论文的 Agent 正在处理另一项任务")
    return {
        **task_payload,
        "paper_id": paper_id,
        "conversation_id": conversation.id,
        "paper_context_sync_required": paper_agent_context_sync_required(
            session, paper, conversation
        ),
    }


@router.post("/tasks")
def create_task(
    payload: TaskCreate,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    allowed = {
        "daily_brief",
        "finish_reading",
        "graph_maintenance",
        "global_chat",
        "paper_chat",
        "translate",
    }
    if payload.kind not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported task kind: {payload.kind}")
    task_payload = _task_payload_with_paper_conversation(session, payload)
    task_id = enqueue_task(payload.kind, task_payload)
    if payload.kind == "translate" or settings.enable_codex_runs:
        background_tasks.add_task(run_task, task_id)
    return {"id": task_id, "status": "queued"}


@router.post("/tasks/stream")
def stream_task(
    payload: TaskCreate,
    session: Annotated[Session, Depends(get_session)],
) -> StreamingResponse:
    allowed = {
        "finish_reading",
        "graph_maintenance",
        "global_chat",
        "paper_chat",
        "translate",
    }
    if payload.kind not in allowed:
        raise HTTPException(
            status_code=400, detail=f"Unsupported streaming task kind: {payload.kind}"
        )
    task_payload = _task_payload_with_paper_conversation(session, payload)
    task_id = enqueue_task(payload.kind, task_payload)
    conversation_id = (
        task_payload.get("conversation_id") if payload.kind == "finish_reading" else None
    )

    def generate():  # type: ignore[no-untyped-def]
        yield encode_stream_event("meta", task_id=task_id)
        yield encode_stream_event(
            "activity",
            activity_id="agent-startup",
            kind="status",
            label="正在启动 Agent",
            status="running",
        )
        content = ""
        thread_id = None
        session_id = None
        recovery_prompt = None
        try:
            if payload.kind != "translate" and not settings.enable_codex_runs:
                raise RuntimeError("Agent 运行已在服务器设置中停用")
            with SessionLocal() as runner_session:
                task = runner_session.get(TaskRun, task_id)
                if not task:
                    raise RuntimeError("任务不存在")
                if task.status == "cancelled":
                    yield encode_stream_event("cancelled", task_id=task_id)
                    return
                task.status = "running"
                task.started_at = utcnow()
                prompt = build_prompt(task)
                if task.kind == "finish_reading":
                    conversation = runner_session.get(Conversation, conversation_id)
                    if conversation is None:
                        raise RuntimeError("论文主 Conversation 不存在")
                    session_id = conversation.codex_session_id
                    thread_id = session_id
                    if session_id:
                        recovery_prompt = build_conversation_recovery_prompt(
                            runner_session,
                            conversation.id,
                            prompt,
                        )
                runner_session.commit()
            if payload.kind == "translate":
                for delta in stream_translation_prompt(prompt):
                    content += delta
                    yield encode_stream_event("delta", delta=delta)
            else:
                stream_options = (
                    {"session_id": session_id}
                    if payload.kind == "finish_reading"
                    else {}
                )
                if recovery_prompt:
                    stream_options["recovery_prompt"] = recovery_prompt
                for event in stream_codex_prompt(
                    prompt,
                    run_id=task_id,
                    **stream_options,
                ):
                    if event["type"] == "thread":
                        thread_id = event["thread_id"]
                        if conversation_id:
                            with SessionLocal() as runner_session:
                                current_conversation = runner_session.get(
                                    Conversation, conversation_id
                                )
                                running = runner_session.get(TaskRun, task_id)
                                if running and running.status != "cancelled":
                                    running.codex_session_id = thread_id
                                    if current_conversation:
                                        current_conversation.codex_session_id = thread_id
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
                        activity = {key: value for key, value in event.items() if key != "type"}
                        yield encode_stream_event("activity", **activity)
            with SessionLocal() as runner_session:
                task = runner_session.get(TaskRun, task_id)
                if task and task.status == "cancelled":
                    yield encode_stream_event("cancelled", task_id=task_id)
                    return
                if task:
                    transition = runner_session.execute(
                        update(TaskRun)
                        .where(TaskRun.id == task_id, TaskRun.status == "running")
                        .values(
                            status="completed",
                            codex_session_id=thread_id,
                            result_json={"response": content},
                            finished_at=utcnow(),
                        )
                    )
                    if transition.rowcount != 1:
                        runner_session.rollback()
                        current = runner_session.get(TaskRun, task_id)
                        if current and current.status == "cancelled":
                            yield encode_stream_event("cancelled", task_id=task_id)
                            return
                        raise RuntimeError("Agent 任务状态已变化，未覆盖其终态")
                    if conversation_id:
                        current_conversation = runner_session.get(
                            Conversation, conversation_id
                        )
                        if current_conversation and thread_id:
                            current_conversation.codex_session_id = thread_id
                    runner_session.commit()
            yield encode_stream_event("done", task_id=task_id, content=content)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                task = runner_session.get(TaskRun, task_id)
                if task and task.status != "cancelled":
                    task.status = "failed"
                    task.error = str(exc)
                    task.finished_at = utcnow()
                    runner_session.commit()
            with SessionLocal() as runner_session:
                cancelled = bool(
                    (task := runner_session.get(TaskRun, task_id))
                    and task.status == "cancelled"
                )
            if cancelled:
                yield encode_stream_event("cancelled", task_id=task_id)
            else:
                yield encode_stream_event("error", message=str(exc), task_id=task_id)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/retry")
def retry_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    old = session.get(TaskRun, task_id)
    if not old:
        raise HTTPException(status_code=404, detail="Task not found")
    if old.status not in {"failed", "completed"}:
        raise HTTPException(status_code=409, detail="Only finished tasks can be rerun")
    if old.kind == "architecture_generation":
        raise HTTPException(
            status_code=409,
            detail="请从论文架构页重新生成，以便先获取最新 revision 和编辑锁",
        )
    new_id = enqueue_task(old.kind, {**old.payload_json, "retry_of": old.id})
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, new_id)
    return {"id": new_id, "status": "queued", "retry_of": old.id}


@router.post("/tasks/run-daily")
def run_daily(
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    target_date: date | None = None,
) -> dict:
    run_date = target_date or current_research_date(session)
    task_id = enqueue_task("daily_brief", {"date": run_date.isoformat()})
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, task_id)
    return {"id": task_id, "status": "queued"}


@router.get("/logs")
def list_logs(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    items = session.scalars(
        select(OperationLog).order_by(OperationLog.created_at.desc()).limit(limit)
    ).all()
    return [model_dict(item) for item in items]


@router.post("/sources/arxiv/search")
def arxiv_search(
    session: Annotated[Session, Depends(get_session)],
    query: str,
    max_results: int = Query(default=50, ge=1, le=100),
    ingest: bool = True,
) -> dict:
    try:
        results = search_arxiv(query, max_results=max_results)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"arXiv query failed: {exc}") from exc
    if not ingest:
        return {"items": [item.model_dump(mode="json") for item in results], "count": len(results)}
    created = 0
    merged = 0
    paper_ids = []
    for payload in results:
        paper, was_created = create_or_update_paper(session, payload, actor="agent")
        metadata = dict(paper.metadata_json or {})
        discovery_queries = [
            str(value)
            for value in metadata.get("discovery_queries", [])
            if isinstance(value, str) and value.strip()
        ]
        if query.casefold() not in {value.casefold() for value in discovery_queries}:
            discovery_queries.append(query)
            paper.metadata_json = {**metadata, "discovery_queries": discovery_queries[-20:]}
        paper_ids.append(paper.id)
        created += int(was_created)
        merged += int(not was_created)
    session.commit()
    return {"paper_ids": paper_ids, "count": len(results), "created": created, "merged": merged}


@router.post("/backup")
def backup() -> dict[str, str | int]:
    try:
        return create_local_git_backup()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc


@router.post("/bootstrap/bibtex")
async def bootstrap_bibtex(
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="BibTeX file exceeds 20 MB")
    try:
        return import_bibtex(session, content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid BibTeX file: {exc}") from exc
