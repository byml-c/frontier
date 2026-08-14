from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from ..agent_runner import build_prompt, enqueue_task, run_task
from ..config import settings
from ..database import SessionLocal, get_session
from ..interactive_stream import encode_stream_event
from ..models import (
    Annotation,
    Paper,
    PaperNode,
    PaperTag,
    ReadingProgress,
    ResearchNode,
    Tag,
    TaskRun,
    TranslationCache,
    utcnow,
)
from ..schemas import (
    AnnotationCreate,
    ConfirmFeedback,
    FinishReading,
    IgnorePaper,
    PaperCreate,
    PaperMetadataUpdate,
    PaperUpdate,
    ReadingProgressUpdate,
    TagCreate,
    TranslationRequest,
)
from ..services.common import compact_search_key, log_operation, model_dict, normalize_title
from ..services.completion_summary import (
    COMPLETION_SUMMARY_METADATA_KEY,
    completion_summary_structured,
    migrate_legacy_completion_summary,
    save_completion_summary,
)
from ..services.document_reader import (
    document_asset,
    document_status,
    queue_document_conversion,
    queue_document_conversions,
)
from ..services.document_resources import RESOURCE_KINDS, structured_document_resources
from ..services.ingest import (
    create_or_update_paper,
    download_paper_pdf,
    extract_pdf_text,
    ingest_arxiv,
    ingest_pdf_bytes,
    ingest_url,
    render_pdf_page,
)
from ..services.manual_recommendations import (
    current_analysis_state,
    queue_paper_metadata_analysis,
    stage_manual_recommendation,
)
from ..services.translation_provider import stream_translation_prompt

router = APIRouter(prefix="/api/papers", tags=["papers"])
tags_router = APIRouter(prefix="/api/tags", tags=["tags"])


def _paper_or_404(session: Session, paper_id: str) -> Paper:
    paper = session.get(Paper, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


def _serialize_paper(session: Session, paper: Paper, *, detail: bool = False) -> dict:
    payload = model_dict(paper)
    payload["completion_summary_structured"] = completion_summary_structured(
        paper, include_user_attitude=True
    )
    tags = session.execute(
        select(Tag.id, Tag.name, Tag.color)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper.id)
        .order_by(Tag.name)
    ).all()
    nodes = session.execute(
        select(ResearchNode.id, ResearchNode.name, PaperNode.reason, PaperNode.is_key)
        .join(PaperNode, PaperNode.node_id == ResearchNode.id)
        .where(PaperNode.paper_id == paper.id)
        .order_by(ResearchNode.name)
    ).all()
    payload["tags"] = [dict(row._mapping) for row in tags]
    payload["nodes"] = [dict(row._mapping) for row in nodes]
    pdf_path = Path(paper.pdf_path) if paper.pdf_path else None
    payload["has_pdf"] = bool(pdf_path and pdf_path.exists())
    if pdf_path and pdf_path.exists():
        pdf_stat = pdf_path.stat()
        payload["pdf_version"] = f"{pdf_stat.st_mtime_ns:x}-{pdf_stat.st_size:x}"
    else:
        payload["pdf_version"] = None
    metadata = paper.metadata_json or {}
    analysis = current_analysis_state(paper)
    payload["analysis_status"] = analysis.get("status")
    payload["analysis_error"] = analysis.get("error")
    payload["suggested_tags"] = metadata.get("suggested_tags", [])
    if not detail:
        payload.pop("full_analysis", None)
        payload.pop("metadata_json", None)
    return payload


def _manual_ingest_result(
    session: Session,
    paper: Paper,
    created: bool,
    background_tasks: BackgroundTasks,
    *,
    analyze: bool,
) -> dict:
    brief, _item, task, task_created = stage_manual_recommendation(
        session, paper, queue_analysis=analyze
    )
    if task and task_created and settings.enable_codex_runs:
        background_tasks.add_task(run_task, task.id)
    return {
        "paper": _serialize_paper(session, paper, detail=True),
        "created": created,
        "brief_id": brief.id,
        "analysis_task_id": task.id if task else None,
    }


