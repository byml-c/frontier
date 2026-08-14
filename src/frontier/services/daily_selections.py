from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import DailySelection, DailySelectionItem

MAX_APPEND_ATTEMPTS = 5


class DailySelectionAppendConflictError(RuntimeError):
    pass


def _existing_daily_selection(
    session: Session,
    *,
    selection_date: date,
    paper_id: str,
) -> DailySelectionItem | None:
    return session.scalar(
        select(DailySelectionItem).where(
            DailySelectionItem.selection_date == selection_date,
            DailySelectionItem.paper_id == paper_id,
        )
    )


def _next_daily_rank(session: Session, selection_date: date) -> int:
    last_rank = session.scalar(
        select(func.max(DailySelectionItem.rank)).where(
            DailySelectionItem.selection_date == selection_date
        )
    )
    return (last_rank or 0) + 1


def daily_selection_items(session: Session, selection_date: date) -> list[DailySelectionItem]:
    return list(
        session.scalars(
            select(DailySelectionItem)
            .where(DailySelectionItem.selection_date == selection_date)
            .order_by(DailySelectionItem.rank, DailySelectionItem.id)
        ).all()
    )


def append_daily_selection(
    session: Session,
    *,
    selection_date: date,
    paper_id: str,
) -> tuple[DailySelectionItem, bool]:
    existing = _existing_daily_selection(
        session,
        selection_date=selection_date,
        paper_id=paper_id,
    )
    if existing:
        return existing, False

    for _attempt in range(MAX_APPEND_ATTEMPTS):
        rank = _next_daily_rank(session, selection_date)
        item = DailySelectionItem(
            selection_date=selection_date,
            paper_id=paper_id,
            rank=rank,
        )
        try:
            with session.begin_nested():
                session.add(item)
                session.flush()
        except IntegrityError:
            existing = _existing_daily_selection(
                session,
                selection_date=selection_date,
                paper_id=paper_id,
            )
            if existing:
                return existing, False
            continue
        return item, True

    raise DailySelectionAppendConflictError(
        f"Could not append a daily selection for {selection_date} after "
        f"{MAX_APPEND_ATTEMPTS} attempts"
    )


def migrate_legacy_daily_selections(session: Session) -> int:
    """Copy the one-paper-per-day legacy rows into the multi-paper table.

    The legacy table remains untouched so upgrades are reversible and no previously
    published selection can be lost. Existing new-table rows win; a missing legacy
    paper is appended after them.
    """

    migrated = 0
    legacy_selections = session.scalars(
        select(DailySelection).order_by(DailySelection.selection_date)
    ).all()
    for legacy in legacy_selections:
        _, created = append_daily_selection(
            session,
            selection_date=legacy.selection_date,
            paper_id=legacy.paper_id,
        )
        migrated += int(created)
    if migrated:
        session.commit()
    return migrated
