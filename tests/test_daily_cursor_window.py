from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from frontier import cli
from frontier.database import Base, get_session
from frontier.models import Brief, BriefItem, DiscoveryRun, Paper, TaskRun
from frontier.services.common import ensure_default_settings


@pytest.fixture
def daily_db(client):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def _draft(session, paper_ids):
    run = DiscoveryRun(
        status="completed",
        window_start=datetime(2026, 10, 27, 1, 5, tzinfo=UTC),
        window_end=datetime(2026, 10, 28, 1, 5, tzinfo=UTC),
        query="test",
        result_json={"brief_date": "2026-10-28", "paper_ids": paper_ids},
    )
    task = TaskRun(
        kind="daily_brief",
        status="running",
        payload_json={
            "date": "2026-10-28",
            "discovery_mode": "brief_date_cursor",
        },
    )
    session.add_all([run, task])
    session.flush()
    brief = Brief(brief_date=date(2026, 10, 28), title="Daily", task_id=task.id)
    session.add(brief)
    session.commit()
    return run, task, brief


def test_candidate_pool_is_bound_to_daily_date_and_discovery_batch(client, daily_db):
    fresh = Paper(title="New", normalized_title="new")
    old = Paper(title="Old", normalized_title="old")
    daily_db.add_all([fresh, old])
    daily_db.flush()
    run, task, _ = _draft(daily_db, [fresh.id])
    url = f"/api/tasks/{task.id}/candidate-pool"
    data = {"paper_ids": [fresh.id], "discovery_complete": True, "notes": "One new paper"}
    assert client.put(url, json=data).status_code == 422
    data["discovery_run_id"] = run.id
    assert client.put(url, json={**data, "paper_ids": [old.id]}).status_code == 422
    run.result_json = {**run.result_json, "brief_date": "2026-10-27"}
    daily_db.commit()
    assert client.put(url, json=data).status_code == 422
    run.result_json = {**run.result_json, "brief_date": "2026-10-28"}
    daily_db.commit()
    assert client.put(url, json=data).status_code == 200


@pytest.mark.parametrize(
    "feedback", [{"user_score": 0}, {"user_score": 0.713}, {"user_note": "Already screened"}]
)
def test_rated_candidate_cannot_reenter_pool_or_be_added_automatically(
    client,
    daily_db,
    feedback,
):
    paper = Paper(title="Rated", normalized_title="rated", **feedback)
    daily_db.add(paper)
    daily_db.flush()
    run, task, brief = _draft(daily_db, [paper.id])
    assert (
        client.put(
            f"/api/tasks/{task.id}/candidate-pool",
            json={
                "paper_ids": [paper.id],
                "discovery_run_id": run.id,
                "discovery_complete": True,
                "notes": "One paper",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/briefs/{brief.id}/items",
            json={
                "paper_id": paper.id,
                "rank": 1,
            },
        ).status_code
        == 409
    )


def test_previously_published_paper_is_rejected_at_pool_and_add_time(client, daily_db):
    paper = Paper(title="Seen", normalized_title="seen")
    prior = Brief(brief_date=date(2026, 10, 27), title="Yesterday", status="published")
    daily_db.add_all([paper, prior])
    daily_db.flush()
    daily_db.add(BriefItem(brief_id=prior.id, paper_id=paper.id, rank=1))
    daily_db.commit()
    run, task, brief = _draft(daily_db, [paper.id])
    assert (
        client.put(
            f"/api/tasks/{task.id}/candidate-pool",
            json={
                "paper_ids": [paper.id],
                "discovery_run_id": run.id,
                "discovery_complete": True,
                "notes": "One paper",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/briefs/{brief.id}/items",
            json={
                "paper_id": paper.id,
                "rank": 1,
            },
        ).status_code
        == 409
    )


def test_publish_rechecks_feedback_and_prior_recommendations_and_preserves_history(
    client, daily_db
):
    fresh, rated, duplicate = [Paper(title=n, normalized_title=n) for n in ("new", "rated", "seen")]
    daily_db.add_all([fresh, rated, duplicate])
    daily_db.flush()
    _, _, brief = _draft(daily_db, [fresh.id, rated.id, duplicate.id])
    for rank, paper in enumerate((fresh, rated, duplicate), 1):
        assert (
            client.post(
                f"/api/briefs/{brief.id}/items",
                json={
                    "paper_id": paper.id,
                    "rank": rank,
                },
            ).status_code
            == 200
        )
    rated.user_score = 0.9
    prior = Brief(brief_date=date(2026, 10, 27), title="Yesterday", status="published")
    daily_db.add(prior)
    daily_db.flush()
    daily_db.add(BriefItem(brief_id=prior.id, paper_id=duplicate.id, rank=1))
    daily_db.commit()
    response = client.post(f"/api/briefs/{brief.id}/publish")
    assert response.status_code == 200, response.text
    assert [i["paper"]["id"] for i in response.json()["items"]] == [fresh.id]
    fresh.user_score = 0.5
    daily_db.commit()
    again = client.post(f"/api/briefs/{brief.id}/publish")
    assert [i["paper"]["id"] for i in again.json()["items"]] == [fresh.id]


def test_complete_empty_window_publishes_without_refilling_old_papers(client, daily_db):
    run, task, brief = _draft(daily_db, [])
    assert (
        client.put(
            f"/api/tasks/{task.id}/candidate-pool",
            json={
                "paper_ids": [],
                "discovery_run_id": run.id,
                "discovery_complete": True,
                "notes": "This cursor interval has no new eligible papers",
            },
        ).status_code
        == 200
    )
    response = client.post(f"/api/briefs/{brief.id}/publish")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["items"] == []


def test_arxiv_sync_cli_forwards_brief_date(monkeypatch):
    captured = {}

    def request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {}

    monkeypatch.setattr(cli.client, "request", request)
    cli.source_arxiv_sync("2026-10-27")
    assert captured["params"] == {"through_date": "2026-10-27"}
