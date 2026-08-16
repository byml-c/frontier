from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .agent_runner import enqueue_task, run_task
from .config import settings
from .database import SessionLocal
from .models import Brief, TaskRun
from .services.backup import create_local_git_backup
from .services.common import get_setting
from .services.research_day import current_research_date

scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))


def scheduled_daily() -> None:
    with SessionLocal() as session:
        target_date = current_research_date(session)
        target = target_date.isoformat()
        published = session.scalar(
            select(Brief.id).where(
                Brief.brief_date == target_date,
                Brief.status == "published",
            )
        )
        if published:
            return
        existing = session.scalar(
            select(TaskRun).where(
                TaskRun.kind == "daily_brief",
                TaskRun.payload_json["date"].as_string() == target,
                TaskRun.status.in_(["queued", "running", "completed"]),
            )
        )
        if existing:
            return
    task_id = enqueue_task("daily_brief", {"date": target, "trigger": "schedule"})
    if settings.enable_codex_runs:
        run_task(task_id)


def configure_jobs() -> None:
    with SessionLocal() as session:
        hour = int(get_setting(session, "daily_hour", 5))
        minute = int(get_setting(session, "daily_minute", 0))
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
