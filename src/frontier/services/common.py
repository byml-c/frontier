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
# Keep the old action name so an existing installation can move through both
# default changes without rewriting the operation log history.
BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION = "setting.default.brief_limit.8"
BRIEF_LIMIT_NEW_DEFAULT_MIGRATION_ACTION = "setting.default.brief_limit.50"
BROAD_DISCOVERY_MIGRATION_ACTION = "setting.discovery.broad_domains.v1"
BROAD_DISCOVERY_WINDOW_MIGRATION_ACTION = "setting.discovery.window_prefilter.v1"
DAILY_TIME_MIGRATION_ACTION = "setting.default.daily_time.0905.v1"
AGENT_BIBLIOGRAPHY_KEY = "agent_bibliography"
RESEARCH_GROUP_SUMMARY_KEY = "research_group_summary"
BROAD_DISCOVERY_DOMAINS = ["3D vision", "embodied intelligence", "AI agents"]
DISCOVERY_CATEGORIES = ["cs.CV", "cs.RO", "cs.AI", "cs.LG", "cs.CL", "cs.MA", "cs.HC", "cs.SE"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_hour": 9,
    "daily_minute": 5,
    "daily_time": DAILY_TIME_DEFAULT,
    "abstract_limit": 100,
    "fulltext_limit": 10,
    "brief_limit": BRIEF_LIMIT_DEFAULT,
    "featured_limit": FEATURED_LIMIT_DEFAULT,
    "candidate_lookback_days": 3,
    "discovery_scan_limit": 300,
    "discovery_prefilter_limit": 120,
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
    had_daily_time = "daily_time" in existing
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing:
            session.add(AppSetting(key=key, value_json=value))
    session.flush()
    brief_limit = session.get(AppSetting, "brief_limit")
    legacy_default_migrated = session.scalar(
        select(OperationLog.id)
        .where(OperationLog.action == BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION)
        .limit(1)
    )
    if not legacy_default_migrated:
        previous_value = int(brief_limit.value_json) if brief_limit else None
        if brief_limit and previous_value in {5, 8}:
            brief_limit.value_json = 8
        log_operation(
            session,
            action=BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION,
            detail={"previous_value": previous_value, "default_value": 8},
        )
    session.flush()
    if not session.scalar(
        select(OperationLog.id)
        .where(OperationLog.action == BRIEF_LIMIT_NEW_DEFAULT_MIGRATION_ACTION)
        .limit(1)
    ):
        previous_value = int(brief_limit.value_json) if brief_limit else None
        fulltext_limit = session.get(AppSetting, "fulltext_limit")
        if fulltext_limit and int(fulltext_limit.value_json) == 19:
            fulltext_limit.value_json = 10
        featured_limit = session.get(AppSetting, "featured_limit")
        if featured_limit and int(featured_limit.value_json) == 4:
            featured_limit.value_json = FEATURED_LIMIT_DEFAULT
        if brief_limit and previous_value == 8:
            brief_limit.value_json = BRIEF_LIMIT_DEFAULT
        log_operation(
            session,
            action=BRIEF_LIMIT_NEW_DEFAULT_MIGRATION_ACTION,
            detail={"previous_value": previous_value, "default_value": BRIEF_LIMIT_DEFAULT},
        )
    if brief_limit and int(brief_limit.value_json) > BRIEF_LIMIT_MAX:
        brief_limit.value_json = BRIEF_LIMIT_MAX
    if not session.scalar(
        select(OperationLog.id).where(OperationLog.action == BROAD_DISCOVERY_MIGRATION_ACTION)
    ):
        abstract_limit = session.get(AppSetting, "abstract_limit")
        previous = abstract_limit.value_json if abstract_limit else None
        if abstract_limit and previous == 50:
            abstract_limit.value_json = 100
        # Keep initial_interest_domains as fine-grained preference seeds. Existing
        # custom domains, limits, memories and all paper data remain untouched.
        log_operation(
            session,
            action=BROAD_DISCOVERY_MIGRATION_ACTION,
            detail={"previous_abstract_limit": previous, "candidate_target_default": 100},
        )
    if not session.scalar(
        select(OperationLog.id).where(
            OperationLog.action == BROAD_DISCOVERY_WINDOW_MIGRATION_ACTION
        )
    ):
        lookback = session.get(AppSetting, "candidate_lookback_days")
        scan_limit = session.get(AppSetting, "discovery_scan_limit")
        previous = {
            "candidate_lookback_days": lookback.value_json if lookback else None,
            "discovery_scan_limit": scan_limit.value_json if scan_limit else None,
        }
        if lookback and lookback.value_json == 14:
            lookback.value_json = 3
        if scan_limit and scan_limit.value_json == 1000:
            scan_limit.value_json = 300
        log_operation(
            session,
            action=BROAD_DISCOVERY_WINDOW_MIGRATION_ACTION,
            detail={"previous": previous, "lookback_days": 3, "scan_limit": 300},
        )
    if not session.scalar(
        select(OperationLog.id).where(OperationLog.action == DAILY_TIME_MIGRATION_ACTION)
    ):
        daily_time = session.get(AppSetting, "daily_time")
        old_hour = session.get(AppSetting, "daily_hour")
        old_minute = session.get(AppSetting, "daily_minute")
        previous = daily_time.value_json if daily_time else None
        if not had_daily_time and daily_time:
            hour = int(old_hour.value_json) if old_hour else 5
            minute = int(old_minute.value_json) if old_minute else 0
            # A legacy untouched 05:00 value represents the former default;
            # preserve any other user-selected time during migration.
            daily_time.value_json = (
                DAILY_TIME_DEFAULT if (hour, minute) == (5, 0) else f"{hour:02d}:{minute:02d}"
            )
        log_operation(
            session,
            action=DAILY_TIME_MIGRATION_ACTION,
            detail={"previous_value": previous, "default_value": DAILY_TIME_DEFAULT},
        )
    # Keep legacy API readers consistent with the canonical minute-precision time.
    hour, minute = get_daily_time(session)
    session.get(AppSetting, "daily_hour").value_json = hour
    session.get(AppSetting, "daily_minute").value_json = minute
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
    """Read the canonical time, falling back to legacy hour/minute settings."""
    daily_time = session.get(AppSetting, "daily_time")
    if daily_time is not None:
        return parse_daily_time(daily_time.value_json)
    return (
        int(get_setting(session, "daily_hour", 9)),
        int(get_setting(session, "daily_minute", 5)),
    )
