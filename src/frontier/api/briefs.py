from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Brief, BriefItem, Paper, TaskRun
from ..schemas import BriefCreate, BriefItemCreate, BriefUpdate
from ..services.abstract_translation import queue_brief_abstract_translations
from ..services.common import (
    BRIEF_LIMIT_DEFAULT,
    BRIEF_LIMIT_MAX,
    get_setting,
    log_operation,
    model_dict,
)
from ..services.manual_recommendations import (
    is_daily_recommendation_eligible,
    previously_recommended_paper_ids,
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


def _serialize_brief(session: Session, brief: Brief, *, detail: bool = True) -> dict:
    from .papers import _serialize_paper

    items = list(
        session.scalars(
            select(BriefItem).where(BriefItem.brief_id == brief.id).order_by(BriefItem.rank)
        )
    )
    payload = model_dict(brief)
    payload["item_count"] = len(items)
    if detail:
        papers = {
            p.id: p
            for p in session.scalars(select(Paper).where(Paper.id.in_([i.paper_id for i in items])))
        }
        payload["items"] = [
            {
                "id": i.id,
                "rank": i.rank,
                "tier": i.tier,
                "paper": _serialize_paper(session, papers[i.paper_id]),
            }
            for i in items
            if i.paper_id in papers
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
            current_digest = existing.interest_context_digest
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
            for field in ("title", "introduction"):
                if field not in payload.model_fields_set:
                    continue
                value = getattr(payload, field)
                if field == "title" and not value:
                    continue
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if payload.task_id and existing.task_id != payload.task_id:
                existing.task_id = payload.task_id
                changed = True
            if (
                payload.interest_context_digest
                and existing.interest_context_digest != payload.interest_context_digest
            ):
                existing.interest_context_digest = payload.interest_context_digest
                changed = True
            if changed:
                session.commit()
        return _serialize_brief(session, existing)
    item = Brief(
        brief_date=payload.brief_date,
        title=payload.title or f"{payload.brief_date.isoformat()} 研究简报",
        introduction=payload.introduction,
        task_id=payload.task_id,
        interest_context_digest=payload.interest_context_digest,
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

    brief.status = "draft"
    brief.title = f"{brief.brief_date.isoformat()} 研究简报"
    brief.published_at = None
    brief.introduction = None
    brief.interest_context_digest = None
    session.flush()
    claim = get_or_create_daily_task(session, brief.brief_date)
    assert claim.task is not None
    brief.task_id = claim.task.id
    session.commit()
    background_tasks.add_task(submit_task, claim.task.id)
    return {"id": claim.task.id, "status": claim.task.status, "brief_id": brief.id}


@router.patch("/{brief_id}")
def update_brief(
    brief_id: str,
    payload: BriefUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    brief = _brief_or_404(session, brief_id)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and (not changes["title"] or not changes["title"].strip()):
        raise HTTPException(status_code=422, detail="Brief title must not be empty")
    for field, value in changes.items():
        setattr(brief, field, value)
    if changes:
        log_operation(
            session,
            actor="user",
            action="brief.updated",
            entity_type="brief",
            entity_id=brief.id,
            detail={"fields": sorted(changes)},
        )
        session.commit()
    return _serialize_brief(session, brief)


@router.post("/{brief_id}/recover-failed")
@serialized_task_claim
def recover_failed_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    """Rebuild a failed daily draft, or recover an accidental publication, with an audit."""
    from ..config import settings

    if not settings.enable_codex_runs:
        raise HTTPException(status_code=503, detail="Agent 运行已在服务器设置中停用")
    brief = _brief_or_404(session, brief_id)
    previous_task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    if (
        brief.status not in {"draft", "published"}
        or not previous_task
        or previous_task.kind != "daily_brief"
        or previous_task.status != "failed"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only drafts or publications from failed daily tasks can recover",
        )
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
    ).all()
    if brief.status == "published" and any(
        item.tier != "manual"
        and (
            paper.status != "candidate"
            or paper.post_read_summary
            or paper.user_score is not None
            or paper.user_note
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
        reason="重建失败任务的日报草稿"
        if brief.status == "draft"
        else "恢复意外发布且任务失败的日报",
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
    if payload.tier != "manual" and (
        not is_daily_recommendation_eligible(paper)
        or paper.id
        in previously_recommended_paper_ids(session, [paper.id], exclude_brief_id=brief.id)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Only unread candidate papers without screening feedback or prior recommendation "
                "can be added to a daily brief "
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
@serialized_task_claim
def publish_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    brief = _brief_or_404(session, brief_id)
    if brief.status == "published":
        return _serialize_brief(session, brief)
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
        .order_by(BriefItem.rank)
    ).all()
    prior_ids = previously_recommended_paper_ids(
        session, [paper.id for _item, paper in rows], exclude_brief_id=brief.id
    )
    excluded_rows = [
        row
        for row in rows
        if row[0].tier != "manual"
        and (not is_daily_recommendation_eligible(row[1]) or row[1].id in prior_ids)
    ]
    rows = [
        row
        for row in rows
        if row[0].tier == "manual"
        or (is_daily_recommendation_eligible(row[1]) and row[1].id not in prior_ids)
    ]
    task = session.get(TaskRun, brief.task_id) if brief.task_id else None
    pool = (task.payload_json or {}).get("candidate_pool") if task else None
    empty_daily = bool(
        task
        and (task.payload_json or {}).get("discovery_mode") == "brief_date_cursor"
        and pool is not None
        and pool.get("paper_ids") == []
        and pool.get("discovery_complete")
        and pool.get("notes", "").strip()
    )
    if not rows and not empty_daily:
        raise HTTPException(status_code=409, detail="Cannot publish a brief without unread papers")
    recommendation_count = sum(item.tier != "manual" for item, _paper in rows)
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
    brief.status = "published"
    brief.published_at = datetime.now(UTC)
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
            "text_cache_count": 0,
        },
    )
    session.commit()
    result = _serialize_brief(session, brief)
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
