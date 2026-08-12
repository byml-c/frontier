from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent_runner import build_prompt, enqueue_task, run_task
from ..config import settings
from ..database import SessionLocal, get_session
from ..interactive_stream import encode_stream_event, stream_codex_prompt
from ..models import AppSetting, Memory, OperationLog, TaskRun, utcnow
from ..schemas import MemoryCreate, MemoryUpdate, SettingUpdate, TaskCreate
from ..services.backup import create_local_git_backup
from ..services.bootstrap import import_bibtex
from ..services.common import log_operation, model_dict
from ..services.ingest import create_or_update_paper, search_arxiv
from ..services.service_checks import check_service
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
    service: Literal["codex", "translation"],
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
    for service in ("codex", "translation"):
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


@router.post("/tasks")
def create_task(payload: TaskCreate, background_tasks: BackgroundTasks) -> dict:
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
    task_id = enqueue_task(payload.kind, payload.payload_json)
    if payload.kind == "translate" or settings.enable_codex_runs:
        background_tasks.add_task(run_task, task_id)
    return {"id": task_id, "status": "queued"}


@router.post("/tasks/stream")
def stream_task(payload: TaskCreate) -> StreamingResponse:
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
    task_id = enqueue_task(payload.kind, payload.payload_json)

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
        try:
            if payload.kind != "translate" and not settings.enable_codex_runs:
                raise RuntimeError("Agent 运行已在服务器设置中停用")
            with SessionLocal() as runner_session:
                task = runner_session.get(TaskRun, task_id)
                if not task:
                    raise RuntimeError("任务不存在")
                task.status = "running"
                task.started_at = utcnow()
                prompt = build_prompt(task)
                runner_session.commit()
            if payload.kind == "translate":
                for delta in stream_translation_prompt(prompt):
                    content += delta
                    yield encode_stream_event("delta", delta=delta)
            else:
                for event in stream_codex_prompt(prompt):
                    if event["type"] == "thread":
                        thread_id = event["thread_id"]
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
                if task:
                    task.status = "completed"
                    task.codex_session_id = thread_id
                    task.result_json = {"response": content}
                    task.finished_at = utcnow()
                    runner_session.commit()
            yield encode_stream_event("done", task_id=task_id, content=content)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                task = runner_session.get(TaskRun, task_id)
                if task:
                    task.status = "failed"
                    task.error = str(exc)
                    task.finished_at = utcnow()
                    runner_session.commit()
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
    new_id = enqueue_task(old.kind, {**old.payload_json, "retry_of": old.id})
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, new_id)
    return {"id": new_id, "status": "queued", "retry_of": old.id}


@router.post("/tasks/run-daily")
def run_daily(background_tasks: BackgroundTasks, target_date: date | None = None) -> dict:
    run_date = target_date or datetime.now(ZoneInfo(settings.timezone)).date()
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
        paper_ids.append(paper.id)
        created += int(was_created)
        merged += int(not was_created)
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
