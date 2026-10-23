from __future__ import annotations

from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier import cli
from frontier.agent_runner import build_prompt
from frontier.database import Base, get_session
from frontier.models import AppSetting, Brief, OperationLog, Paper, TaskRun
from frontier.schemas import PaperCreate
from frontier.services import discovery, ingest
from frontier.services.common import (
    BROAD_DISCOVERY_DOMAINS,
    BROAD_DISCOVERY_MIGRATION_ACTION,
    BROAD_DISCOVERY_WINDOW_MIGRATION_ACTION,
    ensure_default_settings,
)


@pytest.fixture
def discovery_db(client):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        ensure_default_settings(session)
        client.app.dependency_overrides[get_session] = lambda: session
        try:
            yield session
        finally:
            client.app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def test_broad_recall_pages_beyond_100_without_keywords_or_ingestion(
    client, discovery_db, monkeypatch
):
    existing = Paper(
        title="Existing", normalized_title="existing", arxiv_id="2610.00100", status="reading"
    )
    discovery_db.add(existing)
    discovery_db.commit()
    calls = []

    def page(query, *, start, max_results):
        calls.append((query, start, max_results))
        return {
            "total_results": 101,
            "start": start,
            "items": [
                PaperCreate(
                    title=f"Robot paper {i}",
                    abstract="A robot vision agent for embodied manipulation.",
                    arxiv_id=f"2610.{i:05d}",
                )
                for i in range(start, min(start + max_results, 101))
            ],
        }

    monkeypatch.setattr(discovery, "search_arxiv_page", page)
    first = client.get("/api/sources/arxiv/discover", params={"through_date": "2026-10-10"}).json()
    assert first["next_offset"] is None
    assert first["total_results"] == 101
    assert first["candidate_target"] == 100
    assert first["since_date"] == "2026-10-08"
    assert first["domains"] == BROAD_DISCOVERY_DOMAINS
    assert first["raw_scanned"] == 101
    assert first["prefiltered_count"] == 100
    assert first["skipped_existing_count"] == 1
    assert all(item["existing_paper_id"] != existing.id for item in first["items"])
    assert calls[0][0] == calls[1][0]
    assert "cat:cs.CV" in calls[0][0] and "cat:cs.MA" in calls[0][0]
    assert "submittedDate:[202610080000 TO 202610102359]" in calls[0][0]
    assert "VLA" not in calls[0][0] and "all:" not in calls[0][0]
    assert discovery_db.scalar(select(func.count(Paper.id))) == 1
    assert (
        discovery_db.scalar(
            select(func.count(OperationLog.id)).where(
                OperationLog.action == "discovery.page_fetched"
            )
        )
        == 1
    )


def test_discovery_requires_verifiable_coverage(client, discovery_db, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "search_arxiv_page",
        lambda *_args, **_kwargs: {"items": [], "total_results": None},
    )
    response = client.get("/api/sources/arxiv/discover")
    assert response.status_code == 502
    assert "coverage" in response.json()["detail"]


