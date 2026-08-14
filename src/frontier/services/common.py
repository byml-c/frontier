from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting, OperationLog

DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_hour": 5,
    "daily_minute": 0,
    "abstract_limit": 50,
    "fulltext_limit": 19,
    "brief_limit": 9,
    "featured_limit": 4,
    "candidate_lookback_days": 14,
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
