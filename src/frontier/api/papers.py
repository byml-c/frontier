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
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import Text, and_, case, cast, func, or_, select, update
from sqlalchemy.orm import Session

from ..agent_runner import build_prompt, enqueue_task
from ..config import settings
from ..database import SessionLocal, get_session
from ..interactive_stream import cancel_codex_run, encode_stream_event
from ..models import (
    Annotation,
    Conversation,
    NodeProgress,
    Paper,
    PaperNode,
    PaperTag,
    ReadingProgress,
    ResearchGroup,
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
    PaperAgentContextSyncRequest,
    PaperCreate,
    PaperMetadataEnrichmentComplete,
    PaperMetadataUpdate,
    PaperNarrationCommit,
    PaperUpdate,
    ReadingProgressUpdate,
    StartReading,
    TagCreate,
    TranslationRequest,
)
from ..services.abstract_translation import (
    ABSTRACT_TRANSLATION_STATE_KEY,
    abstract_translation,
    queue_paper_abstract_translation,
)
from ..services.author_metadata import (
    BIBLIOGRAPHIC_FIELDS,
    BIBLIOGRAPHIC_PROVENANCE_KEY,
    MANUAL_BIBLIOGRAPHIC_FIELDS_KEY,
    manual_bibliographic_fields,
    preserve_author_details_for_unchanged_names,
    record_manual_bibliographic_changes,
)
from ..services.common import (
    AGENT_BIBLIOGRAPHY_KEY,
    RESEARCH_GROUP_PROFILE_KEY,
    RESEARCH_GROUP_SUMMARY_KEY,
    compact_search_key,
    log_operation,
    model_dict,
    normalize_title,
)
from ..services.completion_summary import (
    COMPLETION_SUMMARY_METADATA_KEY,
    completion_summary_structured,
    migrate_legacy_completion_summary,
    render_completion_summary,
    save_completion_summary,
)
from ..services.document_reader import (
    document_asset,
    document_status,
    queue_document_conversion,
)
from ..services.document_resources import RESOURCE_KINDS, structured_document_resources
from ..services.first_page import paper_first_page_text
from ..services.ingest import (
    create_or_update_paper,
    download_paper_pdf,
    extract_pdf_text,
    ingest_arxiv,
    ingest_pdf_bytes,
    ingest_url,
    render_pdf_page,
)
from ..services.interest_evidence import (
    FEEDBACK_PROVENANCE_KEY,
    IGNORE_PROVENANCE_KEY,
    READING_PROVENANCE_KEY,
    feedback_payload_hash,
    feedback_payload_hash_v2,
    feedback_payload_hash_v3,
    paper_interest_level,
    paper_quality_level,
    validate_brief_item_source,
)
from ..services.manual_recommendations import (
    current_analysis_state,
    queue_paper_metadata_analysis,
    stage_manual_recommendation,
)
from ..services.paper_agent_context import (
    build_paper_agent_context,
    sync_paper_agent_context,
)
from ..services.paper_metadata import queue_metadata_enrichment
from ..services.paper_sources import arxiv_html_url, arxiv_pdf_url
from ..services.podcast_content import (
    PodcastContentError,
    PodcastContentNotFoundError,
    PodcastContentRevisionError,
    PodcastContentSourceError,
    PodcastContentValidationError,
    commit_paper_narration,
    narration_payload,
)
from ..services.research_groups import (
    assign_research_group,
    clear_research_group,
    group_for_paper,
    serialize_group,
    upsert_group_from_paper,
)
from ..services.task_control import ACTIVE_TASK_STATUSES, serialized_task_claim
from ..services.task_dispatcher import submit_task
from ..services.translation_provider import stream_translation_prompt

router = APIRouter(prefix="/api/papers", tags=["papers"])
tags_router = APIRouter(prefix="/api/tags", tags=["tags"])


