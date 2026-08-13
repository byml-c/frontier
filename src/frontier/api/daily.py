from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_session
from ..models import Annotation, DailySelection, Paper, PaperNode, PaperTag, ResearchNode, Tag
from ..services.common import log_operation, model_dict

router = APIRouter(prefix="/api/daily", tags=["daily"])


class DailySelectionRequest(BaseModel):
    paper_id: str


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _public_paper(session: Session, paper: Paper) -> dict:
    tags = session.execute(
        select(Tag.id, Tag.name, Tag.color)
        .join(PaperTag, PaperTag.tag_id == Tag.id)
        .where(PaperTag.paper_id == paper.id)
        .order_by(Tag.name)
    ).all()
    nodes = session.execute(
        select(ResearchNode.id, ResearchNode.name)
        .join(PaperNode, PaperNode.node_id == ResearchNode.id)
        .where(PaperNode.paper_id == paper.id)
        .order_by(ResearchNode.name)
    ).all()
    return {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "first_author": paper.first_author,
        "corresponding_author": paper.corresponding_author,
        "primary_affiliation": paper.primary_affiliation,
        "source": paper.source,
        "source_url": paper.source_url,
        "arxiv_id": paper.arxiv_id,
        "published_at": paper.published_at.isoformat() if paper.published_at else None,
        "status": paper.status,
        "summary": paper.summary,
        "agent_comment": paper.agent_comment,
        "problem": paper.problem,
        "innovation": paper.innovation,
        "experiment": paper.experiment,
        "limitations": paper.limitations,
        "recommendation_reason": paper.recommendation_reason,
        "completion_summary": paper.completion_summary,
        "is_interested": paper.is_interested,
        "is_quality": paper.is_quality,
        "tags": [dict(row._mapping) for row in tags],
        "nodes": [dict(row._mapping) for row in nodes],
    }


def _serialize(session: Session, selection: DailySelection) -> dict:
    paper = session.get(Paper, selection.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="精选论文不存在")
    annotations = session.scalars(
        select(Annotation)
        .where(Annotation.paper_id == paper.id, Annotation.kind.in_(["note", "highlight"]))
        .order_by(Annotation.page_number, Annotation.created_at)
    ).all()
    return {
        "date": selection.selection_date.isoformat(),
        "paper": _public_paper(session, paper),
        "annotations": [
            {
                "id": item.id,
                "kind": item.kind,
                "page_number": item.page_number,
                "selected_text": item.selected_text,
                "content": item.content,
                "color": item.color,
                "created_at": model_dict(item)["created_at"],
            }
            for item in annotations
        ],
    }


@router.get("")
def get_daily(
    session: Annotated[Session, Depends(get_session)],
    target_date: date | None = None,
) -> dict:
    selected_date = target_date or _today()
    available_dates = session.scalars(
        select(DailySelection.selection_date).order_by(DailySelection.selection_date.desc())
    ).all()
    selection = session.get(DailySelection, selected_date)
    return {
        "date": selected_date.isoformat(),
        "available_dates": [item.isoformat() for item in available_dates],
        "selection": _serialize(session, selection) if selection else None,
    }


@router.put("/{target_date}")
def set_daily(
    target_date: date,
    payload: DailySelectionRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = session.get(Paper, payload.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    selection = session.get(DailySelection, target_date)
    previous_paper_id = selection.paper_id if selection else None
    if selection:
        selection.paper_id = paper.id
    else:
        selection = DailySelection(selection_date=target_date, paper_id=paper.id)
        session.add(selection)
    log_operation(
        session,
        actor="user",
        action="daily_selection.set",
        entity_type="paper",
        entity_id=paper.id,
        detail={"date": target_date.isoformat(), "previous_paper_id": previous_paper_id},
    )
    session.commit()
    return _serialize(session, selection)
