from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier.database import Base
from frontier.models import Brief, BriefItem, DiscoveryCursor, DiscoveryItem, DiscoveryRun, Paper
from frontier.schemas import PaperCreate
from frontier.services import arxiv_sync, ingest


@pytest.fixture(autouse=True)
def arxiv_clock(monkeypatch):
    clock = [1000.0]
    waits = []

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(ingest, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ingest, "sleep", wait)
    monkeypatch.setattr(ingest, "_arxiv_last_query_at", 0.0)
    monkeypatch.setattr(ingest, "_arxiv_cooldown_until", 0.0)

    def no_rss(**kwargs):
        raise ValueError("RSS unavailable in API transport tests")

    monkeypatch.setattr(arxiv_sync, "fetch_rss_records", no_rss)
    return clock, waits


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
            "_fetch_arxiv_records",
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
        assert session.scalar(select(func.count(Paper.id))) == 26
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

        monkeypatch.setattr(arxiv_sync, "_fetch_arxiv_records", incomplete)
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
            "_fetch_arxiv_records",
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


def test_oai_fetch_follows_resumption_token_and_filters_categories(
    monkeypatch, arxiv_clock
) -> None:
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
    assert requests[0]["set"] == "cs"
    assert requests[1] == {"verb": "ListRecords", "resumptionToken": "next-page"}
    assert arxiv_clock[1] == [3.0]


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
    monkeypatch.setattr(arxiv_sync, "_fetch_arxiv_records", fetch)
    return calls


