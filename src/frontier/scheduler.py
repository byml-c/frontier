from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .database import SessionLocal
from .services.backup import create_local_git_backup
from .services.common import get_daily_time
from .services.research_day import current_research_date
from .services.task_control import get_or_create_daily_task
from .services.task_dispatcher import submit_task

scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))


def scheduled_daily() -> None:
    if not settings.enable_codex_runs:
        return
    with SessionLocal() as session:
        target_date = current_research_date(session)
        claim = get_or_create_daily_task(
            session,
            target_date,
            payload={"trigger": "schedule"},
        )
    if claim.task is not None and claim.task.status == "queued":
        submit_task(claim.task.id)


def configure_jobs() -> None:
    with SessionLocal() as session:
        hour, minute = get_daily_time(session)
    scheduler.add_job(
        scheduled_daily,
        CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(settings.timezone)),
        id="daily_brief",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        create_local_git_backup,
        CronTrigger(hour=23, minute=50, timezone=ZoneInfo(settings.timezone)),
        id="daily_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def start_scheduler() -> None:
    if not settings.enable_scheduler:
        return
    configure_jobs()
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
