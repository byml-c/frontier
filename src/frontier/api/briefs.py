from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import (
    Brief,
    BriefItem,
    Paper,
    PaperNode,
    PaperTag,
    PodcastEpisode,
    ResearchNode,
    Tag,
    TaskRun,
)
from ..schemas import (
    BriefCreate,
    BriefItemCreate,
    PodcastEpisodeCommit,
    PodcastEpisodeStatusUpdate,
)
from ..services.abstract_translation import abstract_translation, queue_brief_abstract_translations
from ..services.common import (
    BRIEF_LIMIT_DEFAULT,
    BRIEF_LIMIT_MAX,
    FEATURED_LIMIT_DEFAULT,
    RESEARCH_GROUP_SUMMARY_KEY,
    get_setting,
    log_operation,
    model_dict,
)
from ..services.completion_summary import (
    brief_completion_summary,
    completion_summary_structured,
)
from ..services.document_reader import queue_document_conversions
from ..services.interest_evidence import (
    build_interest_context,
    interest_context_snapshot,
    paper_interest_level,
    paper_quality_level,
)
from ..services.manual_recommendations import (
    current_analysis_state,
    is_daily_recommendation_eligible,
)
from ..services.podcast_content import (
    PodcastContentError,
    PodcastContentNotFoundError,
    PodcastContentRevisionError,
    PodcastContentSourceError,
    PodcastContentValidationError,
    commit_podcast_episode,
    episode_payload,
    update_podcast_episode_status,
)
from ..services.podcast_generation import (
    PodcastGenerationError,
    get_or_create_podcast_task,
)
from ..services.task_control import (
    ACTIVE_TASK_STATUSES,
    get_or_create_daily_task,
    serialized_task_claim,
)
from ..services.task_dispatcher import submit_task
from ..services.translation_provider import (
    TranslationProviderError,
    load_translation_provider_config,
)

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


def _brief_or_404(session: Session, brief_id: str) -> Brief:
    item = session.get(Brief, brief_id)
    if not item:
        raise HTTPException(status_code=404, detail="Brief not found")
    return item


