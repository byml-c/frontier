from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..models import Conversation


def find_paper_conversation(
    session: Session,
    paper_id: str,
    *,
    include_archived_fallback: bool = False,
) -> Conversation | None:
    """Resolve the logical main Conversation for a paper deterministically."""
    statement = select(Conversation).where(
        Conversation.kind == "paper",
        Conversation.reference_id == paper_id,
    )
    if not include_archived_fallback:
        statement = statement.where(Conversation.status == "active")
    return session.scalar(
        statement.order_by(
            case((Conversation.status == "active", 0), else_=1),
            case((Conversation.codex_session_id.is_not(None), 0), else_=1),
            Conversation.created_at.asc(),
            Conversation.id.asc(),
        ).limit(1)
    )


def get_or_create_paper_conversation(
    session: Session,
    paper_id: str,
    *,
    title: str | None = "论文问答",
) -> Conversation:
    """Return the active paper root, creating it when the paper has none."""
    conversation = find_paper_conversation(session, paper_id)
    if conversation is not None:
        return conversation
    conversation = Conversation(
        kind="paper",
        reference_id=paper_id,
        title=title,
        status="active",
    )
    session.add(conversation)
    session.flush()
    return conversation