def test_brief_date_fixes_cutoff_and_limits_candidates_to_its_source_batch(monkeypatch):
    payloads = [
        _robot("2610.94001", "2026-10-26T12:00:00Z"),
        _robot("2610.94002", "2026-10-23T12:00:00Z"),
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
        assert result["window"]["start"] == "2026-10-26T01:05:00+00:00"
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


def test_atom_fetch_covers_all_recent_pages_and_keeps_old_papers_new_versions(monkeypatch):
    recent = [_robot(f"2610.{95000 + i}", "2026-10-26T12:00:00Z") for i in range(365)]
    recent[0].metadata_json["arxiv_published_at"] = "2020-01-01T00:00:00Z"
    entries = [
        _robot("2610.96000", "2026-10-28T00:00:00Z"),
        *recent,
        _robot("2610.96001", "2026-10-20T00:00:00Z"),
    ]
    calls = []

    def page(query, **kwargs):
        calls.append((query, kwargs))
        offset = kwargs["start"]
        return {
            "items": entries[offset : offset + kwargs["max_results"]],
            "total_results": 10000,
            "start": offset,
        }

    monkeypatch.setattr(arxiv_sync, "search_arxiv_page", page)
    items, stats = arxiv_sync._fetch_atom_records(
        start=datetime(2026, 10, 25, tzinfo=UTC),
        end=datetime(2026, 10, 27, tzinfo=UTC),
        categories=["cs.RO", "cs.AI"],
    )
    assert len(items) == 365
    assert items[0].metadata_json["arxiv_published_at"].startswith("2020")
    assert items[0].metadata_json["metadata_source"] == "atom"
    assert [options["start"] for _, options in calls] == [0, 100, 200, 300]
    assert calls[0][0] == "(cat:cs.RO OR cat:cs.AI)"
    assert all(options["sort_by"] == "lastUpdatedDate" for _, options in calls)
    assert stats["complete"] is True
    assert stats["source_records"] == 367
    assert stats["pages"] == 4


def _fetch_atom():
    return arxiv_sync._fetch_atom_records(
        start=datetime(2026, 10, 25, tzinfo=UTC),
        end=datetime(2026, 10, 27, tzinfo=UTC),
        categories=["cs.RO"],
    )


@pytest.mark.parametrize(
    "pages",
    [
        [{"items": [], "total_results": None, "start": 0}],
        [{"items": [], "total_results": 1, "start": 0}],
        [{"items": [], "total_results": 0, "start": 1}],
        [{"items": [_robot("2610.97001", "invalid")], "total_results": 1, "start": 0}],
        [
            {
                "items": [
                    _robot("2610.97001", "2026-10-26T00:00:00Z"),
                    _robot("2610.97002", "2026-10-26T01:00:00Z"),
                ],
                "total_results": 2,
                "start": 0,
            }
        ],
        [
            {"items": [_robot("2610.97001", "2026-10-26")], "total_results": 2, "start": 0},
            {"items": [_robot("2610.97001", "2026-10-26")], "total_results": 2, "start": 1},
        ],
        [
            {"items": [_robot("2610.97001", "2026-10-26")], "total_results": 2, "start": 0},
            {"items": [_robot("2610.97002", "2026-10-26")], "total_results": 3, "start": 1},
        ],
    ],
)
def test_atom_rejects_unverifiable_coverage(monkeypatch, pages):
    responses = iter(pages)
    monkeypatch.setattr(arxiv_sync, "search_arxiv_page", lambda *a, **k: next(responses))
    with pytest.raises(ValueError):
        _fetch_atom()


def test_atom_empty_complete_window_does_not_call_oai(monkeypatch):
    monkeypatch.setattr(
        arxiv_sync,
        "search_arxiv_page",
        lambda *a, **k: {"items": [], "total_results": 0, "start": 0},
    )

    def unexpected(**kwargs):
        pytest.fail("A complete Atom window should not start the slow OAI request")

    monkeypatch.setattr(arxiv_sync, "_fetch_oai_records", unexpected)
    items, stats = arxiv_sync._fetch_arxiv_records(
        start=datetime(2026, 10, 25, tzinfo=UTC),
        end=datetime(2026, 10, 27, tzinfo=UTC),
        categories=["cs.RO"],
    )
    assert items == []
    assert stats["fallback_used"] is True
    assert stats["source"] == "atom"


def test_partial_atom_failure_restarts_the_whole_window_using_oai(monkeypatch):
    calls = []

    def page(query, **kwargs):
        if kwargs["start"]:
            raise httpx.ReadTimeout("Atom second page timed out")
        return {
            "items": [_robot("2610.97001", "2026-10-26")],
            "total_results": 2,
            "start": 0,
        }

    def oai(**kwargs):
        calls.append(kwargs)
        return [_robot("2610.97002", "2026-10-26")], {"source": "oai-pmh", "complete": True}

    monkeypatch.setattr(arxiv_sync, "search_arxiv_page", page)
    monkeypatch.setattr(arxiv_sync, "_fetch_oai_records", oai)
    params = {
        "start": datetime(2026, 10, 25, tzinfo=UTC),
        "end": datetime(2026, 10, 27, tzinfo=UTC),
        "categories": ["cs.RO"],
    }
    items, stats = arxiv_sync._fetch_arxiv_records(**params)
    assert [item.arxiv_id for item in items] == ["2610.97002"]
    assert calls == [params]
    assert stats["fallback_used"] is True
    assert "second page" in stats["atom_error"]


def test_both_sources_failing_preserves_database_cursor_and_records_both_errors(monkeypatch):
    fetch = arxiv_sync._fetch_arxiv_records
    _mock_batch(monkeypatch, [])
    monkeypatch.setattr(arxiv_sync, "_fetch_arxiv_records", fetch)

    def atom(**kwargs):
        raise httpx.ReadTimeout("Atom unavailable")

    def oai(**kwargs):
        raise httpx.ReadTimeout("OAI unavailable")

    monkeypatch.setattr(arxiv_sync, "_fetch_atom_records", atom)
    monkeypatch.setattr(arxiv_sync, "_fetch_oai_records", oai)
    with _session() as session:
        prior = datetime(2026, 10, 26, 1, 5, tzinfo=UTC)
        session.add(DiscoveryCursor(id=arxiv_sync.SYNC_CURSOR_ID, committed_through=prior))
        session.commit()
        with pytest.raises(RuntimeError, match="Atom unavailable; OAI-PMH: OAI unavailable"):
            arxiv_sync.sync_arxiv(
                session,
                through_date=date(2026, 10, 27),
                now=datetime(2026, 10, 27, 2, tzinfo=UTC),
            )
        cursor = session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID)
        assert cursor.committed_through.replace(tzinfo=UTC) == prior
        run = session.scalar(select(DiscoveryRun))
        assert run.status == "failed"
        assert "Atom unavailable" in run.error and "OAI unavailable" in run.error
        assert session.scalar(select(func.count(DiscoveryItem.id))) == 0
        assert session.scalar(select(func.count(Paper.id))) == 0


