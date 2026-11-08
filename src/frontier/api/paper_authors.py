"""Paper-scoped author cards, curated through the paper conversation's CLI."""

from __future__ import annotations

from typing import Annotated
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Paper, ResearchGroup
from ..services.common import log_operation

router = APIRouter(prefix="/api/papers/{paper_id}/authors", tags=["authors"])
SessionDep = Annotated[Session, Depends(get_session)]


class AuthorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    affiliation: str | None = None
    current_affiliation: str | None = None
    homepage_url: str | None = None
    research_group_id: str | None = None
    role: str | None = None
    evidence: str | None = None


class AuthorCreate(AuthorUpdate):
    name: str = Field(min_length=1, max_length=255)


def _paper(session: Session, paper_id: str) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "Paper not found")
    return paper


def author_cards(paper: Paper) -> list[dict]:
    # Deterministic IDs let legacy records be read without a database write. IDs
    # are persisted on the first edit, so renaming/reordering then keeps identity.
    return [
        {
            **author,
            "id": author.get("id")
            or str(
                uuid5(NAMESPACE_URL, f"frontier:{paper.id}:author:{index}:{author.get('name', '')}")
            ),
        }
        for index, author in enumerate(paper.authors or [])
    ]


def _changes(session: Session, payload: AuthorUpdate) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and (not changes["name"] or not changes["name"].strip()):
        raise HTTPException(422, "Author name cannot be empty")
    changes = {k: v.strip() if isinstance(v, str) else v for k, v in changes.items()}
    group_id = changes.get("research_group_id")
    if group_id and session.get(ResearchGroup, group_id) is None:
        raise HTTPException(404, "Research group not found")
    return changes


def _save(session: Session, paper: Paper, cards: list[dict], action: str) -> None:
    paper.authors = cards
    log_operation(session, actor="agent", action=action, entity_type="paper", entity_id=paper.id)
    session.commit()


@router.get("")
def list_authors(paper_id: str, session: SessionDep, q: str | None = None) -> list[dict]:
    cards = author_cards(_paper(session, paper_id))
    if q:
        needle = q.casefold()
        cards = [
            a
            for a in cards
            if any(
                needle in str(a.get(k) or "").casefold()
                for k in ("name", "affiliation", "current_affiliation")
            )
        ]
    return cards


@router.get("/{author_id}")
def get_author(paper_id: str, author_id: str, session: SessionDep) -> dict:
    for card in author_cards(_paper(session, paper_id)):
        if card["id"] == author_id:
            return card
    raise HTTPException(404, "Author not found")


@router.post("")
def create_author(paper_id: str, payload: AuthorCreate, session: SessionDep) -> dict:
    paper = _paper(session, paper_id)
    cards = author_cards(paper)
    card = {**_changes(session, payload), "id": str(uuid4())}
    cards.append(card)
    _save(session, paper, cards, "paper.author_created")
    return card


@router.patch("/{author_id}")
def update_author(
    paper_id: str, author_id: str, payload: AuthorUpdate, session: SessionDep
) -> dict:
    paper = _paper(session, paper_id)
    cards = author_cards(paper)
    for card in cards:
        if card["id"] == author_id:
            card.update(_changes(session, payload))
            _save(session, paper, cards, "paper.author_updated")
            return card
    raise HTTPException(404, "Author not found")


@router.delete("/{author_id}")
def delete_author(paper_id: str, author_id: str, session: SessionDep) -> dict:
    paper = _paper(session, paper_id)
    cards = author_cards(paper)
    remaining = [card for card in cards if card["id"] != author_id]
    if len(remaining) == len(cards):
        raise HTTPException(404, "Author not found")
    _save(session, paper, remaining, "paper.author_deleted")
    return {"deleted": author_id}
