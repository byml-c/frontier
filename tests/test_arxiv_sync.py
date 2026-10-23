from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier.database import Base
from frontier.models import DiscoveryCursor, DiscoveryItem, DiscoveryRun, Paper
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
                screening_feedback={"interest_level": "interested"},
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
                screening_feedback={"interest_level": "ignored"},
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
        monkeypatch.setattr(arxiv_sync, "queue_metadata_enrichment", lambda *_args, **_kwargs: None)
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
                metadata_json={"categories": ["cs.DB"]},
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
