from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier.database import Base
from frontier.models import Brief, BriefItem, DiscoveryCursor, DiscoveryItem, DiscoveryRun, Paper
from frontier.schemas import PaperCreate
from frontier.services import arxiv_sync


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _training_papers() -> list[Paper]:
    papers = []
    for index in range(12):
        papers.append(
            Paper(
                title=f"Robot manipulation {index}",
                normalized_title=f"robot manipulation {index}",
                abstract="Embodied visuomotor policy with tactile feedback.",
                authors=[{"name": "Alice"}],
                status="candidate",
                metadata_json={"categories": ["cs.RO"], "primary_category": "cs.RO"},
            )
        )
    for index in range(12):
        papers.append(
            Paper(
                title=f"Database optimizer {index}",
                normalized_title=f"database optimizer {index}",
                abstract="Relational query planning and storage.",
                authors=[{"name": "Bob"}],
                status="candidate",
                metadata_json={"categories": ["cs.DB"], "primary_category": "cs.DB"},
            )
        )
    return papers


def test_cursor_sync_fetches_all_pages_and_commits_after_success(monkeypatch) -> None:
    with _session() as session:
        session.add_all(_training_papers())
        session.commit()

        payloads = [
            PaperCreate(
                title="Robot policy with tactile manipulation",
                abstract="An embodied robot learns a manipulation policy.",
                arxiv_id="2610.90001",
                arxiv_version=1,
                published_at=datetime(2026, 10, 23, tzinfo=UTC).date(),
                metadata_json={
                    "categories": ["cs.RO"],
                    "primary_category": "cs.RO",
                    "arxiv_updated_at": "2026-10-23T01:00:00Z",
                },
            ),
            PaperCreate(
                title="Database query optimizer",
                abstract="A relational query planner.",
                arxiv_id="2610.90002",
                arxiv_version=1,
                published_at=datetime(2026, 10, 23, tzinfo=UTC).date(),
                metadata_json={
                    "categories": ["cs.DB"],
                    "primary_category": "cs.DB",
                    "arxiv_updated_at": "2026-10-23T01:01:00Z",
                },
            ),
        ]

        monkeypatch.setattr(
            arxiv_sync,
            "_fetch_oai_records",
            lambda **_kwargs: (
                payloads,
                {
                    "source": "test",
                    "pages": 1,
                    "source_records": 2,
                    "category_matched": 2,
                    "complete": True,
                },
            ),
        )
        result = arxiv_sync.sync_arxiv(
            session,
            now=datetime(2026, 10, 23, 3, 0, tzinfo=UTC),
            bootstrap_days=1,
        )

        assert result["coverage_complete"] is True
        assert result["counts"]["fetched"] == 2
        assert result["counts"]["domain_rejected"] == 1
        assert result["counts"]["promoted_this_run"] == 1
        assert result["counts"]["candidate_pool"] >= 1
        assert session.scalar(select(func.count(Paper.id))) == 25
        assert session.scalar(select(func.count(DiscoveryItem.id))) == 2
        assert session.scalar(select(func.count(DiscoveryRun.id))) == 1
        cursor = session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID)
        assert cursor is not None
        assert cursor.committed_through is not None
        assert cursor.last_error is None


def test_cursor_sync_does_not_advance_on_incomplete_pagination(monkeypatch) -> None:
    with _session() as session:
        session.add_all(_training_papers())
        session.commit()

        def incomplete(**_kwargs):
            raise RuntimeError("empty page before OAI resumption completion")

        monkeypatch.setattr(arxiv_sync, "_fetch_oai_records", incomplete)
        try:
            arxiv_sync.sync_arxiv(
                session,
                now=datetime(2026, 10, 23, 3, 0, tzinfo=UTC),
                bootstrap_days=1,
            )
        except RuntimeError as exc:
            assert "empty page" in str(exc)
        else:
            raise AssertionError("sync should fail before advancing the cursor")
        cursor = session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID)
        assert cursor is not None
        assert cursor.committed_through is None
        assert "empty page" in (cursor.last_error or "")


def test_cursor_sync_has_no_300_or_120_result_cap(monkeypatch) -> None:
    with _session() as session:
        session.add_all(_training_papers())
        session.commit()
        payloads = [
            PaperCreate(
                title=f"Database query optimizer {index}",
                abstract="Relational query planning.",
                arxiv_id=f"2610.{92000 + index:05d}",
                metadata_json={"categories": ["cs.DB"], "arxiv_updated_at": "2026-10-23"},
            )
            for index in range(205)
        ]
        monkeypatch.setattr(
            arxiv_sync,
            "_fetch_oai_records",
            lambda **_kwargs: (
                payloads,
                {
                    "source": "test",
                    "pages": 3,
                    "source_records": 205,
                    "category_matched": 205,
                    "complete": True,
                },
            ),
        )
        result = arxiv_sync.sync_arxiv(
            session,
            now=datetime(2026, 10, 23, 3, 0, tzinfo=UTC),
            bootstrap_days=1,
        )
        assert result["counts"]["fetched"] == 205
        assert result["coverage_complete"] is True
        assert result["counts"]["domain_rejected"] == 205


