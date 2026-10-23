from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier.database import Base
from frontier.models import InterestModelRun, Paper
from frontier.services.interest_ranker import score_paper, train_daily_ranker


def _ranker_db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _paper(
    index: int,
    *,
    title: str,
    abstract: str,
    level: str,
    categories: list[str],
) -> Paper:
    observed = datetime(2026, 1, 1, tzinfo=UTC)
    screening = {"interest_level": level, "updated_at": observed.isoformat()}
    return Paper(
        title=title,
        normalized_title=title.casefold(),
        abstract=abstract,
        authors=[{"name": f"Author {index}"}],
        status="candidate",
        screening_feedback=screening,
        metadata_json={"categories": categories, "primary_category": categories[0]},
    )


def test_daily_ranker_learns_continuous_relevance_and_confidence() -> None:
    with _ranker_db() as session:
        papers = []
        for index in range(12):
            papers.append(
                _paper(
                    index,
                    title=f"Robot manipulation policy {index}",
                    abstract="A visuomotor manipulation policy with tactile feedback.",
                    level="interested",
                    categories=["cs.RO"],
                )
            )
        for index in range(12):
            papers.append(
                _paper(
                    100 + index,
                    title=f"Database query optimizer {index}",
                    abstract="A relational query planner and database index optimizer.",
                    level="ignored",
                    categories=["cs.DB"],
                )
            )
        session.add_all(papers)
        session.commit()

        ranker, run, metrics = train_daily_ranker(session)
        assert run.status == "active"
        assert metrics["sample_count"] == 24
        assert ranker.score_document(
            "Robot manipulation policy with tactile visuomotor control categories"
        ).score > ranker.score_document(
            "Database query optimizer with relational index planning"
        ).score

        target = _paper(
            999,
            title="Robot manipulation with tactile feedback",
            abstract="A visuomotor policy for embodied manipulation.",
            level="neutral",
            categories=["cs.RO"],
        )
        target.screening_feedback = {}
        session.add(target)
        session.commit()
        ranked = score_paper(ranker, target)
        assert 0 <= ranked.score <= 1
        assert 0 <= ranked.confidence <= 1
        assert target.model_version == run.version
        assert target.model_relevance_score == ranked.score
        assert "interest_model" in target.metadata_json


def test_ranker_versions_retire_previous_active_model() -> None:
    with _ranker_db() as session:
        for index in range(24):
            level = "interested" if index % 2 == 0 else "ignored"
            session.add(
                _paper(
                    index,
                    title=(
                        "Embodied robot manipulation"
                        if level == "interested"
                        else "Database systems"
                    )
                    + f" {index}",
                    abstract="Robot policy" if level == "interested" else "Query planner",
                    level=level,
                    categories=["cs.RO"] if level == "interested" else ["cs.DB"],
                )
            )
        session.commit()
        first, _, _ = train_daily_ranker(
            session, now=datetime(2026, 2, 1, tzinfo=UTC)
        )
        second, _, _ = train_daily_ranker(
            session, now=datetime(2026, 2, 2, tzinfo=UTC)
        )
        assert first.version != second.version
        runs = session.scalars(select(InterestModelRun)).all()
        assert sum(run.status == "active" for run in runs) == 1
        assert sum(run.status == "retired" for run in runs) == 1