def _paper_or_404(session: Session, paper_id: str) -> Paper:
    paper = session.get(Paper, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


def _podcast_content_error(exc: PodcastContentError) -> HTTPException:
    if isinstance(exc, PodcastContentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PodcastContentRevisionError):
        return HTTPException(
            status_code=409,
            detail={
                "message": "文字稿已更新，请读取最新版本后重试",
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        )
    if isinstance(exc, PodcastContentSourceError):
        return HTTPException(
            status_code=409,
            detail={
                "message": "论文来源内容已变化，请基于最新内容重新生成文字稿",
                "expected_source_digest": exc.expected,
                "actual_source_digest": exc.actual,
            },
        )
    if isinstance(exc, PodcastContentValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


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
    payload["pdf_source_url"] = arxiv_pdf_url(paper) or (
        f"/api/papers/{paper.id}/pdf" if payload["has_pdf"] else None
    )
    payload["html_source_url"] = arxiv_html_url(paper)
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
    payload["interest_level"] = paper_interest_level(paper)
    payload["quality_level"] = paper_quality_level(paper)
    screening = paper.screening_feedback or {}
    payload["screening_interest_level"] = screening.get("interest_level")
    payload["screening_quality_level"] = screening.get("quality_level")
    payload["screening_feedback"] = screening.get("comment")
    legacy_confirmed = bool(screening.get("agent_score_confirmed", False))
    payload["agent_quality_confirmed"] = bool(
        screening.get("agent_quality_confirmed", legacy_confirmed)
    )
    payload["agent_interest_confirmed"] = bool(
        screening.get("agent_interest_confirmed", legacy_confirmed)
    )
    payload["agent_score_confirmed"] = (
        payload["agent_quality_confirmed"] and payload["agent_interest_confirmed"]
    )
    payload["open_questions"] = metadata.get("open_questions")
    payload["research_group_summary"] = metadata.get(RESEARCH_GROUP_SUMMARY_KEY)
    group = group_for_paper(session, paper.id)
    payload["research_group"] = serialize_group(session, group) if group else None
    translation = abstract_translation(session, paper)
    translation_state = metadata.get(ABSTRACT_TRANSLATION_STATE_KEY)
    payload["abstract_translation"] = translation.content if translation else None
    payload["abstract_translation_status"] = (
        "ready"
        if translation
        else translation_state.get("status", "missing")
        if isinstance(translation_state, dict)
        else "missing"
    )
    payload["abstract_translation_error"] = (
        translation_state.get("error") if isinstance(translation_state, dict) else None
    )
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
        session,
        paper,
        queue_analysis=analyze and settings.enable_codex_runs,
    )
    if task and task_created:
        background_tasks.add_task(submit_task, task.id)
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


def _prospective_tag_names(
    session: Session,
    paper_id: str,
    tag_ids: list[str] | None,
    new_tags: list[str],
) -> list[str]:
    selected_ids = (
        set(tag_ids)
        if tag_ids is not None
        else set(
            session.scalars(select(PaperTag.tag_id).where(PaperTag.paper_id == paper_id)).all()
        )
    )
    names = [
        name for name in session.scalars(select(Tag.name).where(Tag.id.in_(selected_ids))).all()
    ]
    names.extend(name.strip() for name in new_tags if name.strip())
    return sorted(set(names), key=str.casefold)


def _prospective_node_selections(
    session: Session,
    payload: ConfirmFeedback,
) -> list[dict[str, object]]:
    selections: dict[str, dict[str, object]] = {}
    for link in payload.node_links:
        node = session.get(ResearchNode, link.node_id)
        if node is None or not node.is_active:
            raise ValueError(f"Research node not found or inactive: {link.node_id}")
        selections[f"existing:{node.id}"] = {
            "kind": "existing",
            "node_id": node.id,
            "reason": (link.reason or "").strip() or None,
            "is_key": link.is_key,
        }
    for proposed in payload.new_nodes:
        name = proposed.name.strip()
        core_question = proposed.core_question.strip()
        if not name or not core_question:
            raise ValueError("New research nodes require a name and core question")
        selections[f"new:{name.casefold()}"] = {
            "kind": "new",
            "name": name,
            "core_question": core_question,
            "summary": (proposed.summary or "").strip() or None,
            "domain": (proposed.domain or "").strip() or None,
            "reason": (proposed.reason or "").strip() or None,
            "is_key": proposed.is_key,
        }
    return [selections[key] for key in sorted(selections)]


def _apply_node_selections(
    session: Session,
    paper: Paper,
    selections: list[dict[str, object]],
) -> list[str]:
    session.query(PaperNode).filter(PaperNode.paper_id == paper.id).delete()
    resolved: dict[str, tuple[ResearchNode, dict[str, object]]] = {}
    for selection in selections:
        node: ResearchNode | None = None
        if selection["kind"] == "existing":
            node = session.get(ResearchNode, str(selection["node_id"]))
        else:
            name = str(selection["name"])
            node = session.scalar(
                select(ResearchNode).where(func.lower(ResearchNode.name) == name.lower())
            )
            if node is None:
                node = ResearchNode(
                    name=name,
                    core_question=str(selection["core_question"]),
                    summary=selection.get("summary"),
                    domain=selection.get("domain"),
                )
                session.add(node)
                session.flush()
        if node is None:
            raise ValueError("Selected research node disappeared during confirmation")
        resolved[node.id] = (node, selection)
    for node, selection in resolved.values():
        session.add(
            PaperNode(
                paper_id=paper.id,
                node_id=node.id,
                reason=selection.get("reason"),
                is_key=bool(selection.get("is_key")),
            )
        )
    session.flush()
    return list(resolved)


def _replace_selected_node_progress(
    session: Session,
    paper: Paper,
    selected_node_ids: list[str],
    structured: dict[str, str | None] | None,
) -> None:
    """Project the confirmed paper summary onto exactly the user-selected nodes."""
    session.query(NodeProgress).filter(NodeProgress.paper_id == paper.id).delete()
    if not selected_node_ids:
        return
    content = (
        (render_completion_summary(structured, include_user_attitude=False) if structured else None)
        or paper.summary
        or "该论文已完成精读，并由用户确认归入此研究节点。"
    )
    title = f"精读：{paper.title}"[:255]
    for node_id in selected_node_ids:
        session.add(
            NodeProgress(
                node_id=node_id,
                paper_id=paper.id,
                title=title,
                content=content,
                happened_at=paper.published_at,
            )
        )


def _confirmed_feedback_hash(
    session: Session,
    paper: Paper,
    payload: ConfirmFeedback,
    source_brief_item_id: str | None,
    node_selections: list[dict[str, object]],
) -> str:
    structured = (
        payload.completion_summary_structured.model_dump()
        if payload.completion_summary_structured is not None
        else completion_summary_structured(paper, include_user_attitude=True)
    )
    metadata = paper.metadata_json or {}
    personal_comment = (
        payload.personal_comment
        if payload.personal_comment is not None
        else metadata.get("personal_comment")
    )
    return feedback_payload_hash(
        interest_level=payload.resolved_interest_level,
        quality_level=payload.resolved_quality_level,
        create_node=payload.create_node,
        tag_names=_prospective_tag_names(session, paper.id, payload.tag_ids, payload.new_tags),
        node_selections=node_selections,
        user_attitude=(structured or {}).get("user_attitude"),
        personal_comment=(personal_comment if isinstance(personal_comment, str) else None),
        source_brief_item_id=source_brief_item_id,
    )


def _confirmed_feedback_hash_v2(
    session: Session,
    paper: Paper,
    payload: ConfirmFeedback,
    source_brief_item_id: str | None,
) -> str:
    structured = (
        payload.completion_summary_structured.model_dump()
        if payload.completion_summary_structured is not None
        else completion_summary_structured(paper, include_user_attitude=True)
    )
    metadata = paper.metadata_json or {}
    personal_comment = (
        payload.personal_comment
        if payload.personal_comment is not None
        else metadata.get("personal_comment")
    )
    return feedback_payload_hash_v2(
        interested=payload.resolved_interest_level == "interested",
        quality=payload.resolved_quality_level == "excellent",
        create_node=payload.create_node,
        tag_names=_prospective_tag_names(session, paper.id, payload.tag_ids, payload.new_tags),
        user_attitude=(structured or {}).get("user_attitude"),
        personal_comment=(personal_comment if isinstance(personal_comment, str) else None),
        source_brief_item_id=source_brief_item_id,
    )


def _confirmed_feedback_hash_v3(
    session: Session,
    paper: Paper,
    payload: ConfirmFeedback,
    source_brief_item_id: str | None,
    node_selections: list[dict[str, object]],
) -> str:
    structured = (
        payload.completion_summary_structured.model_dump()
        if payload.completion_summary_structured is not None
        else completion_summary_structured(paper, include_user_attitude=True)
    )
    metadata = paper.metadata_json or {}
    personal_comment = (
        payload.personal_comment
        if payload.personal_comment is not None
        else metadata.get("personal_comment")
    )
    return feedback_payload_hash_v3(
        interest_level=payload.resolved_interest_level,
        quality=payload.resolved_quality_level == "excellent",
        create_node=payload.create_node,
        tag_names=_prospective_tag_names(session, paper.id, payload.tag_ids, payload.new_tags),
        node_selections=node_selections,
        user_attitude=(structured or {}).get("user_attitude"),
        personal_comment=(personal_comment if isinstance(personal_comment, str) else None),
        source_brief_item_id=source_brief_item_id,
    )


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
    queue_metadata_enrichment(session, [paper.id], reason="arxiv-ingest")
    if recommend:
        return _manual_ingest_result(session, paper, created, background_tasks, analyze=analyze)
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
    queue_metadata_enrichment(session, [paper.id], reason="url-ingest")
    if recommend:
        return _manual_ingest_result(session, paper, created, background_tasks, analyze=analyze)
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
    content = await file.read(settings.paper_download_max_bytes + 1)
    if len(content) > settings.paper_download_max_bytes:
        raise HTTPException(status_code=413, detail="PDF exceeds the configured size limit")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    try:
        paper, created = ingest_pdf_bytes(session, content, file.filename or "upload.pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF ingestion failed: {exc}") from exc
    queue_metadata_enrichment(session, [paper.id], reason="upload-ingest")
    if recommend:
        return _manual_ingest_result(session, paper, created, background_tasks, analyze=analyze)
    return {"paper": _serialize_paper(session, paper, detail=True), "created": created}


@router.get("/{paper_id}")
def get_paper(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return _serialize_paper(session, _paper_or_404(session, paper_id), detail=True)


@router.get("/{paper_id}/metadata-context")
def get_paper_metadata_context(
    paper_id: str, session: Annotated[Session, Depends(get_session)]
) -> dict:
    """Compact, non-analysis context for the ingestion metadata Agent."""
    paper = _paper_or_404(session, paper_id)
    group = group_for_paper(session, paper.id)
    return {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors or [],
        "first_author": paper.first_author,
        "corresponding_author": paper.corresponding_author,
        "primary_affiliation": paper.primary_affiliation,
        "arxiv_id": paper.arxiv_id,
        "arxiv_version": paper.arxiv_version,
        "source": paper.source,
        "source_url": paper.source_url,
        "pdf_url": paper.pdf_url,
        "research_group_id": paper.research_group_id,
        "research_group": serialize_group(session, group, detail=True) if group else None,
        "metadata_enrichment": (paper.metadata_json or {}).get("metadata_enrichment"),
    }


@router.get("/{paper_id}/first-page")
def get_paper_first_page(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    max_chars: int = Query(default=2500, ge=200, le=8000),
) -> dict:
    """Expose a short affiliation snippet without retaining the PDF or HTML."""
    return paper_first_page_text(
        session, _paper_or_404(session, paper_id), max_chars=max_chars
    )


@router.post("/{paper_id}/metadata-enrichment/complete")
def complete_paper_metadata_enrichment(
    paper_id: str,
    payload: PaperMetadataEnrichmentComplete,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    state = dict((paper.metadata_json or {}).get("metadata_enrichment") or {})
    note = payload.note.strip() if payload.note and payload.note.strip() else None
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        "metadata_enrichment": {
            **state,
            "status": payload.status,
            "note": note,
            "error": note if payload.status == "failed" else None,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }
    log_operation(
        session,
        actor="agent",
        action="paper.metadata_enrichment_completed",
        entity_type="paper",
        entity_id=paper.id,
        detail={"status": payload.status, "note": note},
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/metadata-enrichment/retry")
def retry_paper_metadata_enrichment(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    state = dict((paper.metadata_json or {}).get("metadata_enrichment") or {})
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        "metadata_enrichment": {
            **state,
            "status": "pending",
            "task_id": None,
            "error": None,
            "requested_at": datetime.now(UTC).isoformat(),
        },
    }
    log_operation(
        session,
        actor="user",
        action="paper.metadata_enrichment_retried",
        entity_type="paper",
        entity_id=paper.id,
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/metadata-enrichment/retry-failed")
def retry_failed_metadata_enrichment(
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    papers = list(
        session.scalars(
            select(Paper).where(
                Paper.metadata_json["metadata_enrichment"]["status"].as_string()
                == "failed"
            )
        ).all()
    )
    now = datetime.now(UTC).isoformat()
    for paper in papers:
        state = dict((paper.metadata_json or {}).get("metadata_enrichment") or {})
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            "metadata_enrichment": {
                **state,
                "status": "pending",
                "task_id": None,
                "error": None,
                "requested_at": now,
            },
        }
    if papers:
        log_operation(
            session,
            actor="user",
            action="paper.metadata_enrichment_retried",
            entity_type="metadata_batch",
            detail={"paper_count": len(papers)},
        )
    session.commit()
    return {"retried": len(papers)}


@router.get("/{paper_id}/narration")
def get_paper_narration(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    language: str = Query(default="zh-CN", min_length=2, max_length=35),
) -> dict:
    try:
        return narration_payload(session, paper_id, language=language)
    except PodcastContentError as exc:
        raise _podcast_content_error(exc) from exc


@router.post("/{paper_id}/narration/commit")
@serialized_task_claim
def save_paper_narration(
    paper_id: str,
    payload: PaperNarrationCommit,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    try:
        item, changed = commit_paper_narration(session, paper_id, payload)
        if changed:
            log_operation(
                session,
                actor="agent" if payload.task_id else "user",
                action="paper_narration.committed",
                entity_type="paper_narration",
                entity_id=item.id,
                task_id=payload.task_id,
                detail={"paper_id": paper_id, "revision": item.revision},
            )
        return narration_payload(session, paper_id, language=payload.language)
    except PodcastContentError as exc:
        raise _podcast_content_error(exc) from exc


@router.get("/{paper_id}/agent-context")
def get_paper_agent_context(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    query: str | None = Query(None, max_length=4_000),
) -> dict:
    paper = _paper_or_404(session, paper_id)
    return build_paper_agent_context(session, paper, query=query)


@router.post("/{paper_id}/agent-context/sync")
def sync_agent_context(
    paper_id: str,
    payload: PaperAgentContextSyncRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    conversation = session.get(Conversation, payload.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.kind != "paper" or conversation.reference_id != paper_id:
        raise HTTPException(
            status_code=409,
            detail="Conversation does not belong to this paper",
        )
    result = sync_paper_agent_context(
        session,
        paper,
        conversation,
        query=payload.query,
    )
    session.commit()
    return result


@router.patch("/{paper_id}")
def update_paper(
    paper_id: str,
    payload: PaperUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    changes = payload.model_dump(exclude_unset=True)
    suggested_tags = changes.pop("suggested_tags", None)
    research_group_summary = changes.pop("research_group_summary", None)
    update_research_group_summary = "research_group_summary" in payload.model_fields_set
    research_group_id = changes.pop("research_group_id", None)
    update_research_group_id = "research_group_id" in payload.model_fields_set
    open_questions = changes.pop("open_questions", None)
    update_open_questions = "open_questions" in payload.model_fields_set
    protected_fields = manual_bibliographic_fields(session, paper)
    skipped_fields = sorted(protected_fields.intersection(changes))
    for field in skipped_fields:
        changes.pop(field, None)
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
                    BIBLIOGRAPHIC_PROVENANCE_KEY,
                    MANUAL_BIBLIOGRAPHIC_FIELDS_KEY,
                    READING_PROVENANCE_KEY,
                    FEEDBACK_PROVENANCE_KEY,
                    IGNORE_PROVENANCE_KEY,
                    "screening_feedback",
                    "personal_comment",
                    "open_questions",
                    RESEARCH_GROUP_SUMMARY_KEY,
                    RESEARCH_GROUP_PROFILE_KEY,
                    ABSTRACT_TRANSLATION_STATE_KEY,
                )
                if key in current_metadata
            },
        }
        if (
            AGENT_BIBLIOGRAPHY_KEY not in changes["metadata_json"]
            and AGENT_BIBLIOGRAPHY_KEY in current_metadata
        ):
            changes["metadata_json"][AGENT_BIBLIOGRAPHY_KEY] = current_metadata[
                AGENT_BIBLIOGRAPHY_KEY
            ]
    if "title" in changes:
        changes["normalized_title"] = normalize_title(changes["title"])
    for key, value in changes.items():
        setattr(paper, key, value)
    if update_open_questions:
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            "open_questions": open_questions.strip()
            if isinstance(open_questions, str) and open_questions.strip()
            else None,
        }
    if update_research_group_summary:
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            RESEARCH_GROUP_SUMMARY_KEY: (
                research_group_summary.strip()
                if isinstance(research_group_summary, str) and research_group_summary.strip()
                else None
            ),
        }
    if suggested_tags is not None:
        paper.metadata_json = {**(paper.metadata_json or {}), "suggested_tags": suggested_tags}
    if update_research_group_id:
        if research_group_id:
            group = session.get(ResearchGroup, research_group_id)
            if group is None:
                raise HTTPException(status_code=404, detail="Research group not found")
            assign_research_group(session, paper, group)
        else:
            clear_research_group(session, paper)
    else:
        upsert_group_from_paper(session, paper)
    log_operation(
        session,
        actor="agent",
        action="paper.updated",
        entity_type="paper",
        entity_id=paper.id,
        detail={"fields": sorted(changes), "protected_fields_skipped": skipped_fields},
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
    open_questions = changes.pop("open_questions", None)
    research_group_summary = changes.pop("research_group_summary", None)
    update_research_group_summary = "research_group_summary" in payload.model_fields_set
    research_group_id = changes.pop("research_group_id", None)
    update_research_group_id = "research_group_id" in payload.model_fields_set
    update_open_questions = "open_questions" in payload.model_fields_set
    interest_level = changes.pop("interest_level", None)
    quality_level = changes.pop("quality_level", None)
    screening_feedback = changes.pop("screening_feedback", None)
    agent_score_confirmed = changes.pop("agent_score_confirmed", None)
    agent_quality_confirmed = changes.pop("agent_quality_confirmed", None)
    agent_interest_confirmed = changes.pop("agent_interest_confirmed", None)
    update_agent_score_confirmation = "agent_score_confirmed" in payload.model_fields_set
    update_agent_quality_confirmation = "agent_quality_confirmed" in payload.model_fields_set
    update_agent_interest_confirmation = "agent_interest_confirmed" in payload.model_fields_set
    if update_agent_score_confirmation:
        if not update_agent_quality_confirmation:
            agent_quality_confirmed = agent_score_confirmed
            update_agent_quality_confirmation = True
        if not update_agent_interest_confirmation:
            agent_interest_confirmed = agent_score_confirmed
            update_agent_interest_confirmation = True
    original_values = {field: getattr(paper, field) for field in BIBLIOGRAPHIC_FIELDS}
    if "authors" in changes and changes["authors"] is not None:
        changes["authors"] = preserve_author_details_for_unchanged_names(
            paper.authors or [], changes["authors"]
        )
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
                    BIBLIOGRAPHIC_PROVENANCE_KEY,
                    MANUAL_BIBLIOGRAPHIC_FIELDS_KEY,
                    READING_PROVENANCE_KEY,
                    FEEDBACK_PROVENANCE_KEY,
                    IGNORE_PROVENANCE_KEY,
                    "screening_feedback",
                    "personal_comment",
                    "open_questions",
                    RESEARCH_GROUP_SUMMARY_KEY,
                    RESEARCH_GROUP_PROFILE_KEY,
                    ABSTRACT_TRANSLATION_STATE_KEY,
                )
                if key in current_metadata
            },
        }
        if (
            AGENT_BIBLIOGRAPHY_KEY not in changes["metadata_json"]
            and AGENT_BIBLIOGRAPHY_KEY in current_metadata
        ):
            changes["metadata_json"][AGENT_BIBLIOGRAPHY_KEY] = current_metadata[
                AGENT_BIBLIOGRAPHY_KEY
            ]
    if changes.get("title"):
        changes["normalized_title"] = normalize_title(changes["title"])
    for key, value in changes.items():
        setattr(paper, key, value)
    manually_changed = record_manual_bibliographic_changes(paper, original_values, changes)
    if structured is not None:
        save_completion_summary(paper, structured)
    if update_open_questions:
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            "open_questions": open_questions.strip()
            if isinstance(open_questions, str) and open_questions.strip()
            else None,
        }
    if update_research_group_summary:
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            RESEARCH_GROUP_SUMMARY_KEY: (
                research_group_summary.strip()
                if isinstance(research_group_summary, str) and research_group_summary.strip()
                else None
            ),
        }
    if tag_ids is not None:
        _replace_paper_tags(session, paper.id, tag_ids, new_tags)
    if update_research_group_id:
        if research_group_id:
            group = session.get(ResearchGroup, research_group_id)
            if group is None:
                raise HTTPException(status_code=404, detail="Research group not found")
            assign_research_group(session, paper, group)
        else:
            clear_research_group(session, paper)
    else:
        upsert_group_from_paper(session, paper)
    if (
        interest_level is not None
        or quality_level is not None
        or screening_feedback is not None
        or update_agent_score_confirmation
        or update_agent_quality_confirmation
        or update_agent_interest_confirmation
    ):
        if paper.status != "completed":
            current_screening = dict(paper.screening_feedback or {})
            legacy_confirmed = bool(current_screening.get("agent_score_confirmed", False))
            current_screening.setdefault("agent_quality_confirmed", legacy_confirmed)
            current_screening.setdefault("agent_interest_confirmed", legacy_confirmed)
            if interest_level is not None:
                current_screening["interest_level"] = interest_level
            if quality_level is not None:
                current_screening["quality_level"] = quality_level
            if screening_feedback is not None:
                current_screening["comment"] = screening_feedback.strip() or None
            if update_agent_score_confirmation:
                current_screening["agent_score_confirmed"] = bool(agent_score_confirmed)
            if update_agent_quality_confirmation:
                current_screening["agent_quality_confirmed"] = bool(agent_quality_confirmed)
            if update_agent_interest_confirmation:
                current_screening["agent_interest_confirmed"] = bool(agent_interest_confirmed)
            current_screening["agent_score_confirmed"] = bool(
                current_screening.get("agent_quality_confirmed", False)
                and current_screening.get("agent_interest_confirmed", False)
            )
            current_screening["updated_at"] = datetime.now(UTC).isoformat()
            current_screening["revision"] = int(current_screening.get("revision") or 0) + 1
            current_screening["evidence_key"] = (
                f"paper:{paper.id}:screening:{current_screening['revision']}"
            )
            paper.screening_feedback = current_screening
        else:
            if (
                screening_feedback is not None
                or update_agent_score_confirmation
                or update_agent_quality_confirmation
                or update_agent_interest_confirmation
            ):
                current_screening = dict(paper.screening_feedback or {})
                legacy_confirmed = bool(current_screening.get("agent_score_confirmed", False))
                current_screening.setdefault("agent_quality_confirmed", legacy_confirmed)
                current_screening.setdefault("agent_interest_confirmed", legacy_confirmed)
                if screening_feedback is not None:
                    current_screening["comment"] = screening_feedback.strip() or None
                if update_agent_score_confirmation:
                    current_screening["agent_score_confirmed"] = bool(agent_score_confirmed)
                if update_agent_quality_confirmation:
                    current_screening["agent_quality_confirmed"] = bool(agent_quality_confirmed)
                if update_agent_interest_confirmation:
                    current_screening["agent_interest_confirmed"] = bool(agent_interest_confirmed)
                current_screening["agent_score_confirmed"] = bool(
                    current_screening.get("agent_quality_confirmed", False)
                    and current_screening.get("agent_interest_confirmed", False)
                )
                current_screening["updated_at"] = datetime.now(UTC).isoformat()
                current_screening["revision"] = int(current_screening.get("revision") or 0) + 1
                current_screening["evidence_key"] = (
                    f"paper:{paper.id}:screening:{current_screening['revision']}"
                )
                paper.screening_feedback = current_screening
        if paper.status == "completed" and (
            interest_level is not None or quality_level is not None
        ):
            resolved_interest = interest_level or paper_interest_level(paper) or "neutral"
            resolved_quality = quality_level or paper_quality_level(paper) or "normal"
            paper.is_interested = resolved_interest in {"high", "interested"}
            paper.is_quality = resolved_quality in {"good", "excellent"}
            current_provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY)
            provenance = dict(current_provenance) if isinstance(current_provenance, dict) else {}
            revision = int(provenance.get("revision") or 0) + 1
            confirmed_at = datetime.now(UTC)
            provenance.update(
                {
                    "schema_version": 2,
                    "revision": revision,
                    "evidence_key": f"paper:{paper.id}:feedback:{revision}",
                    "confirmed_at": confirmed_at.isoformat(),
                    "interest_level": resolved_interest,
                    "quality_level": resolved_quality,
                    "active": True,
                }
            )
            provenance.pop("payload_hash", None)
            provenance.pop("payload_hash_version", None)
            paper.metadata_json = {
                **(paper.metadata_json or {}),
                FEEDBACK_PROVENANCE_KEY: provenance,
            }
    log_operation(
        session,
        actor="user",
        action="paper.metadata_updated",
        entity_type="paper",
        entity_id=paper.id,
        detail={
            "fields": sorted(changes),
            "manually_changed_bibliographic_fields": sorted(manually_changed),
            "tags_updated": tag_ids is not None,
            "evaluation_updated": (
                interest_level is not None
                or quality_level is not None
                or update_agent_score_confirmation
                or update_agent_quality_confirmation
                or update_agent_interest_confirmation
            ),
        },
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/metadata/generate")
def generate_paper_metadata(
    paper_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    if not settings.enable_codex_runs:
        raise HTTPException(status_code=503, detail="Agent 运行已在服务器设置中停用")
    paper = _paper_or_404(session, paper_id)
    task, created = queue_paper_metadata_analysis(session, paper)
    if created:
        background_tasks.add_task(submit_task, task.id)
    translation_task = queue_paper_abstract_translation(session, paper)
    if translation_task:
        background_tasks.add_task(submit_task, translation_task.id)
    return {
        "id": task.id,
        "status": task.status,
        "created": created,
        "translation_task_id": translation_task.id if translation_task else None,
    }


@router.post("/{paper_id}/ignore")
def ignore_paper(
    paper_id: str,
    payload: IgnorePaper,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    if paper.status in {"completed", "feedback_pending"}:
        raise HTTPException(status_code=409, detail="Confirmed reading feedback cannot be ignored")
    existing_ignore = (paper.metadata_json or {}).get(IGNORE_PROVENANCE_KEY)
    if (
        paper.status == "ignored"
        and paper.ignored_reason == payload.reason
        and isinstance(existing_ignore, dict)
        and existing_ignore.get("brief_item_id") == payload.source_brief_item_id
    ):
        return _serialize_paper(session, paper, detail=True)
    if payload.source_brief_item_id:
        try:
            validate_brief_item_source(session, paper.id, payload.source_brief_item_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    ignored_at = datetime.now(UTC).isoformat()
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        IGNORE_PROVENANCE_KEY: {
            "schema_version": 1,
            "evidence_key": f"paper:{paper.id}:ignore:{ignored_at}",
            "brief_item_id": payload.source_brief_item_id,
            "ignored_at": ignored_at,
            "active": True,
        },
    }
    paper.status = "ignored"
    paper.ignored_reason = payload.reason
    log_operation(
        session,
        actor="user",
        action="paper.ignored",
        entity_type="paper",
        entity_id=paper.id,
        detail={
            "reason": payload.reason,
            "source_brief_item_id": payload.source_brief_item_id,
        },
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/start-reading")
def start_reading(
    paper_id: str,
    session: Annotated[Session, Depends(get_session)],
    payload: StartReading | None = None,
) -> dict:
    paper = _paper_or_404(session, paper_id)
    previous_status = paper.status
    source_brief_item_id = payload.source_brief_item_id if payload else None
    if source_brief_item_id is None and previous_status == "ignored":
        ignore_provenance = (paper.metadata_json or {}).get(IGNORE_PROVENANCE_KEY)
        candidate = (
            ignore_provenance.get("brief_item_id") if isinstance(ignore_provenance, dict) else None
        )
        if isinstance(candidate, str):
            try:
                validate_brief_item_source(session, paper.id, candidate)
            except ValueError:
                pass
            else:
                source_brief_item_id = candidate
    if source_brief_item_id:
        try:
            validate_brief_item_source(session, paper.id, source_brief_item_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        metadata = dict(paper.metadata_json or {})
        if previous_status in {"candidate", "ignored"} or not isinstance(
            metadata.get(READING_PROVENANCE_KEY), dict
        ):
            metadata[READING_PROVENANCE_KEY] = {
                "schema_version": 1,
                "brief_item_id": source_brief_item_id,
                "started_at": datetime.now(UTC).isoformat(),
            }
            paper.metadata_json = metadata
    if paper.status not in {"completed", "feedback_pending"}:
        paper.status = "reading"
    log_operation(
        session,
        actor="user",
        action="paper.reading_started",
        entity_type="paper",
        entity_id=paper.id,
        detail={"source_brief_item_id": source_brief_item_id},
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
    removed_links = session.query(PaperNode).filter(PaperNode.paper_id == paper.id).delete()
    removed_progress = (
        session.query(NodeProgress).filter(NodeProgress.paper_id == paper.id).delete()
    )
    metadata = dict(paper.metadata_json or {})
    feedback_provenance = metadata.get(FEEDBACK_PROVENANCE_KEY)
    cancelled_task_ids: list[str] = []
    if isinstance(feedback_provenance, dict):
        evidence_key = feedback_provenance.get("evidence_key")
        graph_task_id = feedback_provenance.get("graph_task_id")
        for task in session.scalars(
            select(TaskRun).where(
                TaskRun.kind == "graph_maintenance",
                TaskRun.status.in_(ACTIVE_TASK_STATUSES),
            )
        ):
            context = (task.payload_json or {}).get("context") or {}
            matches_recorded_task = isinstance(graph_task_id, str) and task.id == graph_task_id
            matches_evidence = (
                context.get("paper_id") == paper.id
                and isinstance(evidence_key, str)
                and context.get("feedback_evidence_key") == evidence_key
            )
            if not (matches_recorded_task or matches_evidence):
                continue
            task.status = "cancelled"
            task.error = None
            task.finished_at = utcnow()
            task.result_json = {
                **(task.result_json or {}),
                "stream_status": "cancelled",
            }
            cancelled_task_ids.append(task.id)
        metadata[FEEDBACK_PROVENANCE_KEY] = {
            **feedback_provenance,
            "active": False,
            "retracted_at": datetime.now(UTC).isoformat(),
        }
        paper.metadata_json = metadata
    log_operation(
        session,
        actor="user",
        action="paper.reading_cancelled",
        entity_type="paper",
        entity_id=paper.id,
        detail={
            "previous_status": previous_status,
            "removed_node_links": removed_links,
            "removed_node_progress": removed_progress,
            "cancelled_graph_task_ids": cancelled_task_ids,
        },
    )
    session.commit()
    for task_id in cancelled_task_ids:
        cancel_codex_run(task_id)
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
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _paper_or_404(session, paper_id)
    source_brief_item_id = payload.source_brief_item_id
    existing_provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY)
    if (
        source_brief_item_id is None
        and paper.status == "completed"
        and isinstance(existing_provenance, dict)
    ):
        candidate = existing_provenance.get("brief_item_id")
        source_brief_item_id = candidate if isinstance(candidate, str) else None
    reading_provenance = (paper.metadata_json or {}).get(READING_PROVENANCE_KEY)
    if source_brief_item_id is None and isinstance(reading_provenance, dict):
        candidate = reading_provenance.get("brief_item_id")
        source_brief_item_id = candidate if isinstance(candidate, str) else None
    if source_brief_item_id is None:
        ignore_provenance = (paper.metadata_json or {}).get(IGNORE_PROVENANCE_KEY)
        candidate = (
            ignore_provenance.get("brief_item_id") if isinstance(ignore_provenance, dict) else None
        )
        source_brief_item_id = candidate if isinstance(candidate, str) else None
    if source_brief_item_id:
        try:
            validate_brief_item_source(session, paper.id, source_brief_item_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        node_selections = _prospective_node_selections(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    attempted_hash = _confirmed_feedback_hash(
        session, paper, payload, source_brief_item_id, node_selections
    )
    if paper.status == "completed":
        hash_matches = bool(
            isinstance(existing_provenance, dict)
            and existing_provenance.get("payload_hash") == attempted_hash
        )
        if (
            not hash_matches
            and isinstance(existing_provenance, dict)
            and existing_provenance.get("payload_hash_version") == 3
            and payload.resolved_quality_level != "poor"
        ):
            hash_matches = existing_provenance.get("payload_hash") == _confirmed_feedback_hash_v3(
                session,
                paper,
                payload,
                source_brief_item_id,
                node_selections,
            )
        if (
            not hash_matches
            and isinstance(existing_provenance, dict)
            and existing_provenance.get("payload_hash_version") == 2
            and payload.resolved_interest_level != "ignored"
            and payload.resolved_quality_level != "poor"
            and not payload.node_links
            and not payload.new_nodes
        ):
            hash_matches = existing_provenance.get("payload_hash") == _confirmed_feedback_hash_v2(
                session, paper, payload, source_brief_item_id
            )
        if (
            isinstance(existing_provenance, dict)
            and existing_provenance.get("active", True)
            and hash_matches
        ):
            return _serialize_paper(session, paper, detail=True)
        raise HTTPException(status_code=409, detail="Paper feedback is already confirmed")
    if paper.status != "feedback_pending":
        raise HTTPException(
            status_code=409,
            detail="Paper must be summarized before feedback confirmation",
        )
    claim = session.execute(
        update(Paper)
        .where(Paper.id == paper.id, Paper.status == "feedback_pending")
        .values(status="feedback_confirming")
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.rollback()
        paper = _paper_or_404(session, paper_id)
        concurrent_provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY)
        if (
            paper.status == "completed"
            and isinstance(concurrent_provenance, dict)
            and concurrent_provenance.get("active", True)
            and concurrent_provenance.get("payload_hash") == attempted_hash
        ):
            return _serialize_paper(session, paper, detail=True)
        raise HTTPException(status_code=409, detail="Paper feedback confirmation is already active")
    paper.status = "completed"
    interest_level = payload.resolved_interest_level
    quality_level = payload.resolved_quality_level
    paper.is_interested = interest_level in {"high", "interested"}
    paper.is_quality = quality_level in {"good", "excellent"}
    paper.reading_completed_at = datetime.now(UTC)
    paper.pending_feedback = {}
    if payload.completion_summary_structured is not None:
        save_completion_summary(paper, payload.completion_summary_structured.model_dump())
    if payload.personal_comment is not None:
        paper.metadata_json = {
            **paper.metadata_json,
            "personal_comment": payload.personal_comment.strip() or None,
        }
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
    tag_names = list(session.scalars(select(Tag.name).where(Tag.id.in_(selected_tag_ids))).all())
    try:
        selected_node_ids = _apply_node_selections(session, paper, node_selections)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    structured = completion_summary_structured(paper, include_user_attitude=True)
    _replace_selected_node_progress(session, paper, selected_node_ids, structured)
    previous_revision = (
        int(existing_provenance.get("revision") or 0)
        if isinstance(existing_provenance, dict)
        else 0
    )
    confirmed_at = datetime.now(UTC)
    revision = previous_revision + 1
    evidence_key = f"paper:{paper.id}:feedback:{revision}"
    payload_hash = feedback_payload_hash(
        interest_level=interest_level,
        quality_level=quality_level,
        create_node=payload.create_node,
        tag_names=tag_names,
        node_selections=node_selections,
        user_attitude=(structured or {}).get("user_attitude"),
        personal_comment=(paper.metadata_json or {}).get("personal_comment"),
        source_brief_item_id=source_brief_item_id,
    )
    paper.metadata_json = {
        **paper.metadata_json,
        "create_node_requested": bool(payload.new_nodes) or payload.create_node,
        FEEDBACK_PROVENANCE_KEY: {
            "schema_version": 2,
            "payload_hash_version": 4,
            "revision": revision,
            "evidence_key": evidence_key,
            "confirmed_at": confirmed_at.isoformat(),
            "brief_item_id": source_brief_item_id,
            "interest_level": interest_level,
            "quality_level": quality_level,
            "selected_node_ids": selected_node_ids,
            "payload_hash": payload_hash,
            "active": True,
        },
    }
    ignore_provenance = paper.metadata_json.get(IGNORE_PROVENANCE_KEY)
    if isinstance(ignore_provenance, dict):
        paper.metadata_json = {
            **paper.metadata_json,
            IGNORE_PROVENANCE_KEY: {
                **ignore_provenance,
                "active": False,
                "replaced_at": confirmed_at.isoformat(),
            },
        }
    log_operation(
        session,
        actor="user",
        action="paper.feedback_confirmed",
        entity_type="paper",
        entity_id=paper.id,
        detail={
            "evidence_key": evidence_key,
            "feedback_revision": revision,
            "source_brief_item_id": source_brief_item_id,
            "interest_level": interest_level,
            "interested": paper.is_interested,
            "quality_level": quality_level,
            "create_node": payload.create_node,
            "tag_count": len(selected_tag_ids),
            "node_count": len(selected_node_ids),
        },
    )
    session.commit()
    return _serialize_paper(session, paper, detail=True)


@router.post("/{paper_id}/download-pdf")
def fetch_pdf(paper_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    paper = _paper_or_404(session, paper_id)
    if source_url := arxiv_pdf_url(paper):
        return {
            "paper_id": paper.id,
            "pdf_path": None,
            "url": source_url,
            "source": "arxiv",
        }
    try:
        path = download_paper_pdf(session, paper)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PDF download failed: {exc}") from exc
    return {"paper_id": paper.id, "pdf_path": str(path), "url": f"/api/papers/{paper.id}/pdf"}


@router.get("/{paper_id}/pdf")
def get_pdf(
    paper_id: str, session: Annotated[Session, Depends(get_session)]
) -> Response:
    paper = _paper_or_404(session, paper_id)
    if source_url := arxiv_pdf_url(paper):
        return RedirectResponse(source_url, status_code=302)
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
            queued = queue_document_conversion(paper_id)
            if not queued:
                refreshed = document_status(session, paper)
                if refreshed.get("status") == "failed":
                    return refreshed
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
    if queued:
        return {"paper_id": paper_id, "status": "processing", "queued": True}
    paper = _paper_or_404(session, paper_id)
    return {**document_status(session, paper), "queued": False}


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
    background_tasks.add_task(submit_task, task_id)
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
        content = ""
        terminal = False
        try:
            # Keep even the initial protocol event inside the guarded region. A
            # browser can disconnect immediately after receiving metadata, and
            # GeneratorExit is not caught by ``except Exception``.
            yield encode_stream_event("meta", cached=False, task_id=task.id)
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "running"
                    running.started_at = utcnow()
                    runner_session.commit()
            for delta in stream_translation_prompt(
                build_prompt(task),
                timeout_seconds=settings.translation_timeout_seconds,
            ):
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
            terminal = True
            yield encode_stream_event("done", content=content, cached=False)
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as runner_session:
                running = runner_session.get(TaskRun, task.id)
                if running:
                    running.status = "failed"
                    running.error = str(exc)
                    running.finished_at = utcnow()
                    runner_session.commit()
            terminal = True
            yield encode_stream_event("error", message=str(exc))
        finally:
            if not terminal:
                interruption = "浏览器连接已中断；翻译任务已停止。"
                with SessionLocal() as runner_session:
                    running = runner_session.get(TaskRun, task.id)
                    if running and running.status in {"queued", "running"}:
                        running.status = "failed"
                        running.error = interruption
                        running.result_json = {
                            **(running.result_json or {}),
                            "response": content,
                            "stream_status": "interrupted",
                        }
                        running.finished_at = utcnow()
                        runner_session.commit()

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
