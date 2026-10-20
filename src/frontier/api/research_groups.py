from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import ResearchGroup
from ..schemas import ResearchGroupCreate, ResearchGroupUpdate
from ..services.common import normalize_title
from ..services.research_groups import serialize_group

router = APIRouter(prefix="/api/research-groups", tags=["research-groups"])


def _group_or_404(session: Session, group_id: str) -> ResearchGroup:
    group = session.get(ResearchGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Research group not found")
    return group


@router.get("")
def list_research_groups(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict]:
    statement = select(ResearchGroup).order_by(ResearchGroup.updated_at.desc())
    groups = list(session.scalars(statement).all())
    if q and (query := q.strip()):
        needle = query.casefold()

        def matches(group: ResearchGroup) -> bool:
            values = [
                group.name,
                group.institution,
                group.representative_summary or "",
                *(group.aliases or []),
                *(group.research_focus or []),
            ]
            return any(needle in str(value).casefold() for value in values if value)

        groups = [group for group in groups if matches(group)]
    if status:
        groups = [group for group in groups if group.verification_status == status]
    return [serialize_group(session, group) for group in groups[:limit]]


@router.get("/{group_id}")
def get_research_group(
    group_id: str, session: Annotated[Session, Depends(get_session)]
) -> dict:
    return serialize_group(session, _group_or_404(session, group_id), detail=True)


@router.post("")
def create_research_group(
    payload: ResearchGroupCreate, session: Annotated[Session, Depends(get_session)]
) -> dict:
    normalized_name = normalize_title(payload.name)
    normalized_institution = normalize_title(payload.institution)
    existing = session.scalar(
        select(ResearchGroup).where(
            ResearchGroup.normalized_name == normalized_name,
            ResearchGroup.normalized_institution == normalized_institution,
        )
    )
    if existing:
        return serialize_group(session, existing, detail=True)
    group = ResearchGroup(
        name=payload.name.strip(),
        normalized_name=normalized_name,
        institution=payload.institution.strip(),
        normalized_institution=normalized_institution,
        homepage_url=payload.homepage_url,
        aliases=payload.aliases,
        research_focus=payload.research_focus,
        representative_contributions=payload.representative_contributions,
        representative_summary=payload.representative_summary,
        confidence=payload.confidence,
        verification_status=payload.verification_status,
    )
    session.add(group)
    session.commit()
    return serialize_group(session, group, detail=True)


@router.patch("/{group_id}")
def update_research_group(
    group_id: str,
    payload: ResearchGroupUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    group = _group_or_404(session, group_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(group, key, value)
    if "name" in changes:
        group.normalized_name = normalize_title(group.name)
    if "institution" in changes:
        group.normalized_institution = normalize_title(group.institution)
    group.verification_status = "verified"
    session.commit()
    return serialize_group(session, group, detail=True)