def test_oai_fetch_follows_resumption_token_and_filters_categories(monkeypatch) -> None:
    pages = [
        """<?xml version="1.0"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <ListRecords>
            <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
              <id>2610.93001</id><created>2026-10-22</created><updated>2026-10-23</updated>
              <authors><author><keyname>Lee</keyname><forenames>Ada</forenames></author></authors>
              <title>Robot manipulation</title><categories>cs.RO cs.AI</categories>
              <abstract>Embodied robot policy.</abstract>
            </arXiv></metadata></record>
            <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
              <id>2610.93002</id><created>2026-10-22</created><updated>2026-10-23</updated>
              <authors><author><keyname>Doe</keyname><forenames>Sam</forenames></author></authors>
              <title>Database optimizer</title><categories>cs.DB</categories>
              <abstract>Query planning.</abstract>
            </arXiv></metadata></record>
            <resumptionToken>next-page</resumptionToken>
          </ListRecords>
        </OAI-PMH>""",
        """<?xml version="1.0"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <ListRecords>
            <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
              <id>2610.93003</id><created>2026-10-22</created><updated>2026-10-23</updated>
              <authors><author><keyname>Kim</keyname><forenames>Lin</forenames></author></authors>
              <title>Tactile policy</title><categories>cs.RO</categories>
              <abstract>Robot touch.</abstract>
            </arXiv></metadata></record>
          </ListRecords>
        </OAI-PMH>""",
    ]
    requests = []

    def get(_url, *, params, **_kwargs):
        requests.append(dict(params))
        return SimpleNamespace(
            content=pages[len(requests) - 1].encode(),
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(arxiv_sync.httpx, "get", get)
    monkeypatch.setattr(arxiv_sync, "sleep", lambda _seconds: None)
    items, stats = arxiv_sync._fetch_oai_records(
        start=datetime(2026, 10, 20, tzinfo=UTC),
        end=datetime(2026, 10, 23, tzinfo=UTC),
        categories=["cs.RO", "cs.AI"],
    )
    assert [item.arxiv_id for item in items] == ["2610.93001", "2610.93003"]
    assert stats["pages"] == 2
    assert stats["source_records"] == 3
    assert stats["complete"] is True
    assert requests[0]["from"] == "2026-10-20"
    assert requests[1] == {"verb": "ListRecords", "resumptionToken": "next-page"}


def _robot(identifier: str, stamp: str) -> PaperCreate:
    return PaperCreate(
        title=f"Robot manipulation {identifier}",
        arxiv_id=identifier,
        metadata_json={"categories": ["cs.RO"], "arxiv_updated_at": stamp},
    )


def _mock_batch(monkeypatch, payloads):
    calls = []
    ranker = SimpleNamespace(relevance_threshold=0.5, confidence_threshold=0.62)
    model = SimpleNamespace(
        version="test-model",
        sample_count=24,
        validation_mae=0.1,
        residual_scale=0.1,
    )
    monkeypatch.setattr(arxiv_sync, "train_daily_ranker", lambda *a, **k: (ranker, model, {}))

    def score(_ranker, paper):
        paper.model_score = 0.8
        paper.model_review_required = False
        return SimpleNamespace(score=0.8, confidence=0.9, review_required=False, support=0.9)

    def fetch(**kwargs):
        calls.append(kwargs)
        return payloads, {"complete": True, "pages": 1}

    monkeypatch.setattr(arxiv_sync, "score_paper", score)
    monkeypatch.setattr(arxiv_sync, "_fetch_oai_records", fetch)
    return calls


def test_brief_date_fixes_cutoff_and_limits_candidates_to_its_source_batch(monkeypatch):
    payloads = [
        _robot("2610.94001", "2026-10-26T12:00:00Z"),
        _robot("2610.94002", "2026-10-25T12:00:00Z"),
        _robot("2610.94003", "2026-10-27T02:00:00Z"),
    ]
    calls = _mock_batch(monkeypatch, payloads)
    with _session() as session:
        session.add(
            DiscoveryCursor(
                id=arxiv_sync.SYNC_CURSOR_ID,
                committed_through=datetime(2026, 10, 26, 1, 5, tzinfo=UTC),
            )
        )
        # This old library candidate passes the former discovered_at >= start filter.
        old = Paper(
            title="Old candidate",
            normalized_title="old candidate",
            discovered_at=datetime(2026, 10, 26, 2, tzinfo=UTC),
            model_score=0.95,
            model_review_required=False,
        )
        session.add(old)
        session.commit()
        result = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 28, 12, tzinfo=UTC),
        )
        assert calls[0]["start"] == datetime(2026, 10, 26, 1, 5, tzinfo=UTC)
        assert calls[0]["end"] == datetime(2026, 10, 27, 1, 5, tzinfo=UTC)
        assert result["brief_date"] == "2026-10-27"
        assert result["counts"]["outside_window"] == 2
        assert len(result["paper_ids"]) == 1
        assert old.id not in result["paper_ids"]
        paper = session.get(Paper, result["paper_ids"][0])
        assert paper.arxiv_id == "2610.94001"
        assert session.scalar(select(func.count(Paper.id))) == 2


