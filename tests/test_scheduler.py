from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from frontier import scheduler
from frontier.database import Base
from frontier.models import Brief


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
    monkeypatch.setattr(
        scheduler,
        "enqueue_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("published brief must prevent a duplicate task")
        ),
    )

    scheduler.scheduled_daily()
