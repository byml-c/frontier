from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting, OperationLog

BRIEF_LIMIT_DEFAULT = 50
BRIEF_LIMIT_MAX = 50
FEATURED_LIMIT_DEFAULT = 10
DAILY_TIME_DEFAULT = "09:05"
DISCOVERY_FALLBACK_LOOKBACK_DAYS = 7
AGENT_BIBLIOGRAPHY_KEY = "agent_bibliography"
RESEARCH_GROUP_SUMMARY_KEY = "research_group_summary"
RESEARCH_GROUP_PROFILE_KEY = "research_group_profile"
BROAD_DISCOVERY_DOMAINS = ["3D vision", "embodied intelligence", "AI agents"]
DISCOVERY_CATEGORIES = ["cs.RO", "cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.MA"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_time": DAILY_TIME_DEFAULT,
    "brief_limit": BRIEF_LIMIT_DEFAULT,
    "candidate_lookback_days": 3,
    "discovery_domains": BROAD_DISCOVERY_DOMAINS,
    "discovery_arxiv_categories": DISCOVERY_CATEGORIES,
    "initial_interest_domains": [
        "VLA",
        "WAM",
        "mobile manipulation",
        "tactile sensing",
        "robot simulation",
        "embodied AI evaluation",
        "teleoperation",
        "3D vision for embodied intelligence",
    ],
    "log_level": "INFO",
}


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def compact_search_key(value: str) -> str:
    """Normalize notation variants used in short paper or method names."""
    return normalize_title(value).replace(" ", "").replace("π", "pi")


def parse_arxiv_id(value: str) -> tuple[str, int | None] | None:
    match = re.search(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v(\d+))?", value)
    if not match:
        match = re.search(r"([A-Za-z][A-Za-z.\-]+/\d{7})(?:v(\d+))?", value)
    if not match:
        return None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def as_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def model_dict(model: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    return {
        column.name: as_json_value(getattr(model, column.name))
        for column in model.__table__.columns
        if column.name not in excluded
    }


def log_operation(
    session: Session,
    *,
    action: str,
    actor: str = "system",
    entity_type: str | None = None,
    entity_id: str | None = None,
    task_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        OperationLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            task_id=task_id,
            detail_json=detail or {},
        )
    )


def ensure_default_settings(session: Session) -> None:
    existing = set(session.scalars(select(AppSetting.key)).all())
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing:
            session.add(AppSetting(key=key, value_json=value))
    session.commit()


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    setting = session.get(AppSetting, key)
    return setting.value_json if setting else default


def parse_daily_time(value: object) -> tuple[int, int]:
    """Parse a persisted local daily schedule time as (hour, minute)."""
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value) if isinstance(value, str) else None
    if not match:
        raise ValueError("daily_time must use HH:MM in 24-hour format")
    return int(match.group(1)), int(match.group(2))


def get_daily_time(session: Session) -> tuple[int, int]:
    return parse_daily_time(get_setting(session, "daily_time", DAILY_TIME_DEFAULT))