def _podcast_content_error(exc: PodcastContentError) -> HTTPException:
    if isinstance(exc, PodcastContentNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PodcastContentRevisionError):
        return HTTPException(
            status_code=409,
            detail={
                "message": "播客已更新，请读取最新版本后重试",
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        )
    if isinstance(exc, PodcastContentSourceError):
        return HTTPException(
            status_code=409,
            detail={
                "message": "简报或文字稿已变化，请重新生成播客",
                "expected_source_digest": exc.expected,
                "actual_source_digest": exc.actual,
            },
        )
    if isinstance(exc, PodcastContentValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _paper_snapshot(session: Session, paper: Paper) -> dict:
    nodes = session.execute(
        select(ResearchNode.id, ResearchNode.name, PaperNode.reason)
        .join(PaperNode, PaperNode.node_id == ResearchNode.id)
        .where(PaperNode.paper_id == paper.id)
    ).all()
    tags = session.execute(
        select(Tag.id, Tag.name, Tag.color)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper.id)
        .order_by(Tag.name)
    ).all()
    metadata = paper.metadata_json or {}
    manual_analysis = current_analysis_state(paper)
    translation = abstract_translation(session, paper)
    return {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors or [],
        "arxiv_id": paper.arxiv_id,
        "first_author": paper.first_author,
        "corresponding_author": paper.corresponding_author,
        "primary_affiliation": paper.primary_affiliation,
        "research_group_summary": metadata.get(RESEARCH_GROUP_SUMMARY_KEY),
        "source": paper.source,
        "source_url": paper.source_url,
        "pdf_url": paper.pdf_url,
        "published_at": paper.published_at.isoformat() if paper.published_at else None,
        "summary": paper.summary,
        "agent_comment": paper.agent_comment,
        "problem": paper.problem,
        "innovation": paper.innovation,
        "experiment": paper.experiment,
        "limitations": paper.limitations,
        "open_questions": metadata.get("open_questions"),
        "research_score": paper.research_score,
        "relevance_score": paper.relevance_score,
        "topic_relevance_score": paper.topic_relevance_score,
        "author_influence_score": paper.author_influence_score,
        "recommendation_reason": paper.recommendation_reason,
        "nodes": [dict(row._mapping) for row in nodes],
        "tags": [dict(row._mapping) for row in tags],
        "analysis_status": manual_analysis.get("status"),
        "analysis_error": manual_analysis.get("error"),
        "suggested_tags": metadata.get("suggested_tags", []),
        "screening_quality_level": (paper.screening_feedback or {}).get("quality_level"),
        "screening_interest_level": (paper.screening_feedback or {}).get("interest_level"),
        "screening_feedback": (paper.screening_feedback or {}).get("comment"),
        "agent_score_confirmed": (paper.screening_feedback or {}).get("agent_score_confirmed", False),
        "agent_quality_confirmed": (paper.screening_feedback or {}).get(
            "agent_quality_confirmed",
            (paper.screening_feedback or {}).get("agent_score_confirmed", False),
        ),
        "agent_interest_confirmed": (paper.screening_feedback or {}).get(
            "agent_interest_confirmed",
            (paper.screening_feedback or {}).get("agent_score_confirmed", False),
        ),
        "abstract_translation": translation.content if translation else None,
        "abstract_translation_status": (
            "ready"
            if translation
            else (
                (metadata.get("abstract_translation") or {}).get("status", "missing")
                if isinstance(metadata.get("abstract_translation"), dict)
                else "missing"
            )
        ),
        "abstract_translation_error": (
            (metadata.get("abstract_translation") or {}).get("error")
            if isinstance(metadata.get("abstract_translation"), dict)
            else None
        ),
    }


def _serialize_brief(session: Session, brief: Brief, *, detail: bool = True) -> dict:
    # Brief membership is a historical recommendation decision. Eligibility is checked
    # when an item is added and again at publish time, never recomputed while reading.
    payload = model_dict(brief)
    if not detail:
        statement = select(BriefItem.id).where(BriefItem.brief_id == brief.id)
        payload["item_count"] = len(session.scalars(statement).all())
        return payload
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief.id)
        .order_by(BriefItem.rank)
    ).all()
    translations = {paper.id: abstract_translation(session, paper) for _, paper in rows}
    translation_errors = (brief.snapshot_json or {}).get("abstract_translation_errors", {})
    payload["items"] = [
        {
            **model_dict(item),
            "paper": (
                {
                    **_paper_snapshot(session, paper),
                    **((item.snapshot_json or {}) if item.tier != "manual" else {}),
                    # Keep editable analysis fields current so a user or Agent can
                    # correct a brief comment after publication without rewriting
                    # the historical membership decision.
                    "summary": paper.summary,
                    "agent_comment": paper.agent_comment,
                    "problem": paper.problem,
                    "innovation": paper.innovation,
                    "experiment": paper.experiment,
                    "limitations": paper.limitations,
                    "open_questions": (paper.metadata_json or {}).get("open_questions"),
                    "research_score": paper.research_score,
                    "relevance_score": paper.relevance_score,
                    "topic_relevance_score": paper.topic_relevance_score,
                    "author_influence_score": paper.author_influence_score,
                    "recommendation_reason": paper.recommendation_reason,
                    "abstract": paper.abstract,
                    "authors": paper.authors or [],
                    "first_author": paper.first_author,
                    "corresponding_author": paper.corresponding_author,
                    "primary_affiliation": paper.primary_affiliation,
                    "research_group_summary": (paper.metadata_json or {}).get(
                        RESEARCH_GROUP_SUMMARY_KEY
                    ),
                    "screening_quality_level": (paper.screening_feedback or {}).get(
                        "quality_level"
                    ),
                    "screening_interest_level": (paper.screening_feedback or {}).get(
                        "interest_level"
                    ),
                    "screening_feedback": (paper.screening_feedback or {}).get("comment"),
                    "agent_score_confirmed": (paper.screening_feedback or {}).get(
                        "agent_score_confirmed", False
                    ),
                    "agent_quality_confirmed": (paper.screening_feedback or {}).get(
                        "agent_quality_confirmed",
                        (paper.screening_feedback or {}).get("agent_score_confirmed", False),
                    ),
                    "agent_interest_confirmed": (paper.screening_feedback or {}).get(
                        "agent_interest_confirmed",
                        (paper.screening_feedback or {}).get("agent_score_confirmed", False),
                    ),
                    # 阅读结论属于论文的后续状态，不应被冻结在发布时的简报快照中。
                    "completion_summary": brief_completion_summary(paper),
                    "completion_summary_structured": completion_summary_structured(
                        paper, include_user_attitude=False
                    ),
                    "abstract_translation_status": (
                        "ready"
                        if translations[paper.id]
                        else (
                            "failed"
                            if paper.id in translation_errors
                            else (brief.snapshot_json or {}).get(
                                "abstract_translation_status",
                                (
                                    ((paper.metadata_json or {}).get("abstract_translation") or {})
                                    .get("status", "missing")
                                    if isinstance(
                                        (paper.metadata_json or {}).get("abstract_translation"),
                                        dict,
                                    )
                                    else "missing"
                                ),
                            )
                        )
                    ),
                    "abstract_translation": (
                        translations[paper.id].content if translations[paper.id] else None
                    ),
                    "abstract_translation_error": translation_errors.get(paper.id)
                    or (
                        ((paper.metadata_json or {}).get("abstract_translation") or {}).get("error")
                        if isinstance(
                            (paper.metadata_json or {}).get("abstract_translation"), dict
                        )
                        else None
                    ),
                }
            ),
            "current_status": paper.status,
            "interest_level": paper_interest_level(paper),
            "quality_level": paper_quality_level(paper),
            "is_interested": paper.is_interested,
            "is_quality": paper.is_quality,
        }
        for item, paper in rows
    ]
    return payload


