from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    anonymous_device_id,
    anonymous_profile,
    current_anonymous_device_id,
    replace_anonymous_device,
)
from ..database import get_session
from ..models import (
    Annotation,
    DailyReply,
    DailyReview,
    DailyReviewRevision,
    DailySelection,
    DailySelectionItem,
    Paper,
    PaperNode,
    PaperTag,
    ResearchNode,
    Tag,
)
from ..services.common import RESEARCH_GROUP_SUMMARY_KEY, log_operation, model_dict
from ..services.completion_summary import (
    completion_summary_structured,
)
from ..services.daily_selections import (
    DailySelectionAppendConflictError,
    append_daily_selection,
    daily_selection_items,
)
from ..services.paper_sources import arxiv_html_url, arxiv_pdf_url
from ..services.research_day import current_research_date
from ..services.research_groups import group_for_paper, serialize_group

router = APIRouter(prefix="/api/daily", tags=["daily"])


class DailySelectionRequest(BaseModel):
    paper_id: str


class DailyReviewCreate(BaseModel):
    score: int = Field(ge=1, le=6)
    confidence: int = Field(ge=1, le=5)
    content: str = Field(min_length=1, max_length=12_000)


class DailyReviewUpdate(DailyReviewCreate):
    pass


class DailyReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)


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
        "authors": [
            {"name": str(author.get("name") or "")}
            for author in (paper.authors or [])
            if str(author.get("name") or "").strip()
        ],
        "first_author": paper.first_author,
        "corresponding_author": paper.corresponding_author,
        "primary_affiliation": paper.primary_affiliation,
        "research_group_id": paper.research_group_id,
        "research_group": (
            serialize_group(session, group)
            if (group := group_for_paper(session, paper.id)) is not None
            else None
        ),
        "research_group_summary": (paper.metadata_json or {}).get(RESEARCH_GROUP_SUMMARY_KEY),
        "source": paper.source,
        "source_url": paper.source_url,
        "pdf_source_url": arxiv_pdf_url(paper),
        "html_source_url": arxiv_html_url(paper),
        "arxiv_id": paper.arxiv_id,
        "published_at": paper.published_at.isoformat() if paper.published_at else None,
        "status": paper.status,
        "catalog_summary": paper.catalog_summary,
        "summary": paper.summary,
        "agent_comment": paper.agent_comment,
        "problem": paper.problem,
        "innovation": paper.innovation,
        "experiment": paper.experiment,
        "limitations": paper.limitations,
        "open_questions": (paper.metadata_json or {}).get("open_questions"),
        "recommendation_reason": paper.recommendation_reason,
        "completion_summary_structured": completion_summary_structured(
            paper, include_user_attitude=False
        ),
        "tags": [dict(row._mapping) for row in tags],
        "nodes": [dict(row._mapping) for row in nodes],
    }


def _serialize(session: Session, selection: DailySelectionItem) -> dict:
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
        "rank": selection.rank,
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


def _serialize_reply(reply: DailyReply, viewer_id: str) -> dict:
    return {
        "id": reply.id,
        "author": anonymous_profile(reply.device_id),
        "is_mine": reply.device_id == viewer_id,
        "content": reply.content,
        "created_at": model_dict(reply)["created_at"],
    }


def _serialize_review(session: Session, review: DailyReview, viewer_id: str) -> dict:
    replies = session.scalars(
        select(DailyReply).where(DailyReply.review_id == review.id).order_by(DailyReply.created_at)
    ).all()
    initial_score = session.scalar(
        select(DailyReviewRevision.previous_score)
        .where(DailyReviewRevision.review_id == review.id)
        .order_by(DailyReviewRevision.created_at, DailyReviewRevision.id)
        .limit(1)
    )
    return {
        "id": review.id,
        "author": anonymous_profile(review.device_id),
        "is_mine": review.device_id == viewer_id,
        "score": review.score,
        "initial_score": initial_score if initial_score is not None else review.score,
        "confidence": review.confidence,
        "content": review.content,
        "created_at": model_dict(review)["created_at"],
        "updated_at": model_dict(review)["updated_at"],
        "replies": [_serialize_reply(reply, viewer_id) for reply in replies],
    }


def _public_daily_paper_or_404(session: Session, paper_id: str) -> Paper:
    selected = session.scalar(
        select(DailySelectionItem.selection_date)
        .where(DailySelectionItem.paper_id == paper_id)
        .limit(1)
    )
    if not selected:
        selected = session.scalar(
            select(DailySelection.selection_date)
            .where(DailySelection.paper_id == paper_id)
            .limit(1)
        )
    paper = session.get(Paper, paper_id)
    if not selected or not paper:
        raise HTTPException(status_code=404, detail="这篇论文不在每日精选中")
    return paper