def test_same_brief_retry_reuses_batch_and_does_not_fetch_or_repeat_rated_papers(monkeypatch):
    calls = _mock_batch(monkeypatch, [_robot("2610.94004", "2026-10-27T00:00:00Z")])
    with _session() as session:
        result = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 27, 2, tzinfo=UTC),
        )
        replay = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 28, 12, tzinfo=UTC),
        )
        assert replay["run_id"] == result["run_id"]
        assert replay["paper_ids"] == result["paper_ids"]
        assert replay["cursor"] == result["cursor"]
        assert len(calls) == 1
        paper = session.get(Paper, result["paper_ids"][0])
        paper.user_score = 0.5
        session.commit()
        after_rating = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 28, 12, tzinfo=UTC),
        )
        assert after_rating["paper_ids"] == []
        assert after_rating["counts"]["candidate_pool"] == 0
        assert len(calls) == 1


def test_batch_routing_rejects_previously_published_papers_even_when_still_candidates(monkeypatch):
    _mock_batch(monkeypatch, [_robot("2610.94005", "2026-10-27T00:00:00Z")])
    with _session() as session:
        first = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 27, 2, tzinfo=UTC),
        )
        brief = Brief(brief_date=date(2026, 10, 27), title="Published", status="published")
        session.add(brief)
        session.flush()
        session.add(BriefItem(brief_id=brief.id, paper_id=first["paper_ids"][0], rank=1))
        session.commit()
        replay = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 28, 2, tzinfo=UTC),
        )
        assert replay["paper_ids"] == []


def test_partial_ingestion_recovers_only_the_same_window_staging(monkeypatch):
    _mock_batch(monkeypatch, [_robot("2610.94006", "2026-10-27T00:00:00Z")])
    with _session() as session:

        def interrupted(session, *args, **kwargs):
            session.commit()
            raise RuntimeError("interrupted after staging was committed")

        original_routing = arxiv_sync._batch_candidate_routing
        monkeypatch.setattr(arxiv_sync, "_batch_candidate_routing", interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            arxiv_sync.sync_arxiv(
                session,
                through_date=date(2026, 10, 27),
                now=datetime(2026, 10, 27, 2, tzinfo=UTC),
            )
        assert session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID).committed_through is None
        monkeypatch.setattr(arxiv_sync, "_batch_candidate_routing", original_routing)
        recovered = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 28, 2, tzinfo=UTC),
        )
        assert len(recovered["paper_ids"]) == 1
        assert recovered["counts"]["promoted_this_run"] == 0
        assert session.scalar(select(func.count(Paper.id))) == 1


def test_cursor_cannot_advance_to_a_future_brief(monkeypatch):
    calls = _mock_batch(monkeypatch, [])
    with _session() as session:
        with pytest.raises(ValueError, match="future"):
            arxiv_sync.sync_arxiv(
                session,
                through_date=date(2026, 10, 28),
                now=datetime(2026, 10, 27, 2, tzinfo=UTC),
            )
        assert calls == []


def test_failure_after_cursor_assignment_does_not_commit_the_advance(monkeypatch):
    _mock_batch(monkeypatch, [_robot("2610.94007", "2026-10-27T00:00:00Z")])

    def failed_audit(*args, **kwargs):
        raise RuntimeError("audit failed before final commit")

    monkeypatch.setattr(arxiv_sync, "log_operation", failed_audit)
    with _session() as session:
        with pytest.raises(RuntimeError, match="audit failed"):
            arxiv_sync.sync_arxiv(
                session,
                through_date=date(2026, 10, 27),
                now=datetime(2026, 10, 27, 2, tzinfo=UTC),
            )
        cursor = session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID)
        assert cursor.committed_through is None
        assert cursor.last_success_at is None
        run = session.scalar(select(DiscoveryRun))
        assert run.status == "failed"