@router.get("")
def list_briefs(
    session: Annotated[Session, Depends(get_session)],
    status: str | None = "published",
    limit: int = Query(default=30, ge=1, le=365),
) -> list[dict]:
    statement = select(Brief).order_by(Brief.brief_date.desc()).limit(limit)
    if status:
        statement = statement.where(Brief.status == status)
    return [_serialize_brief(session, item, detail=False) for item in session.scalars(statement)]


@router.get("/latest")
def latest_brief(session: Annotated[Session, Depends(get_session)]) -> dict:
    item = session.scalar(
        select(Brief)
        .outerjoin(BriefItem, BriefItem.brief_id == Brief.id)
        .where(or_(Brief.status == "published", BriefItem.tier == "manual"))
        .order_by(Brief.brief_date.desc())
        .distinct()
        .limit(1)
    )
    if not item:
        raise HTTPException(status_code=404, detail="No published brief")
    return _serialize_brief(session, item)


@router.post("")
def create_brief(payload: BriefCreate, session: Annotated[Session, Depends(get_session)]) -> dict:
    existing = session.scalar(select(Brief).where(Brief.brief_date == payload.brief_date))
    if existing:
        if existing.status != "published":
            current_digest = (existing.snapshot_json or {}).get("interest_context_digest_used")
            if (
                payload.interest_context_digest
                and payload.interest_context_digest != current_digest
                and session.scalar(
                    select(BriefItem.id).where(
                        BriefItem.brief_id == existing.id,
                        BriefItem.tier != "manual",
                    )
                )
                is not None
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Draft already contains ranked daily items from another interest "
                        "context; rebuild the draft before changing its digest"
                    ),
                )
            changed = False
            if payload.task_id and existing.task_id != payload.task_id:
                existing.task_id = payload.task_id
                changed = True
            if payload.interest_context_digest:
                snapshot = dict(existing.snapshot_json or {})
                if snapshot.get("interest_context_digest_used") != payload.interest_context_digest:
                    snapshot["interest_context_digest_used"] = payload.interest_context_digest
                    existing.snapshot_json = snapshot
                    changed = True
            if changed:
                session.commit()
        return _serialize_brief(session, existing)
    item = Brief(
        brief_date=payload.brief_date,
        title=payload.title or f"{payload.brief_date.isoformat()} 研究简报",
        introduction=payload.introduction,
        task_id=payload.task_id,
        snapshot_json={
            "interest_context_digest_used": payload.interest_context_digest,
        }
        if payload.interest_context_digest
        else {},
    )
    session.add(item)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="brief.created",
        entity_type="brief",
        entity_id=item.id,
        task_id=payload.task_id,
    )
    session.commit()
    return _serialize_brief(session, item)