def _replace_paper_tags(
    session: Session,
    paper_id: str,
    tag_ids: list[str],
    new_tags: list[str],
) -> None:
    selected_tag_ids = set(tag_ids)
    for name in new_tags:
        normalized = name.strip()
        if not normalized:
            continue
        tag = session.scalar(select(Tag).where(func.lower(Tag.name) == normalized.lower()))
        if tag is None:
            tag = Tag(name=normalized)
            session.add(tag)
            session.flush()
        selected_tag_ids.add(tag.id)
    session.query(PaperTag).filter(PaperTag.paper_id == paper_id).delete()
    for tag_id in selected_tag_ids:
        if session.get(Tag, tag_id):
            session.add(PaperTag(paper_id=paper_id, tag_id=tag_id))


@router.get("")
def list_papers(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    status: str | None = None,
    node_id: str | None = None,
    tag_id: str | None = None,
    quality: bool | None = None,
    interested: bool | None = None,
    source: str | None = None,
    library: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    reading_status_order = case(
        (Paper.status == "feedback_pending", 0),
        (Paper.status == "reading", 1),
        (Paper.status == "candidate", 2),
        (Paper.status == "completed", 3),
        (Paper.status == "ignored", 4),
        else_=5,
    )
    statement = select(Paper).order_by(
        reading_status_order,
        Paper.published_at.desc(),
        Paper.created_at.desc(),
    )
    count_statement = select(func.count(func.distinct(Paper.id)))
    filters = []
    if q and (query := q.strip()):
        token = f"%{query}%"
        identifier = query.removeprefix("arXiv:").removeprefix("arxiv:").strip()
        identifier_token = f"%{identifier}%"
        compact_key = compact_search_key(query)
        compact_title = func.replace(
            func.replace(func.lower(Paper.normalized_title), " ", ""), "π", "pi"
        )
        search_clauses = [
            Paper.title.ilike(token),
            Paper.abstract.ilike(token),
            Paper.first_author.ilike(token),
            Paper.corresponding_author.ilike(token),
            Paper.primary_affiliation.ilike(token),
            cast(Paper.authors, Text).ilike(token),
            cast(Paper.metadata_json, Text).ilike(token),
            Paper.doi.ilike(token),
        ]
        if identifier:
            search_clauses.append(Paper.arxiv_id.ilike(identifier_token))
        if compact_key:
            search_clauses.append(compact_title.like(f"%{compact_key}%"))
        filters.append(or_(*search_clauses))
    if status:
        filters.append(Paper.status == status)
    if quality is not None:
        filters.append(Paper.is_quality == quality)
    if interested is not None:
        filters.append(Paper.is_interested == interested)
    if source:
        filters.append(Paper.source == source)
    if library:
        filters.append(Paper.status == "completed")
    if node_id:
        statement = statement.join(PaperNode, PaperNode.paper_id == Paper.id)
        count_statement = count_statement.join(PaperNode, PaperNode.paper_id == Paper.id)
        filters.append(PaperNode.node_id == node_id)
    if tag_id:
        statement = statement.join(PaperTag, PaperTag.paper_id == Paper.id)
        count_statement = count_statement.join(PaperTag, PaperTag.paper_id == Paper.id)
        filters.append(PaperTag.tag_id == tag_id)
    if filters:
        statement = statement.where(and_(*filters))
        count_statement = count_statement.where(and_(*filters))
    statement = statement.distinct().limit(limit).offset(offset)
    papers = session.scalars(statement).all()
    total = session.scalar(count_statement) or 0
    return {
        "items": [_serialize_paper(session, paper) for paper in papers],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("")
def create_paper(
    payload: PaperCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper, created = create_or_update_paper(session, payload, actor="agent")
    return {"paper": _serialize_paper(session, paper, detail=True), "created": created}


@router.post("/ingest/arxiv")
def add_arxiv(
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    value: str = Form(...),
    recommend: bool = Form(False),
    analyze: bool = Form(True),
) -> dict:
    try:
        paper, created = ingest_arxiv(session, value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"arXiv ingestion failed: {exc}") from exc
    background_tasks.add_task(queue_document_conversions, [paper.id], limit=1)
    if recommend:
        return _manual_ingest_result(
            session, paper, created, background_tasks, analyze=analyze
        )
    return {"paper": _serialize_paper(session, paper, detail=True), "created": created}


@router.post("/ingest/url")
def add_url(
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    url: str = Form(...),
    recommend: bool = Form(False),
    analyze: bool = Form(True),
) -> dict:
    try:
        paper, created = ingest_url(session, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"URL ingestion failed: {exc}") from exc
    background_tasks.add_task(queue_document_conversions, [paper.id], limit=1)
    if recommend:
        return _manual_ingest_result(
            session, paper, created, background_tasks, analyze=analyze
        )
    return {"paper": _serialize_paper(session, paper, detail=True), "created": created}


@router.post("/ingest/pdf")
async def add_pdf(
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    recommend: bool = Form(False),
    analyze: bool = Form(True),
) -> dict:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only PDF files are supported")
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF exceeds 100 MB")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    try:
        paper, created = ingest_pdf_bytes(session, content, file.filename or "upload.pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF ingestion failed: {exc}") from exc
    background_tasks.add_task(queue_document_conversions, [paper.id], limit=1)
    if recommend:
        return _manual_ingest_result(
            session, paper, created, background_tasks, analyze=analyze
        )
    return {"paper": _serialize_paper(session, paper, detail=True), "created": created}


@router.get("/{paper_id}")
def get_paper(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return _serialize_paper(session, _paper_or_404(session, paper_id), detail=True)


@router.patch("/{paper_id}")
def update_paper(
    paper_id: str,
    payload: PaperUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    changes = payload.model_dump(exclude_unset=True)
    suggested_tags = changes.pop("suggested_tags", None)
    if "metadata_json" in changes:
        current_metadata = paper.metadata_json or {}
        changes["metadata_json"] = {
            **(changes["metadata_json"] or {}),
            **{
                key: current_metadata[key]
                for key in (
                    "manual_analysis",
                    "agent_metadata_analysis",
                    COMPLETION_SUMMARY_METADATA_KEY,
                )
                if key in current_metadata
            },
        }
    if "title" in changes:
        changes["normalized_title"] = normalize_title(changes["title"])
    for key, value in changes.items():
        setattr(paper, key, value)
    if suggested_tags is not None:
        paper.metadata_json = {**(paper.metadata_json or {}), "suggested_tags": suggested_tags}
    log_operation(
        session,
        actor="agent",
        action="paper.updated",
        entity_type="paper",
        entity_id=paper.id,
        detail={"fields": sorted(changes)},
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.patch("/{paper_id}/metadata")
def update_paper_metadata(
    paper_id: str,
    payload: PaperMetadataUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    changes = payload.model_dump(exclude_unset=True)
    tag_ids = changes.pop("tag_ids", None)
    new_tags = changes.pop("new_tags", [])
    structured = changes.pop("completion_summary_structured", None)
    if "metadata_json" in changes:
        current_metadata = paper.metadata_json or {}
        changes["metadata_json"] = {
            **(changes["metadata_json"] or {}),
            **{
                key: current_metadata[key]
                for key in (
                    "manual_analysis",
                    "agent_metadata_analysis",
                    "suggested_tags",
                    COMPLETION_SUMMARY_METADATA_KEY,
                )
                if key in current_metadata
            },
        }
    if changes.get("title"):
        changes["normalized_title"] = normalize_title(changes["title"])
    for key, value in changes.items():
        setattr(paper, key, value)
    if structured is not None:
        save_completion_summary(paper, structured)
    if tag_ids is not None:
        _replace_paper_tags(session, paper.id, tag_ids, new_tags)
    log_operation(
        session,
        actor="user",
        action="paper.metadata_updated",
        entity_type="paper",
        entity_id=paper.id,
        detail={"fields": sorted(changes), "tags_updated": tag_ids is not None},
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/metadata/generate")
def generate_paper_metadata(
    paper_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    task, created = queue_paper_metadata_analysis(session, paper)
    if created and settings.enable_codex_runs:
        background_tasks.add_task(run_task, task.id)
    return {"id": task.id, "status": task.status, "created": created}


@router.post("/{paper_id}/ignore")
def ignore_paper(
    paper_id: str,
    payload: IgnorePaper,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    paper.status = "ignored"
    paper.ignored_reason = payload.reason
    log_operation(
        session,
        actor="user",
        action="paper.ignored",
        entity_type="paper",
        entity_id=paper.id,
        detail={"reason": payload.reason},
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/start-reading")
def start_reading(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    paper = _paper_or_404(session, paper_id)
    if paper.status not in {"completed", "feedback_pending"}:
        paper.status = "reading"
    log_operation(
        session,
        actor="user",
        action="paper.reading_started",
        entity_type="paper",
        entity_id=paper.id,
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/cancel-reading")
def cancel_reading(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    paper = _paper_or_404(session, paper_id)
    if paper.status not in {"reading", "feedback_pending", "completed"}:
        raise HTTPException(status_code=409, detail="Paper is not in a reading state")
    previous_status = paper.status
    paper.status = "candidate"
    paper.pending_feedback = {}
    paper.is_interested = False
    paper.is_quality = False
    paper.reading_completed_at = None
    removed_links = (
        session.query(PaperNode).filter(PaperNode.paper_id == paper.id).delete()
    )
    log_operation(
        session,
        actor="user",
        action="paper.reading_cancelled",
        entity_type="paper",
        entity_id=paper.id,
        detail={"previous_status": previous_status, "removed_node_links": removed_links},
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/finish")
def finish_reading(
    paper_id: str,
    payload: FinishReading,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    paper.status = "feedback_pending"
    structured = payload.completion_summary_structured
    if structured is not None:
        save_completion_summary(paper, structured.model_dump())
    elif payload.summary:
        paper.completion_summary = payload.summary
        metadata = dict(paper.metadata_json or {})
        metadata.pop(COMPLETION_SUMMARY_METADATA_KEY, None)
        paper.metadata_json = metadata
        migrate_legacy_completion_summary(paper)
    paper.pending_feedback = payload.model_dump(
        exclude={"summary", "completion_summary_structured"}
    )
    log_operation(
        session,
        actor="agent" if payload.summary or structured is not None else "user",
        action="paper.reading_summarized",
        entity_type="paper",
        entity_id=paper.id,
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/confirm-feedback")
def confirm_feedback(
    paper_id: str,
    payload: ConfirmFeedback,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    paper.status = "completed"
    paper.is_interested = payload.interested
    paper.is_quality = payload.quality
    paper.reading_completed_at = datetime.now(UTC)
    paper.pending_feedback = {}
    if payload.completion_summary_structured is not None:
        save_completion_summary(
            paper, payload.completion_summary_structured.model_dump()
        )
    if payload.personal_comment:
        paper.metadata_json = {**paper.metadata_json, "personal_comment": payload.personal_comment}
    selected_tag_ids = (
        set(payload.tag_ids)
        if payload.tag_ids is not None
        else set(
            session.scalars(select(PaperTag.tag_id).where(PaperTag.paper_id == paper.id)).all()
        )
    )
    for name in payload.new_tags:
        normalized = name.strip()
        if not normalized:
            continue
        tag = session.scalar(select(Tag).where(func.lower(Tag.name) == normalized.lower()))
        if tag is None:
            tag = Tag(name=normalized)
            session.add(tag)
            session.flush()
        selected_tag_ids.add(tag.id)
    session.query(PaperTag).filter(PaperTag.paper_id == paper.id).delete()
    for tag_id in selected_tag_ids:
        if session.get(Tag, tag_id):
            session.add(PaperTag(paper_id=paper.id, tag_id=tag_id))
    paper.metadata_json = {
        **paper.metadata_json,
        "create_node_requested": payload.create_node,
    }
    log_operation(
        session,
        actor="user",
        action="paper.feedback_confirmed",
        entity_type="paper",
        entity_id=paper.id,
        detail={
            "interested": payload.interested,
            "quality": payload.quality,
            "create_node": payload.create_node,
            "tag_count": len(selected_tag_ids),
        },
    )
    session.commit()
    task_id = enqueue_task(
        "graph_maintenance",
        {
            "message": (
                f"论文 {paper.id} 已完成用户确认的精读。请根据完整分析和用户反馈，将论文关联到"
                "已有的相关研究节点，并在确有增量时更新节点进展。"
                + (
                    "用户明确要求评估并在必要时创建新节点。"
                    if payload.create_node
                    else "用户未要求创建新节点，不要创建或拆分节点。"
                )
            ),
            "context": {
                "paper_id": paper.id,
                "create_node_requested": payload.create_node,
                "interested": payload.interested,
                "quality": payload.quality,
            },
        },
    )
    if settings.enable_codex_runs:
        background_tasks.add_task(run_task, task_id)
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/download-pdf")
def fetch_pdf(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    paper = _paper_or_404(session, paper_id)
    try:
        path = download_paper_pdf(session, paper)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PDF download failed: {exc}") from exc
    return {"paper_id": paper.id, "pdf_path": str(path), "url": f"/api/papers/{paper.id}/pdf"}


@router.get("/{paper_id}/pdf")
def get_pdf(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> FileResponse:
    paper = _paper_or_404(session, paper_id)
    try:
        path = download_paper_pdf(session, paper)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"PDF unavailable: {exc}") from exc
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{paper.id}.pdf",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{paper_id}/text")
def get_pdf_text(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    force: bool = False,
) -> dict:
    paper = _paper_or_404(session, paper_id)
    try:
        return extract_pdf_text(session, paper, force=force)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF extraction failed: {exc}") from exc


@router.get("/{paper_id}/document")
def get_document_reader(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    try:
        status = document_status(session, paper)
        if status.get("status") in {"missing", "processing"}:
            queue_document_conversion(paper_id)
            return {**status, "status": "processing"}
        return status
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Document conversion failed: {exc}") from exc


@router.post("/{paper_id}/document/rebuild")
def rebuild_document_reader(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _paper_or_404(session, paper_id)
    queued = queue_document_conversion(paper_id, force=True)
    return {"paper_id": paper_id, "status": "processing", "queued": queued}


@router.get("/{paper_id}/document/resources")
def get_document_resources(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    kind: str | None = Query(default=None),
    block_id: str | None = Query(default=None),
) -> dict:
    paper = _paper_or_404(session, paper_id)
    if kind and kind not in RESOURCE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of: {', '.join(sorted(RESOURCE_KINDS))}",
        )
    try:
        document = document_status(session, paper)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Document lookup failed: {exc}") from exc
    if document.get("status") != "ready":
        if document.get("status") in {"missing", "processing"}:
            queue_document_conversion(paper_id)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "结构化论文资源尚未就绪，请稍后重试",
                "document_status": document,
            },
        )
    return structured_document_resources(
        document,
        kind=kind,
        block_id=block_id,
        asset_resolver=lambda filename: document_asset(paper_id, filename),
    )


@router.get("/{paper_id}/document/assets/{filename}")
def get_document_asset(
    paper_id: str,
    filename: str,
    session: Annotated[Session, Depends(get_session)],
) -> FileResponse:
    _paper_or_404(session, paper_id)
    try:
        path = document_asset(paper_id, filename)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Document asset not found") from exc
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.post("/{paper_id}/render-page")
def render_page(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    page_number: int = Query(ge=1),
    dpi: int = Query(default=144, ge=72, le=300),
) -> dict:
    paper = _paper_or_404(session, paper_id)
    try:
        path = render_pdf_page(session, paper, page_number, dpi=dpi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF page rendering failed: {exc}") from exc
    return {
        "paper_id": paper.id,
        "page_number": page_number,
        "dpi": dpi,
        "path": str(path.resolve()),
    }


@router.post("/{paper_id}/translate")
def translate_selection(
    paper_id: str,
    payload: TranslationRequest,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _paper_or_404(session, paper_id)
    task_id = enqueue_task(
        "translate",
        {
            "paper_id": paper_id,
            "text": payload.text,
            "page_number": payload.page_number,
            "target_language": payload.target_language,
        },
    )
    background_tasks.add_task(run_task, task_id)
    return {"id": task_id, "status": "queued"}


def _translation_key(paper_id: str, payload: TranslationRequest) -> str:
    normalized = {
        "paper_id": paper_id,
        "page_number": payload.page_number,
        "text": " ".join(payload.text.split()),
        "target_language": payload.target_language,
    }
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/{paper_id}/translate/stream")
def stream_translation(
    paper_id: str,
    payload: TranslationRequest,
    session: Annotated[Session, Depends(get_session)],
) -> StreamingResponse:
    _paper_or_404(session, paper_id)
    selection_key = _translation_key(paper_id, payload)
    cached = session.scalar(
        select(TranslationCache).where(TranslationCache.selection_key == selection_key)
    )
    if cached is None:
        cached = session.scalar(
            select(TranslationCache)
            .where(
                TranslationCache.paper_id == paper_id,
                TranslationCache.page_number == payload.page_number,
                TranslationCache.selected_text == payload.text,
                TranslationCache.target_language == payload.target_language,
            )
            .order_by(TranslationCache.created_at.desc())
        )
    if cached:

        def cached_result():  # type: ignore[no-untyped-def]
            yield encode_stream_event("meta", cached=True, cache_id=cached.id)
            yield encode_stream_event("delta", delta=cached.content)
            yield encode_stream_event("done", content=cached.content, cached=True)

        return StreamingResponse(
            cached_result(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    task = TaskRun(
        kind="translate",
        payload_json={
            "paper_id": paper_id,
            "text": payload.text,
            "page_number": payload.page_number,
            "target_language": payload.target_language,
            "page_context": payload.page_context,
        },
    )
    session.add(task)
    session.commit()

    def generate():  # type: ignore[no-untyped-def]
        yield encode_stream_event("meta", cached=False, task_id=task.id)
        content = ""
        try:
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "running"
                    running.started_at = utcnow()
                    runner_session.commit()
            for delta in stream_translation_prompt(build_prompt(task)):
                content += delta
                yield encode_stream_event("delta", delta=delta)
            with SessionLocal() as runner_session:
                runner_session.add(
                    TranslationCache(
                        selection_key=selection_key,
                        paper_id=paper_id,
                        page_number=payload.page_number,
                        selected_text=payload.text,
                        target_language=payload.target_language,
                        content=content,
                        position_json=payload.position_json,
                    )
                )
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "completed"
                    running.result_json = {"response": content, "cached": False}
                    running.finished_at = utcnow()
                runner_session.commit()
            yield encode_stream_event("done", content=content, cached=False)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "failed"
                    running.error = str(exc)
                    running.finished_at = utcnow()
                    runner_session.commit()
            yield encode_stream_event("error", message=str(exc))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{paper_id}/annotations")
def list_annotations(
    paper_id: str, session: Annotated[Session, Depends(get_session)]
) -> list[dict]:
    _paper_or_404(session, paper_id)
    items = session.scalars(
        select(Annotation)
        .where(Annotation.paper_id == paper_id)
        .order_by(Annotation.page_number, Annotation.created_at)
    ).all()
    return [model_dict(item) for item in items]


@router.post("/{paper_id}/annotations")
def create_annotation(
    paper_id: str,
    payload: AnnotationCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _paper_or_404(session, paper_id)
    values = payload.model_dump()
    if values["kind"] == "highlight":
        values["kind"] = "note"
        values["content"] = values["content"] or ""
    annotation = Annotation(paper_id=paper_id, **values)
    session.add(annotation)
    session.flush()
    log_operation(
        session,
        actor="user",
        action="annotation.created",
        entity_type="annotation",
        entity_id=annotation.id,
        detail={"paper_id": paper_id, "kind": annotation.kind},
    )
    session.commit()
    return model_dict(annotation)


@router.patch("/{paper_id}/annotations/{annotation_id}")
def update_annotation(
    paper_id: str,
    annotation_id: str,
    payload: AnnotationCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = session.get(Annotation, annotation_id)
    if not item or item.paper_id != paper_id:
        raise HTTPException(status_code=404, detail="Annotation not found")
    values = payload.model_dump()
    if values["kind"] == "highlight":
        values["kind"] = "note"
        values["content"] = values["content"] or ""
    for key, value in values.items():
        setattr(item, key, value)
    session.commit()
    return model_dict(item)


@router.delete("/{paper_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(
    paper_id: str,
    annotation_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    item = session.get(Annotation, annotation_id)
    if not item or item.paper_id != paper_id:
        raise HTTPException(status_code=404, detail="Annotation not found")
    session.delete(item)
    session.commit()


@router.get("/{paper_id}/progress")
def get_progress(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    _paper_or_404(session, paper_id)
    item = session.get(ReadingProgress, paper_id)
    if item is None:
        item = ReadingProgress(paper_id=paper_id)
        session.add(item)
        session.commit()
    return model_dict(item)


@router.put("/{paper_id}/progress")
def save_progress(
    paper_id: str,
    payload: ReadingProgressUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _paper_or_404(session, paper_id)
    item = session.get(ReadingProgress, paper_id)
    if item is None:
        item = ReadingProgress(paper_id=paper_id)
        session.add(item)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    session.commit()
    return model_dict(item)


@tags_router.get("")
def list_tags(session: Annotated[Session, Depends(get_session)]) -> list[dict]:
    items = session.scalars(select(Tag).order_by(Tag.name)).all()
    return [model_dict(item) for item in items]


@tags_router.post("")
def create_tag(payload: TagCreate, session: Annotated[Session, Depends(get_session)]) -> dict:
    existing = session.scalar(
        select(Tag).where(func.lower(Tag.name) == payload.name.strip().lower())
    )
    if existing:
        return model_dict(existing)
    item = Tag(name=payload.name.strip(), color=payload.color)
    session.add(item)
    session.commit()
    return model_dict(item)
