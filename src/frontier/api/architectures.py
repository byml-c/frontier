from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..agent_runner import run_task
from ..architecture_schemas import (
    ArchitectureAgentCommitRequest,
    ArchitectureCommandRequest,
    ArchitectureCommitRequest,
    ArchitectureGenerateRequest,
    ArchitectureLockRequest,
)
from ..config import settings
from ..database import get_session
from ..models import Paper, TaskRun
from ..services.architecture_progress import ARCHITECTURE_PROGRESS_KEY
from ..services.architecture_scene import (
    ArchitectureError,
    ArchitectureLockedError,
    ArchitectureRevisionError,
    ArchitectureValidationError,
    acquire_architecture_lock,
    architecture_asset_path,
    architecture_document_payload,
    commit_architecture_scene,
    release_architecture_lock,
    save_architecture_asset,
    validate_architecture_scene,
)
from ..services.common import log_operation, model_dict

router = APIRouter(prefix="/api/papers/{paper_id}/architecture", tags=["architecture"])


def _paper_or_404(session: Session, paper_id: str) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


def _architecture_error(exc: ArchitectureError) -> HTTPException:
    if isinstance(exc, ArchitectureLockedError):
        return HTTPException(
            status_code=423,
            detail={"message": "架构正在编辑", "ownerType": exc.owner_type},
        )
    if isinstance(exc, ArchitectureRevisionError):
        return HTTPException(
            status_code=409,
            detail={
                "message": "架构已更新，请重新加载后再编辑",
                "expectedRevision": exc.expected,
                "actualRevision": exc.actual,
            },
        )
    if isinstance(exc, ArchitectureValidationError):
        return HTTPException(
            status_code=422,
            detail={"message": "架构数据未通过校验", "diagnostics": exc.diagnostics},
        )
    if str(exc) == "Paper not found":
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def get_architecture(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        return architecture_document_payload(session, paper_id)
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc


@router.post("/validate")
def validate_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    normalized, diagnostics = validate_architecture_scene(payload)
    return {
        "valid": normalized is not None
        and not any(item["level"] == "error" for item in diagnostics),
        "scene": normalized,
        "diagnostics": diagnostics,
    }


@router.post("/lock")
def acquire_user_lock(
    paper_id: str,
    payload: ArchitectureLockRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        acquire_architecture_lock(
            session, paper_id, owner_type="user", owner_id=payload.owner_id
        )
        return architecture_document_payload(session, paper_id)
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc


@router.post("/lock/release")
def release_user_lock(
    paper_id: str,
    payload: ArchitectureLockRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    release_architecture_lock(
        session, paper_id, owner_type="user", owner_id=payload.owner_id
    )
    return architecture_document_payload(session, paper_id)


@router.put("")
def save_user_architecture(
    paper_id: str,
    payload: ArchitectureCommitRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        item, diagnostics = commit_architecture_scene(
            session,
            paper_id,
            payload.scene,
            expected_revision=payload.expected_revision,
            owner_type="user",
            owner_id=payload.owner_id,
        )
        log_operation(
            session,
            actor="user",
            action="architecture.updated",
            entity_type="architecture",
            entity_id=paper_id,
            detail={"revision": item.revision},
        )
        session.commit()
        return {**architecture_document_payload(session, paper_id), "diagnostics": diagnostics}
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc


@router.post("/agent-lock")
def acquire_agent_lock(
    paper_id: str,
    payload: ArchitectureLockRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        acquire_architecture_lock(
            session, paper_id, owner_type="agent", owner_id=payload.owner_id
        )
        return architecture_document_payload(session, paper_id)
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc


@router.post("/agent-lock/release")
def release_agent_lock(
    paper_id: str,
    payload: ArchitectureLockRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    release_architecture_lock(
        session, paper_id, owner_type="agent", owner_id=payload.owner_id
    )
    return architecture_document_payload(session, paper_id)


@router.post("/agent-commit")
def save_agent_architecture(
    paper_id: str,
    payload: ArchitectureAgentCommitRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    try:
        item, diagnostics = commit_architecture_scene(
            session,
            paper_id,
            payload.scene,
            expected_revision=payload.expected_revision,
            owner_type="agent",
            owner_id=payload.task_id,
        )
        log_operation(
            session,
            actor="agent",
            action="architecture.updated",
            entity_type="architecture",
            entity_id=paper_id,
            task_id=payload.task_id,
            detail={"revision": item.revision},
        )
        session.commit()
        return {**architecture_document_payload(session, paper_id), "diagnostics": diagnostics}
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc


@router.post("/commands")
def validate_commands(
    paper_id: str,
    payload: ArchitectureCommandRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    document = architecture_document_payload(session, paper_id)
    scene = document.get("scene")
    if document["status"] != "ready" or not isinstance(scene, dict):
        raise HTTPException(status_code=409, detail="架构尚未就绪")
    node_ids = {item["id"] for item in scene.get("nodes", [])}
    tensor_ids = {item["id"] for item in scene.get("tensors", [])}
    for command in payload.commands:
        missing: list[str] = []
        if command.target and command.target not in node_ids:
            missing.append(command.target)
        missing.extend(value for value in command.targets if value not in node_ids)
        missing.extend(value for value in command.except_ids if value not in node_ids)
        if command.tensor_id and command.tensor_id not in tensor_ids:
            missing.append(command.tensor_id)
        if missing:
            raise HTTPException(
                status_code=422,
                detail={"message": "控图命令引用了不存在的对象", "missingIds": missing},
            )
    return {
        "paperId": paper_id,
        "revision": document["revision"],
        "architectureCommands": [
            command.model_dump(mode="json", by_alias=True, exclude_none=True)
            for command in payload.commands
        ],
    }


@router.post("/generate")
def generate_architecture(
    paper_id: str,
    payload: ArchitectureGenerateRequest,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    _paper_or_404(session, paper_id)
    current = architecture_document_payload(session, paper_id)
    if current["status"] == "ready" and not payload.rebuild:
        return current
    task = TaskRun(
        kind="architecture_generation",
        payload_json={
            "paper_id": paper_id,
            "base_revision": current["revision"],
            "rebuild": payload.rebuild,
        },
        result_json={
            ARCHITECTURE_PROGRESS_KEY: {
                "activities": [
                    {
                        "id": "agent-startup",
                        "kind": "status",
                        "label": "正在启动 Agent",
                        "status": "running",
                    }
                ]
            }
        },
    )
    session.add(task)
    session.flush()
    try:
        item = acquire_architecture_lock(
            session, paper_id, owner_type="agent", owner_id=task.id
        )
    except ArchitectureError as exc:
        session.rollback()
        raise _architecture_error(exc) from exc
    item.status = "generating"
    item.generation_task_id = task.id
    item.error = None
    session.commit()
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, task.id)
    return architecture_document_payload(session, paper_id)


@router.post("/assets")
async def upload_architecture_asset(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    try:
        item = save_architecture_asset(
            session,
            paper_id,
            content=await file.read(),
            media_type=file.content_type or "application/octet-stream",
            original_name=file.filename or "image",
        )
    except ArchitectureError as exc:
        raise _architecture_error(exc) from exc
    return {
        **model_dict(item),
        "src": f"asset://architecture/{item.id}",
        "url": f"/api/papers/{paper_id}/architecture/assets/{item.id}",
    }


@router.get("/assets/{asset_id}")
def get_architecture_asset(
    paper_id: str,
    asset_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> FileResponse:
    try:
        item, path = architecture_asset_path(session, paper_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Architecture asset not found") from exc
    return FileResponse(
        path,
        media_type=item.media_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