@router.get("/{brief_id}")
def get_brief(brief_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    return _serialize_brief(session, _brief_or_404(session, brief_id))


def _queue_daily_brief_regeneration(
    *,
    brief: Brief,
    previous_task: TaskRun,
    background_tasks: BackgroundTasks,
    session: Session,
    action: str,
    reason: str,
) -> dict:
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief.id)
    ).all()
    if session.scalar(
        select(TaskRun.id).where(
            TaskRun.kind == "daily_brief",
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
            TaskRun.payload_json["date"].as_string() == brief.brief_date.isoformat(),
        )
    ):
        raise HTTPException(status_code=409, detail="A daily task is already active for this date")

    log_operation(
        session,
        actor="user",
        action=action,
        entity_type="brief",
        entity_id=brief.id,
        task_id=previous_task.id,
        detail={"reason": reason, "previous_publication": _serialize_brief(session, brief)},
    )
    for item, _paper in rows:
        if item.tier != "manual":
            session.delete(item)

    episode = session.scalar(select(PodcastEpisode).where(PodcastEpisode.brief_id == brief.id))
    if episode is not None:
        episode.status = "voided"
        episode.revision += 1
        episode.audio_path = None
        episode.audio_content_hash = None
        episode.duration_ms = None
        episode.size_bytes = None
        episode.generation_task_id = None
        episode.published_at = None
        episode.error = reason
        log_operation(
            session,
            actor="user",
            action="podcast_episode.voided",
            entity_type="podcast_episode",
            entity_id=episode.id,
            task_id=previous_task.id,
            detail={"brief_id": brief.id, "reason": reason},
        )

    brief.status = "draft"
    brief.published_at = None
    brief.introduction = None
    brief.snapshot_json = {}
    session.flush()
    claim = get_or_create_daily_task(session, brief.brief_date)
    assert claim.task is not None
    brief.task_id = claim.task.id
    session.commit()
    background_tasks.add_task(submit_task, claim.task.id)
    return {"id": claim.task.id, "status": claim.task.status, "brief_id": brief.id}