def test_rss_finds_late_papers_without_recommending_existing_ones(monkeypatch):
    late = _robot("2610.97003", "2026-10-25T12:00:00Z")
    late.metadata_json["arxiv_announced_at"] = "2026-10-27T00:00:00Z"
    calls = _mock_batch(monkeypatch, [late])
    with _session() as session:
        session.add(
            DiscoveryCursor(
                id=arxiv_sync.SYNC_CURSOR_ID,
                committed_through=datetime(2026, 10, 26, 1, 5, tzinfo=UTC),
            )
        )
        session.commit()
        first = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 27),
            now=datetime(2026, 10, 27, 2, tzinfo=UTC),
        )
        assert len(first["paper_ids"]) == 1
        assert calls[0]["start"] == datetime(2026, 10, 26, 1, 5, tzinfo=UTC)
        second = arxiv_sync.sync_arxiv(
            session,
            through_date=date(2026, 10, 28),
            now=datetime(2026, 10, 28, 2, tzinfo=UTC),
        )
        assert second["paper_ids"] == []
        assert second["counts"]["existing"] == 1
        assert session.scalar(select(func.count(Paper.id))) == 1


def test_atom_transport_failure_falls_back_quickly_and_shares_oai_rate_limit(
    monkeypatch, arxiv_clock
):
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        if url == ingest.ARXIV_API:
            assert kwargs["timeout"] <= 15
            raise httpx.ReadTimeout("Atom network failure")
        return httpx.Response(
            200,
            text='<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            '<error code="noRecordsMatch">No matches</error></OAI-PMH>',
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ingest.httpx, "get", get)
    items, stats = arxiv_sync._fetch_arxiv_records(
        start=datetime(2026, 10, 25, tzinfo=UTC),
        end=datetime(2026, 10, 27, tzinfo=UTC),
        categories=["cs.RO"],
    )
    assert items == []
    assert urls == [ingest.ARXIV_API, ingest.ARXIV_API, arxiv_sync.OAI_ENDPOINT]
    assert arxiv_clock[1] == [3.0, 6.0]
    assert stats["source"] == "oai-pmh"
    assert stats["fallback_used"] is True


@pytest.mark.parametrize(
    "body",
    [
        "<html>proxy error</html>",
        '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords/></OAI-PMH>',
    ],
)
def test_oai_does_not_mistake_invalid_or_empty_pages_for_success(monkeypatch, body):
    monkeypatch.setattr(
        ingest.httpx,
        "get",
        lambda url, **kwargs: httpx.Response(200, text=body, request=httpx.Request("GET", url)),
    )
    with pytest.raises(ValueError):
        arxiv_sync._fetch_oai_records(
            start=datetime(2026, 10, 25, tzinfo=UTC),
            end=datetime(2026, 10, 27, tzinfo=UTC),
            categories=["cs.RO"],
        )


def test_committed_cursor_uses_article_date_and_empty_run_does_not_advance_it(monkeypatch):
    payload = _robot("2611.08576", "2026-11-05T18:44:00Z")
    _mock_batch(monkeypatch, [payload])
    with _session() as session:
        first = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 6), now=datetime(2026, 11, 6, 12, tzinfo=UTC)
        )
        assert first["cursor"]["committed_through"] == "2026-11-05T18:44:00+00:00"
        assert first["cursor"]["basis"] == arxiv_sync.CURSOR_BASIS
        _mock_batch(monkeypatch, [])
        empty = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 7), now=datetime(2026, 11, 7, 12, tzinfo=UTC)
        )
        assert empty["cursor"]["committed_through"] == first["cursor"]["committed_through"]