def test_discovery_expands_window_for_monday_tuesday_announcement_lag(
    client, discovery_db, monkeypatch
):
    calls = []

    def page(query, *, start, max_results):
        calls.append(query)
        return {
            "items": [
                PaperCreate(
                    title="Delayed embodied robot paper",
                    abstract="A robot agent learns manipulation from 3D vision.",
                    arxiv_id="2610.99999",
                )
            ],
            "total_results": 1,
            "start": start,
        }

    monkeypatch.setattr(discovery, "search_arxiv_page", page)
    response = client.get("/api/sources/arxiv/discover", params={"through_date": "2026-10-13"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["fallback_applied"] is True
    assert result["requested_lookback_days"] == 3
    assert result["effective_lookback_days"] == 7
    assert result["since_date"] == "2026-10-07"
    assert result["count"] == 1
    assert len(calls) == 1
    assert "submittedDate:[202610070000 TO 202610132359]" in calls[0]


@pytest.mark.parametrize(
    "status", ["candidate", "reading", "feedback_pending", "completed", "ignored"]
)
def test_discovery_excludes_every_library_status_and_normalized_identity(
    client, discovery_db, monkeypatch, status
):
    existing = [
        Paper(
            title="Library robot",
            normalized_title="library robot",
            arxiv_id="2610.00001",
            status=status,
        ),
        Paper(
            title="Other library robot",
            normalized_title="other library robot",
            doi="10.1234/ABC",
            status=status,
        ),
        Paper(title="Robot: New Method!", normalized_title="robot new method", status=status),
    ]
    discovery_db.add_all(existing)
    discovery_db.commit()
    entries = [
        PaperCreate(title="Renamed robot", arxiv_id="2610.00001v3"),
        PaperCreate(title="Renamed DOI robot", doi="https://doi.org/10.1234/abc"),
        PaperCreate(title="ROBOT new method", arxiv_id="2610.00003"),
        PaperCreate(title="New robot navigation", arxiv_id="2610.00004"),
        PaperCreate(title="New robot navigation", arxiv_id="2610.00005"),
        PaperCreate(title="Updated robot navigation", arxiv_id="2610.00004v2"),
        PaperCreate(title="Memory allocation", abstract="Supervision of reagent processing."),
    ]
    monkeypatch.setattr(
        discovery,
        "search_arxiv_page",
        lambda *_args, **_kwargs: {"items": entries, "total_results": len(entries)},
    )
    result = client.get("/api/sources/arxiv/discover").json()
    assert [item["arxiv_id"] for item in result["items"]] == ["2610.00004"]
    assert result["skipped_existing_count"] == 3
    assert result["duplicate_count"] == 2
    assert result["coarse_rejected_count"] == 1
    assert discovery_db.scalar(select(func.count(Paper.id))) == 3


def test_prefilter_bounds_raw_scan_and_agent_input_separately(client, discovery_db, monkeypatch):
    discovery_db.get(AppSetting, "discovery_scan_limit").value_json = 250
    discovery_db.commit()
    calls = []

    def page(_query, *, start, max_results):
        calls.append((start, max_results))
        return {
            "total_results": 500,
            "items": [
                PaperCreate(title=f"Robot work {i}", arxiv_id=f"2610.{i:05d}")
                for i in range(start, start + max_results)
            ],
        }

    monkeypatch.setattr(discovery, "search_arxiv_page", page)
    result = client.get("/api/sources/arxiv/discover").json()
    assert calls == [(0, 100), (100, 100), (200, 50)]
    assert result["raw_scanned"] == 250
    assert result["coarse_match_count"] == 250
    assert result["count"] == 120
    assert result["scan_capped"] and result["prefilter_capped"]
    assert result["scan_complete"] and not result["coverage_complete"]
    assert result["next_offset"] is None
    assert result["items"][0]["title"] == "Robot work 0"
    assert discovery_db.scalar(select(func.count(Paper.id))) == 0


@pytest.mark.parametrize(
    "title, abstract, categories, expected",
    [
        ("Gaussian splatting", None, ["cs.CV"], "3D vision"),
        ("A new control method", "", ["cs.RO"], "embodied intelligence"),
        ("Coordination", "", ["cs.MA"], "AI agents"),
        ("Long-term memory for language models", "", ["cs.CL"], "AI agents"),
        ("Memory-efficient sorting", "Reagent supervision", ["cs.SE"], None),
        ("Financial planning", "Dynamic memory allocation", ["cs.AI"], None),
    ],
)
def test_coarse_filter_uses_broad_concepts_without_substring_false_positives(
    title, abstract, categories, expected
):
    score, domains = discovery.coarse_domain_match(title, abstract, categories)
    if expected:
        assert expected in domains and score > 0
    else:
        assert score == 0 and domains == []


def test_internal_paging_failure_does_not_report_empty_success(client, discovery_db, monkeypatch):
    def page(_query, *, start, max_results):
        if start:
            raise RuntimeError("second page unavailable")
        return {
            "total_results": 200,
            "items": [PaperCreate(title=f"Robot {i}") for i in range(100)],
        }

    monkeypatch.setattr(discovery, "search_arxiv_page", page)
    response = client.get("/api/sources/arxiv/discover")
    assert response.status_code == 502
    assert "second page unavailable" in response.json()["detail"]
    assert discovery_db.scalar(select(func.count(Paper.id))) == 0


def atom(total=2, offset=1, entry=True):
    item = (
        """<entry><id>http://arxiv.org/abs/2610.12345v1</id><title>New paper</title>
      <summary>A paper without VLA or WAM keyword constraints.</summary>
      <published>2026-10-09T00:00:00Z</published><author><name>Alice</name></author>
      <category term="cs.AI"/></entry>"""
        if entry
        else ""
    )
    return f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>{total}</opensearch:totalResults>
      <opensearch:startIndex>{offset}</opensearch:startIndex>{item}</feed>"""


def test_arxiv_transport_keeps_offset_total_and_rate_limit(monkeypatch):
    seen = []
    slept = []
    monkeypatch.setattr(ingest, "_arxiv_last_query_at", 99.0)
    monkeypatch.setattr(ingest, "monotonic", lambda: 100.0)
    monkeypatch.setattr(ingest, "sleep", slept.append)

    def get(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(200, text=atom(), request=httpx.Request("GET", url))

    monkeypatch.setattr(ingest.httpx, "get", get)
    result = ingest.search_arxiv_page("cat:cs.AI", start=1, max_results=1)
    assert seen[0]["start"] == 1
    assert result["total_results"] == 2
    assert result["items"][0].arxiv_id == "2610.12345"
    assert slept == [2.0]


@pytest.fixture
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
    return clock, waits


def test_arxiv_retries_429_and_honors_retry_after(monkeypatch, arxiv_clock):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        assert ingest._ARXIV_QUERY_LOCK.locked()
        return httpx.Response(
            429 if len(calls) == 1 else 200,
            headers={"Retry-After": "90"},
            text=atom(),
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(ingest.httpx, "get", get)
    assert ingest.search_arxiv_page("cat:cs.AI", start=1)["total_results"] == 2
    assert len(calls) == 2
    assert arxiv_clock[1] == [90.0]
    assert calls[0]["params"] == calls[1]["params"]


def test_arxiv_stops_retrying_at_ten_minutes(monkeypatch, arxiv_clock):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(429, request=httpx.Request("GET", url))

    monkeypatch.setattr(ingest.httpx, "get", get)
    with pytest.raises(httpx.TimeoutException, match="ten-minute"):
        ingest.search_arxiv_page("cat:cs.AI")
    assert len(calls) == 4
    assert arxiv_clock[0][0] == 1600.0
    assert arxiv_clock[1] == [60.0, 120.0, 240.0, 180.0]


def test_arxiv_budget_is_shared_across_pages(monkeypatch, arxiv_clock):
    def get(url, **kwargs):
        if kwargs["params"]["start"] == 0:
            arxiv_clock[0][0] += 590
            return httpx.Response(200, text=atom(offset=0), request=httpx.Request("GET", url))
        return httpx.Response(429, request=httpx.Request("GET", url))

    monkeypatch.setattr(ingest.httpx, "get", get)
    with ingest.arxiv_request_budget():
        ingest.search_arxiv_page("cat:cs.AI", start=0)
        with pytest.raises(httpx.TimeoutException):
            ingest.search_arxiv_page("cat:cs.AI", start=1)
    assert arxiv_clock[0][0] == 1600.0


def test_arxiv_retries_transport_failure(monkeypatch, arxiv_clock):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary failure")
        return httpx.Response(200, text=atom(), request=httpx.Request("GET", url))

    monkeypatch.setattr(ingest.httpx, "get", get)
    ingest.search_arxiv_page("cat:cs.AI", start=1)
    assert arxiv_clock[1] == [60.0]


def test_arxiv_does_not_retry_permanent_errors(monkeypatch, arxiv_clock):
    monkeypatch.setattr(
        ingest.httpx,
        "get",
        lambda url, **kwargs: httpx.Response(400, request=httpx.Request("GET", url)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        ingest.search_arxiv_page("invalid")
    assert arxiv_clock[1] == []


@pytest.mark.parametrize("body", [atom(entry=False), atom(offset=0), "<html>error</html>"])
def test_arxiv_transport_does_not_silently_skip_broken_pages(monkeypatch, body):
    monkeypatch.setattr(ingest, "_arxiv_last_query_at", 0.0)
    monkeypatch.setattr(
        ingest.httpx,
        "get",
        lambda url, **_kwargs: httpx.Response(200, text=body, request=httpx.Request("GET", url)),
    )
    # HTML may parse, but must not be mistaken for a completed, empty discovery.
    try:
        page = ingest.search_arxiv_page("cat:cs.AI", start=1)
    except ValueError:
        return
    assert page["total_results"] is None


def test_candidate_pool_requires_unique_unread_papers_and_explanations(client, discovery_db):
    task = TaskRun(kind="daily_brief", status="running", payload_json={"date": "2026-10-10"})
    paper = Paper(title="Candidate", normalized_title="candidate")
    other = Paper(title="Outside pool", normalized_title="outside pool")
    discovery_db.add_all([task, paper, other])
    discovery_db.commit()
    url = f"/api/tasks/{task.id}/candidate-pool"
    data = {"paper_ids": [paper.id], "discovery_complete": True}
    assert client.put(url, json=data).status_code == 422
    data["notes"] = "领域筛选后只有一篇合格文章"
    assert client.put(url, json={**data, "paper_ids": [paper.id, paper.id]}).status_code == 422
    assert client.put(url, json={**data, "paper_ids": ["missing"]}).status_code == 422
    assert client.put(url, json=data).status_code == 200
    assert task.payload_json["candidate_pool"]["paper_ids"] == [paper.id]
    brief = Brief(brief_date=date(2026, 10, 10), title="Daily", task_id=task.id)
    discovery_db.add(brief)
    discovery_db.commit()
    assert (
        client.post(
            f"/api/briefs/{brief.id}/items",
            json={"paper_id": other.id, "rank": 1, "tier": "related"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/briefs/{brief.id}/items",
            json={"paper_id": paper.id, "rank": 1, "tier": "related"},
        ).status_code
        == 200
    )
    paper.status = "reading"
    discovery_db.commit()
    assert client.put(url, json=data).status_code == 422
    task.status = "completed"
    discovery_db.commit()
    assert client.put(url, json=data).status_code == 409


def test_candidate_pool_has_no_artificial_target_cap(client, discovery_db):
    discovery_db.get(AppSetting, "abstract_limit").value_json = 5
    task = TaskRun(kind="daily_brief", status="running", payload_json={})
    papers = [Paper(title=str(i), normalized_title=str(i)) for i in range(7)]
    discovery_db.add_all([task, *papers])
    discovery_db.commit()
    data = {"paper_ids": [p.id for p in papers[:6]], "discovery_complete": True}
    assert client.put(f"/api/tasks/{task.id}/candidate-pool", json=data).status_code == 200
    data["paper_ids"] = [p.id for p in papers]
    assert client.put(f"/api/tasks/{task.id}/candidate-pool", json=data).status_code == 200


@pytest.mark.parametrize("old_limit, expected", [(50, 100), (27, 27)])
def test_discovery_settings_migrate_once_and_preserve_interests(old_limit, expected):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                AppSetting(key="abstract_limit", value_json=old_limit),
                AppSetting(key="candidate_lookback_days", value_json=14),
                AppSetting(key="discovery_scan_limit", value_json=1000),
                AppSetting(key="initial_interest_domains", value_json=["VLA", "WAM", "custom"]),
                AppSetting(key="discovery_domains", value_json=["custom broad domain"]),
            ]
        )
        session.commit()
        ensure_default_settings(session)
        assert session.get(AppSetting, "abstract_limit").value_json == expected
        assert session.get(AppSetting, "initial_interest_domains").value_json == [
            "VLA",
            "WAM",
            "custom",
        ]
        assert session.get(AppSetting, "discovery_domains").value_json == ["custom broad domain"]
        assert session.get(AppSetting, "candidate_lookback_days").value_json == 3
        assert session.get(AppSetting, "discovery_scan_limit").value_json == 300
        session.get(AppSetting, "abstract_limit").value_json = 50
        session.commit()
        ensure_default_settings(session)
        assert session.get(AppSetting, "abstract_limit").value_json == 50
        assert (
            session.scalar(
                select(func.count(OperationLog.id)).where(
                    OperationLog.action == BROAD_DISCOVERY_MIGRATION_ACTION
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count(OperationLog.id)).where(
                    OperationLog.action == BROAD_DISCOVERY_WINDOW_MIGRATION_ACTION
                )
            )
            == 1
        )
    engine.dispose()


def test_discovery_settings_validation(client, discovery_db):
    for value in [[], [""], ["cs.CV", "cs.CV"], ["cs.CV OR all:VLA"]]:
        assert (
            client.put(
                "/api/settings/discovery_arxiv_categories", json={"value": value}
            ).status_code
            == 422
        )
    assert client.put("/api/settings/abstract_limit", json={"value": 100}).status_code == 200


def test_cli_exposes_discovery_and_pool(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli.client, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    )
    cli.source_arxiv_discover(date="2026-10-10", offset=100, page_size=50)
    cli.task_candidate_pool(
        "task", data='{"paper_ids":[],"discovery_complete":false,"notes":"source unavailable"}'
    )
    assert calls[0][0] == ("GET", "/api/sources/arxiv/discover")
    assert calls[0][1]["params"] == {"through_date": "2026-10-10", "offset": 100, "page_size": 50}
    assert calls[0][1]["timeout"] == 660
    assert calls[1][0] == ("PUT", "/api/tasks/task/candidate-pool")


def test_cli_arxiv_search_allows_retry_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli.client, "request", lambda *args, **kwargs: calls.append(kwargs) or {}
    )
    cli.source_arxiv_search("cat:cs.RO", max_results=1, ingest=False)
    assert calls[0]["timeout"] == 660


def test_daily_prompt_separates_recall_pool_and_brief():
    prompt = " ".join(
        build_prompt(
            TaskRun(id="daily", kind="daily_brief", payload_json={"date": "2026-10-10"})
        ).split()
    )
    assert "frontier source arxiv-sync" in prompt
    assert "There is no raw scan or prefilter numeric cap" in prompt
    assert "agent_review_paper_ids" in prompt
    assert "continuous value in `[0,1]`" in prompt
    assert "frontier task candidate-pool daily" in prompt
    assert "Choose the final brief only from this saved pool" in prompt
    assert "Assign only a five-level personal-relevance rating" not in prompt
