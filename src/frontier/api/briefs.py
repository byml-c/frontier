from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Brief, BriefItem, Paper, PaperNode, PaperTag, ResearchNode, Tag
from ..schemas import BriefCreate, BriefItemCreate
from ..services.common import get_setting, log_operation, model_dict
from ..services.manual_recommendations import current_analysis_state

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


def _brief_or_404(session: Session, brief_id: str) -> Brief:
    item = session.get(Brief, brief_id)
    if not item:
        raise HTTPException(status_code=404, detail="Brief not found")
    return item


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
    return {
        "id": paper.id,
        "title": paper.title,
        "first_author": paper.first_author,
        "corresponding_author": paper.corresponding_author,
        "primary_affiliation": paper.primary_affiliation,
        "source": paper.source,
        "source_url": paper.source_url,
        "published_at": paper.published_at.isoformat() if paper.published_at else None,
        "summary": paper.summary,
        "agent_comment": paper.agent_comment,
        "problem": paper.problem,
        "innovation": paper.innovation,
        "experiment": paper.experiment,
        "limitations": paper.limitations,
        "research_score": paper.research_score,
        "relevance_score": paper.relevance_score,
        "recommendation_reason": paper.recommendation_reason,
        "nodes": [dict(row._mapping) for row in nodes],
        "tags": [dict(row._mapping) for row in tags],
        "analysis_status": manual_analysis.get("status"),
        "analysis_error": manual_analysis.get("error"),
        "suggested_tags": metadata.get("suggested_tags", []),
    }


def _serialize_brief(session: Session, brief: Brief, *, detail: bool = True) -> dict:
    payload = model_dict(brief)
    if not detail:
        payload["item_count"] = len(
            session.scalars(select(BriefItem.id).where(BriefItem.brief_id == brief.id)).all()
        )
        return payload
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief.id)
        .order_by(BriefItem.rank)
    ).all()
    payload["items"] = [
        {
            **model_dict(item),
            "paper": (
                {
                    **(
                        _paper_snapshot(session, paper)
                        if item.tier == "manual"
                        else item.snapshot_json or _paper_snapshot(session, paper)
                    ),
                    # 阅读结论属于论文的后续状态，不应被冻结在发布时的简报快照中。
                    "completion_summary": paper.completion_summary,
                }
            ),
            "current_status": paper.status,
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
        return _serialize_brief(session, existing)
    item = Brief(
        brief_date=payload.brief_date,
        title=payload.title or f"{payload.brief_date.isoformat()} 研究简报",
        introduction=payload.introduction,
        task_id=payload.task_id,
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
    limit = int(get_setting(session, "brief_limit", 10))
    if current_count >= limit:
        raise HTTPException(status_code=409, detail=f"Brief item limit reached ({limit})")
    featured_count = len(
        session.scalars(
            select(BriefItem.id).where(BriefItem.brief_id == brief_id, BriefItem.tier == "featured")
        ).all()
    )
    featured_limit = int(get_setting(session, "featured_limit", 5))
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
def publish_brief(brief_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    brief = _brief_or_404(session, brief_id)
    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
        .order_by(BriefItem.rank)
    ).all()
    if not rows:
        raise HTTPException(status_code=409, detail="Cannot publish an empty brief")
    for item, paper in rows:
        item.snapshot_json = _paper_snapshot(session, paper)
    brief.snapshot_json = {
        "title": brief.title,
        "introduction": brief.introduction,
        "paper_ids": [paper.id for _item, paper in rows],
    }
    brief.status = "published"
    brief.published_at = datetime.now(UTC)
    log_operation(
        session,
        actor="agent",
        action="brief.published",
        entity_type="brief",
        entity_id=brief.id,
        task_id=brief.task_id,
        detail={"item_count": len(rows)},
    )
    session.commit()
    return _serialize_brief(session, brief)