@router.post("/{brief_id}/recover-failed")
@serialized_task_claim
def recover_failed_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Recover an interrupted, accidentally published brief without losing its audit history."""
    from ..config import settings

    if not settings.enable_codex_runs:
        raise HTTPException(status_code=503, detail="Agent 运行已在服务器设置中停用")
    brief = _brief_or_404(session, brief_id)
    previous_task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    if brief.status != "published" or not previous_task or previous_task.status != "failed":
        raise HTTPException(
            status_code=409, detail="Only briefs from failed daily tasks can be recovered"
        )
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
    ).all()
    if any(
        item.tier != "manual"
        and (
            paper.status != "candidate"
            or paper.research_score is not None
            or paper.relevance_score is not None
            or paper.screening_feedback
        )
        for item, paper in rows
    ):
        raise HTTPException(
            status_code=409, detail="Brief already contains analysis or user feedback"
        )
    return _queue_daily_brief_regeneration(
        brief=brief,
        previous_task=previous_task,
        background_tasks=background_tasks,
        session=session,
        action="brief.failed_publication_recovered",
        reason="恢复意外发布且任务失败的日报",
    )


@router.post("/{brief_id}/void-and-regenerate")
@serialized_task_claim
def void_and_regenerate_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Explicitly void a published daily brief and queue a clean replacement run."""
    from ..config import settings

    if not settings.enable_codex_runs:
        raise HTTPException(status_code=503, detail="Agent 运行已在服务器设置中停用")
    brief = _brief_or_404(session, brief_id)
    previous_task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    if (
        brief.status != "published"
        or not previous_task
        or previous_task.kind != "daily_brief"
        or previous_task.status not in {"completed", "failed"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Only completed or failed published daily briefs can be voided and regenerated",
        )
    return _queue_daily_brief_regeneration(
        brief=brief,
        previous_task=previous_task,
        background_tasks=background_tasks,
        session=session,
        action="brief.voided_for_regeneration",
        reason="用户要求作废已发布日报并重新生成",
    )


@router.get("/{brief_id}/podcast")
def get_brief_podcast(
    brief_id: str,
    session: Annotated[Session, Depends(get_session)],
    language: str = Query(default="zh-CN", min_length=2, max_length=35),
) -> dict:
    try:
        return episode_payload(session, brief_id, language=language)
    except PodcastContentError as exc:
        raise _podcast_content_error(exc) from exc


@router.post("/{brief_id}/podcast/generate")
def generate_brief_podcast(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    try:
        claim = get_or_create_podcast_task(session, brief_id)
    except PodcastGenerationError as exc:
        status_code = 404 if str(exc) == "简报不存在" else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    assert claim.task is not None
    if claim.task.status == "queued":
        background_tasks.add_task(submit_task, claim.task.id)
    return {
        "id": claim.task.id,
        "task_id": claim.task.id,
        "status": claim.task.status,
    }


@router.post("/{brief_id}/podcast/commit")
@serialized_task_claim
def save_brief_podcast(
    brief_id: str,
    payload: PodcastEpisodeCommit,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    try:
        item, changed = commit_podcast_episode(session, brief_id, payload)
        if changed:
            log_operation(
                session,
                actor="agent" if payload.task_id else "user",
                action="podcast_episode.committed",
                entity_type="podcast_episode",
                entity_id=item.id,
                task_id=payload.task_id,
                detail={"brief_id": brief_id, "revision": item.revision},
            )
        return episode_payload(session, brief_id, language=payload.language)
    except PodcastContentError as exc:
        raise _podcast_content_error(exc) from exc


@router.patch("/{brief_id}/podcast/status")
@serialized_task_claim
def set_brief_podcast_status(
    brief_id: str,
    payload: PodcastEpisodeStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    language: str = Query(default="zh-CN", min_length=2, max_length=35),
) -> dict:
    try:
        item, changed = update_podcast_episode_status(session, brief_id, payload, language=language)
        if changed:
            log_operation(
                session,
                actor="agent" if payload.task_id else "system",
                action="podcast_episode.status_updated",
                entity_type="podcast_episode",
                entity_id=item.id,
                task_id=payload.task_id,
                detail={
                    "brief_id": brief_id,
                    "revision": item.revision,
                    "status": item.status,
                },
            )
        return episode_payload(session, brief_id, language=language)
    except PodcastContentError as exc:
        raise _podcast_content_error(exc) from exc


@router.post("/{brief_id}/items")
def add_brief_item(
    brief_id: str,
    payload: BriefItemCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    brief = _brief_or_404(session, brief_id)
    if brief.status == "published":
        raise HTTPException(status_code=409, detail="Published briefs are immutable")
    paper = session.get(Paper, payload.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    pool = (task.payload_json or {}).get("candidate_pool") if task else None
    if pool is not None and payload.tier != "manual" and paper.id not in pool["paper_ids"]:
        raise HTTPException(status_code=409, detail="Paper is not in the daily candidate pool")
    if not is_daily_recommendation_eligible(paper):
        raise HTTPException(
            status_code=409,
            detail=(
                "Only unread candidate papers can be added to a daily brief "
                f"(current status: {paper.status})"
            ),
        )
    existing = session.scalar(
        select(BriefItem).where(
            BriefItem.brief_id == brief_id, BriefItem.paper_id == payload.paper_id
        )
    )
    if existing and existing.tier == "manual":
        return model_dict(existing)
    current_count = len(
        session.scalars(
            select(BriefItem.id).where(
                BriefItem.brief_id == brief_id,
                BriefItem.tier != "manual",
            )
        ).all()
    )
    limit = min(
        BRIEF_LIMIT_MAX,
        int(get_setting(session, "brief_limit", BRIEF_LIMIT_DEFAULT)),
    )
    if current_count >= limit:
        raise HTTPException(status_code=409, detail=f"Brief item limit reached ({limit})")
    featured_count = len(
        session.scalars(
            select(BriefItem.id).where(BriefItem.brief_id == brief_id, BriefItem.tier == "featured")
        ).all()
    )
    featured_limit = int(get_setting(session, "featured_limit", FEATURED_LIMIT_DEFAULT))
    if payload.tier == "featured" and featured_count >= featured_limit:
        raise HTTPException(
            status_code=409, detail=f"Featured item limit reached ({featured_limit})"
        )
    if existing:
        existing.rank = payload.rank
        existing.tier = payload.tier
        session.commit()
        return model_dict(existing)
    item = BriefItem(brief_id=brief_id, **payload.model_dump())
    session.add(item)
    session.commit()
    return model_dict(item)


@router.post("/{brief_id}/publish")
def publish_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    brief = _brief_or_404(session, brief_id)
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
        .order_by(BriefItem.rank)
    ).all()
    excluded_rows = [
        row
        for row in rows
        if row[0].tier != "manual" and not is_daily_recommendation_eligible(row[1])
    ]
    rows = [
        row for row in rows if row[0].tier == "manual" or is_daily_recommendation_eligible(row[1])
    ]
    if not rows:
        raise HTTPException(status_code=409, detail="Cannot publish a brief without unread papers")
    recommendation_count = sum(item.tier != "manual" for item, _paper in rows)
    task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    pool = (task.payload_json or {}).get("candidate_pool") if task else None
    if pool is not None and any(
        item.tier != "manual" and paper.id not in pool["paper_ids"] for item, paper in rows
    ):
        raise HTTPException(
            status_code=409, detail="Brief contains papers outside its candidate pool"
        )
    limit = min(
        BRIEF_LIMIT_MAX,
        int(get_setting(session, "brief_limit", BRIEF_LIMIT_DEFAULT)),
    )
    if recommendation_count > limit:
        raise HTTPException(
            status_code=409,
            detail=f"Brief contains more than the top {limit} recommendations",
        )
    for item, _paper in excluded_rows:
        session.delete(item)
    for item, paper in rows:
        item.snapshot_json = _paper_snapshot(session, paper)
    interest_context = build_interest_context(session, recent_limit=20)
    digest_used = (brief.snapshot_json or {}).get("interest_context_digest_used")
    brief.snapshot_json = {
        **(brief.snapshot_json or {}),
        "title": brief.title,
        "introduction": brief.introduction,
        "paper_ids": [paper.id for _item, paper in rows],
        **({"candidate_pool": pool} if pool is not None else {}),
        "interest_context": interest_context_snapshot(interest_context),
        "interest_context_stale_at_publish": (
            digest_used != interest_context["source_digest"] if digest_used else None
        ),
    }
    brief.status = "published"
    brief.published_at = datetime.now(UTC)
    # Preprocess the ranked top ten even when an older Agent run did not mark the
    # tier explicitly; the continuous brief list is rank-driven.
    preprocess_limit = min(
        10,
        int(get_setting(session, "fulltext_limit", 10)),
        int(get_setting(session, "featured_limit", FEATURED_LIMIT_DEFAULT)),
    )
    featured_paper_ids = [paper.id for _item, paper in rows if _item.tier != "manual"][
        :preprocess_limit
    ]
    log_operation(
        session,
        actor="agent",
        action="brief.published",
        entity_type="brief",
        entity_id=brief.id,
        task_id=brief.task_id,
        detail={
            "item_count": len(rows),
            "excluded_reading_count": len(excluded_rows),
            "text_cache_count": len(featured_paper_ids),
        },
    )
    session.commit()
    result = _serialize_brief(session, brief)
    if featured_paper_ids:
        background_tasks.add_task(
            queue_document_conversions, featured_paper_ids, limit=preprocess_limit
        )
    try:
        load_translation_provider_config()
    except TranslationProviderError:
        pass
    else:
        translation_task = queue_brief_abstract_translations(session, brief.id)
        if translation_task:
            background_tasks.add_task(submit_task, translation_task.id)
    return result


@router.post("/{brief_id}/abstract-translations/{paper_id}/retry")
def retry_abstract_translation(
    brief_id: str,
    paper_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _brief_or_404(session, brief_id)
    if not session.scalar(
        select(BriefItem.id).where(BriefItem.brief_id == brief_id, BriefItem.paper_id == paper_id)
    ):
        raise HTTPException(status_code=404, detail="Paper is not part of this brief")
    try:
        load_translation_provider_config()
    except TranslationProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    task = queue_brief_abstract_translations(session, brief_id, paper_id=paper_id, force=True)
    if not task:
        return {"status": "ready"}
    background_tasks.add_task(submit_task, task.id)
    return {"id": task.id, "status": task.status}


@router.post("/{brief_id}/abstract-translations")
def translate_brief_abstracts(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    force: bool = False,
) -> dict:
    """Queue one batch for every untranslated abstract in the brief."""
    _brief_or_404(session, brief_id)
    try:
        load_translation_provider_config()
    except TranslationProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    task = queue_brief_abstract_translations(session, brief_id, force=force)
    if not task:
        return {"status": "ready"}
    background_tasks.add_task(submit_task, task.id)
    return {"id": task.id, "status": task.status}
