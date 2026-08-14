from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import settings
from .common import get_setting


def research_date_at(
    now: datetime,
    *,
    rollover_hour: int = 5,
    rollover_minute: int = 0,
    timezone: str | None = None,
) -> date:
    """Return the research day whose boundary is the daily update time."""
    zone = ZoneInfo(timezone or settings.timezone)
    local_now = now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    rollover = time(hour=rollover_hour, minute=rollover_minute)
    if local_now.time().replace(tzinfo=None) < rollover:
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def current_research_date(session: Session, *, now: datetime | None = None) -> date:
    """Use the configured daily schedule as the shared date boundary."""
    zone = ZoneInfo(settings.timezone)
    return research_date_at(
        now or datetime.now(zone),
        rollover_hour=int(get_setting(session, "daily_hour", 5)),
        rollover_minute=int(get_setting(session, "daily_minute", 0)),
        timezone=settings.timezone,
    )