def test_legacy_runtime_cursor_is_rebuilt_from_stored_article_dates(monkeypatch):
    _mock_batch(monkeypatch, [])
    with _session() as session:
        session.add(
            DiscoveryCursor(
                id="arxiv-last-updated-v1", committed_through=datetime(2026, 11, 9, 2, tzinfo=UTC)
            )
        )
        run = DiscoveryRun(
            status="completed",
            window_start=datetime(2026, 11, 5, tzinfo=UTC),
            window_end=datetime(2026, 11, 9, tzinfo=UTC),
            query="old clock",
        )
        session.add(run)
        session.flush()
        session.add(
            DiscoveryItem(
                run_id=run.id,
                title="Stored article",
                arxiv_id="2611.08576",
                status="fetched",
                updated_at=datetime(2026, 11, 5, 18, 44, tzinfo=UTC),
            )
        )
        session.commit()
        result = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
        )
        assert result["cursor"]["previous"] == "2026-11-05T18:44:00+00:00"
        assert result["cursor"]["committed_through"] == result["cursor"]["previous"]
        assert session.get(DiscoveryCursor, "arxiv-last-updated-v1") is None


def test_rss_late_article_older_than_cursor_is_saved_even_after_known_id(monkeypatch):
    known = _robot("2611.10000", "2026-11-06T01:00:00Z")
    late = _robot("2611.08576", "2026-11-05T18:44:00Z")
    for item in (known, late):
        item.arxiv_version = 1
        item.metadata_json["arxiv_announced_at"] = "2026-11-09T05:00:00Z"
    _mock_batch(monkeypatch, [known, late])
    with _session() as session:
        session.add(
            DiscoveryCursor(
                id=arxiv_sync.SYNC_CURSOR_ID, committed_through=datetime(2026, 11, 7, tzinfo=UTC)
            )
        )
        session.add(
            Paper(
                title=known.title,
                normalized_title=known.title.lower(),
                arxiv_id=known.arxiv_id,
                arxiv_version=1,
            )
        )
        session.commit()
        result = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
        )
        assert len(result["paper_ids"]) == 1
        assert session.get(Paper, result["paper_ids"][0]).arxiv_id == late.arxiv_id
        assert result["cursor"]["committed_through"] == "2026-11-07T00:00:00+00:00"


def test_old_id_new_version_updates_library_without_overwriting_feedback(monkeypatch):
    update = _robot("2501.12345", "2026-11-09T12:00:00Z")
    update.arxiv_version = 2
    update.abstract = "Revised abstract"
    _mock_batch(monkeypatch, [update])
    with _session() as session:
        existing = Paper(
            title="Original title",
            normalized_title="original title",
            arxiv_id="2501.12345",
            arxiv_version=1,
            user_score=0.75,
            user_note="My note",
            pre_read_summary="Intro",
            post_read_summary="Read summary",
            primary_affiliation="Verified institution",
            pdf_path="/cached/original-v1.pdf",
        )
        session.add(existing)
        session.commit()
        result = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
        )
        assert existing.arxiv_version == 2
        assert existing.abstract == "Revised abstract"
        assert existing.user_score == 0.75 and existing.user_note == "My note"
        assert existing.pre_read_summary == "Intro" and existing.post_read_summary == "Read summary"
        assert existing.primary_affiliation == "Verified institution"
        assert existing.pdf_path is None
        assert result["counts"]["updated_existing"] == 1
        assert result["paper_ids"] == []


