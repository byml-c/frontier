from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from frontier import scheduler
from frontier.database import Base
from frontier.models import Brief, TaskRun


def test_published_brief_is_the_final_scheduler_deduplication_fact(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    target = date(2035, 1, 1)
    with Session(engine) as session:
        session.add(
            Brief(
                brief_date=target,
                title="Already published",
                status="published",
            )
        )
        session.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "current_research_date", lambda _session: target)
    monkeypatch.setattr(scheduler.settings, "enable_codex_runs", True)
    monkeypatch.setattr(
        scheduler,
        "submit_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("published brief must prevent a duplicate task")
        ),
    )

    scheduler.scheduled_daily()


def test_completed_daily_without_published_brief_does_not_block_recovery(
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    target = date(2035, 1, 2)
    with Session(engine) as session:
        session.add(
            TaskRun(
                kind="daily_brief",
                status="completed",
                payload_json={"date": target.isoformat()},
            )
        )
        session.commit()

    submitted: list[str] = []
    monkeypatch.setattr(scheduler, "SessionLocal", sessions)
    monkeypatch.setattr(scheduler, "current_research_date", lambda _session: target)
    monkeypatch.setattr(scheduler.settings, "enable_codex_runs", True)
    monkeypatch.setattr(scheduler, "submit_task", submitted.append)

    scheduler.scheduled_daily()

    with sessions() as session:
        tasks = session.scalars(
            select(TaskRun).where(
                TaskRun.kind == "daily_brief",
                TaskRun.payload_json["date"].as_string() == target.isoformat(),
            )
        ).all()
        assert len(tasks) == 2
        queued = next(task for task in tasks if task.status == "queued")
        assert submitted == [queued.id]