def _discussion(session: Session, paper: Paper, viewer_id: str) -> dict:
    reviews = session.scalars(
        select(DailyReview)
        .where(DailyReview.paper_id == paper.id)
        .order_by(DailyReview.created_at.desc())
    ).all()
    average = sum(item.score for item in reviews) / len(reviews) if reviews else None
    has_posted = bool(
        session.scalar(select(DailyReview.id).where(DailyReview.device_id == viewer_id).limit(1))
        or session.scalar(select(DailyReply.id).where(DailyReply.device_id == viewer_id).limit(1))
    )
    return {
        "paper_id": paper.id,
        "viewer": anonymous_profile(viewer_id),
        "can_rotate_identity": not has_posted,
        "review_count": len(reviews),
        "average_score": round(average, 2) if average is not None else None,
        "reviews": [_serialize_review(session, review, viewer_id) for review in reviews],
    }


@router.post("/identity/rotate")
def rotate_identity(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    viewer_id = current_anonymous_device_id(request)
    has_posted = bool(
        viewer_id
        and (
            session.scalar(
                select(DailyReview.id).where(DailyReview.device_id == viewer_id).limit(1)
            )
            or session.scalar(
                select(DailyReply.id).where(DailyReply.device_id == viewer_id).limit(1)
            )
        )
    )
    if has_posted:
        raise HTTPException(status_code=409, detail="发表内容后不能更换匿名 ID")
    return {
        "viewer": anonymous_profile(replace_anonymous_device(response)),
        "can_rotate_identity": True,
    }


@router.get("")
def get_daily(
    session: Annotated[Session, Depends(get_session)],
    target_date: date | None = None,
) -> dict:
    selected_date = target_date or current_research_date(session)
    available_dates = session.scalars(
        select(DailySelectionItem.selection_date)
        .distinct()
        .order_by(DailySelectionItem.selection_date.desc())
    ).all()
    selections = daily_selection_items(session, selected_date)
    serialized = [_serialize(session, selection) for selection in selections]
    return {
        "date": selected_date.isoformat(),
        "available_dates": [item.isoformat() for item in available_dates],
        "selections": serialized,
        "selection": serialized[0] if serialized else None,
    }


@router.get("/papers/{paper_id}/reviews")
def list_reviews(
    paper_id: str,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _public_daily_paper_or_404(session, paper_id)
    viewer_id = anonymous_device_id(request, response)
    return _discussion(session, paper, viewer_id)


@router.post("/papers/{paper_id}/reviews")
def create_review(
    paper_id: str,
    payload: DailyReviewCreate,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _public_daily_paper_or_404(session, paper_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="评语不能为空")
    viewer_id = anonymous_device_id(request, response)
    existing = session.scalar(
        select(DailyReview).where(
            DailyReview.paper_id == paper.id,
            DailyReview.device_id == viewer_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="你已经评审过这篇论文，请编辑已有评审")
    review = DailyReview(
        paper_id=paper.id,
        device_id=viewer_id,
        score=payload.score,
        confidence=payload.confidence,
        content=content,
    )
    session.add(review)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="你已经评审过这篇论文，请编辑已有评审",
        ) from exc
    return _serialize_review(session, review, viewer_id)


@router.put("/papers/{paper_id}/reviews/mine")
def update_my_review(
    paper_id: str,
    payload: DailyReviewUpdate,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = _public_daily_paper_or_404(session, paper_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="评语不能为空")
    viewer_id = anonymous_device_id(request, response)
    review = session.scalar(
        select(DailyReview).where(
            DailyReview.paper_id == paper.id,
            DailyReview.device_id == viewer_id,
        )
    )
    if not review:
        raise HTTPException(status_code=404, detail="你还没有评审这篇论文")
    changed = (
        review.score != payload.score
        or review.confidence != payload.confidence
        or review.content != content
    )
    if changed:
        session.add(
            DailyReviewRevision(
                review_id=review.id,
                previous_score=review.score,
                score=payload.score,
                previous_confidence=review.confidence,
                confidence=payload.confidence,
            )
        )
        review.score = payload.score
        review.confidence = payload.confidence
        review.content = content
        session.commit()
    return _serialize_review(session, review, viewer_id)


@router.post("/reviews/{review_id}/replies")
def create_reply(
    review_id: str,
    payload: DailyReplyCreate,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    review = session.get(DailyReview, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="评论不存在")
    _public_daily_paper_or_404(session, review.paper_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="回复不能为空")
    viewer_id = anonymous_device_id(request, response)
    reply = DailyReply(review_id=review.id, device_id=viewer_id, content=content)
    session.add(reply)
    session.commit()
    return _serialize_reply(reply, viewer_id)


@router.put("/{target_date}")
def set_daily(
    target_date: date,
    payload: DailySelectionRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = session.get(Paper, payload.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    try:
        selection, created = append_daily_selection(
            session,
            selection_date=target_date,
            paper_id=paper.id,
        )
    except DailySelectionAppendConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="同时添加的请求发生冲突，请重试",
        ) from exc
    if created:
        log_operation(
            session,
            actor="user",
            action="daily_selection.add",
            entity_type="paper",
            entity_id=paper.id,
            detail={"date": target_date.isoformat(), "rank": selection.rank},
        )
    session.commit()
    return _serialize(session, selection)