def test_failed_ingestion_rolls_back_papers_and_cursor_as_one_transaction(monkeypatch):
    _mock_batch(
        monkeypatch,
        [
            _robot("2611.10001", "2026-11-09T12:00:00Z"),
            _robot("2611.10002", "2026-11-09T13:00:00Z"),
        ],
    )
    original = arxiv_sync.create_or_update_paper
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("second insert failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(arxiv_sync, "create_or_update_paper", interrupted)
    with _session() as session:
        with pytest.raises(RuntimeError, match="second insert failed"):
            arxiv_sync.sync_arxiv(
                session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
            )
        assert session.scalar(select(func.count(Paper.id))) == 0
        assert session.scalar(select(func.count(DiscoveryItem.id))) == 0
        assert session.get(DiscoveryCursor, arxiv_sync.SYNC_CURSOR_ID).committed_through is None


def test_atom_does_not_stop_before_all_equal_timestamp_records_are_read(monkeypatch):
    responses = iter(
        [
            {
                "items": [_robot("2611.10001", "2026-11-09T00:00:00Z")],
                "start": 0,
                "total_results": 3,
            },
            {
                "items": [_robot("2611.10002", "2026-11-09T00:00:00Z")],
                "start": 1,
                "total_results": 3,
            },
            {
                "items": [_robot("2611.10003", "2026-11-08T23:00:00Z")],
                "start": 2,
                "total_results": 3,
            },
        ]
    )
    monkeypatch.setattr(arxiv_sync, "search_arxiv_page", lambda *a, **k: next(responses))
    items, stats = arxiv_sync._fetch_atom_records(
        start=datetime(2026, 11, 9, tzinfo=UTC),
        end=datetime(2026, 11, 10, tzinfo=UTC),
        categories=["cs.RO"],
    )
    assert [item.arxiv_id for item in items] == ["2611.10001", "2611.10002"]
    assert stats["pages"] == 3


def test_rss_metadata_failure_ingests_without_falsely_advancing_cursor(monkeypatch):
    fetch = arxiv_sync._fetch_arxiv_records
    _mock_batch(monkeypatch, [])
    monkeypatch.setattr(arxiv_sync, "_fetch_arxiv_records", fetch)
    late = _robot("2611.08576", "not available")
    late.arxiv_version = 1
    late.metadata_json.update(
        {
            "arxiv_announced_at": "2026-11-09T05:00:00Z",
            "arxiv_timestamp_pending": True,
        }
    )
    monkeypatch.setattr(
        arxiv_sync,
        "fetch_rss_records",
        lambda **k: (
            [late],
            {
                "source": "rss",
                "complete": True,
                "metadata_error": "API down",
                "cursor_can_advance": False,
            },
        ),
    )

    def no_api(**kwargs):
        pytest.fail("Do not retry API scanning after RSS enrichment has established an outage")

    monkeypatch.setattr(arxiv_sync, "_fetch_atom_records", no_api)
    with _session() as session:
        result = arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
        )
        assert len(result["paper_ids"]) == 1
        assert result["cursor"]["committed_through"] is None
        assert result["coverage_complete"] is False
        assert session.scalar(select(func.count(Paper.id))) == 1


def test_rss_new_version_never_reuses_previous_versions_timestamp(monkeypatch):
    update = _robot("2501.12345", "")
    update.arxiv_version = 2
    update.metadata_json.update(
        {
            "arxiv_announced_at": "2026-11-09T05:00:00Z",
            "arxiv_timestamp_pending": True,
        }
    )
    _mock_batch(monkeypatch, [update])
    with _session() as session:
        paper = Paper(
            title="Existing",
            normalized_title="existing",
            arxiv_id="2501.12345",
            arxiv_version=1,
            metadata_json={"arxiv_updated_at": "2025-01-01T00:00:00Z"},
        )
        session.add(paper)
        session.commit()
        arxiv_sync.sync_arxiv(
            session, through_date=date(2026, 11, 10), now=datetime(2026, 11, 10, 12, tzinfo=UTC)
        )
        assert paper.arxiv_version == 2
        assert paper.metadata_json["arxiv_updated_at"] is None
        assert paper.metadata_json["arxiv_timestamp_pending"] is True
