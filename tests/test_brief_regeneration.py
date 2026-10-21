from __future__ import annotations

from datetime import UTC, date, datetime

from frontier.api import briefs as briefs_api
from frontier.config import settings
from frontier.database import SessionLocal
from frontier.models import Brief, BriefItem, Paper, PodcastEpisode, TaskRun
from frontier.services.podcast_content import episode_payload


def test_void_and_regenerate_completed_daily_brief_keeps_audit_and_voids_audio(
    client, monkeypatch
) -> None:
    target_date = date(2041, 2, 3)
    with SessionLocal() as session:
        paper = Paper(
            title="Published daily paper",
            normalized_title="published daily paper",
            source="manual",
        )
        task = TaskRun(
            kind="daily_brief",
            status="completed",
            payload_json={"date": target_date.isoformat()},
        )
        interrupted = TaskRun(
            kind="daily_brief",
            status="failed",
            payload_json={
                "date": target_date.isoformat(),
                "candidate_pool": {"paper_ids": ["earlier-candidate"]},
            },
        )
        session.add_all([paper, task, interrupted])
        session.flush()
        task.payload_json = {
            **task.payload_json,
            "candidate_pool": {"paper_ids": [paper.id]},
        }
        interrupted_id = interrupted.id
        brief = Brief(
            brief_date=target_date,
            title="Published daily brief",
            status="published",
            published_at=datetime.now(UTC),
            task_id=task.id,
            snapshot_json={"paper_ids": [paper.id]},
        )
        session.add(brief)
        session.flush()
        session.add(
            BriefItem(brief_id=brief.id, paper_id=paper.id, rank=1, tier="featured")
        )
        episode = PodcastEpisode(
            brief_id=brief.id,
            language="zh-CN",
            status="ready",
            source_digest="sha256:old",
            audio_path="podcasts/old.wav",
            published_at=datetime.now(UTC),
        )
        session.add(episode)
        session.commit()
        brief_id = brief.id
        old_task_id = task.id
        episode_id = episode.id

    monkeypatch.setattr(settings, "enable_codex_runs", True)
    submitted: list[str] = []
    monkeypatch.setattr(briefs_api, "submit_task", submitted.append)

    response = client.post(f"/api/briefs/{brief_id}/void-and-regenerate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["brief_id"] == brief_id
    assert payload["status"] == "queued"
    assert payload["id"] != old_task_id
    assert submitted == [payload["id"]]

    with SessionLocal() as session:
        brief = session.get(Brief, brief_id)
        assert brief is not None
        assert brief.status == "draft"
        assert brief.task_id == payload["id"]
        assert brief.published_at is None
        assert not session.query(BriefItem).filter_by(brief_id=brief_id).count()
        episode = session.get(PodcastEpisode, episode_id)
        assert episode is not None
        assert episode.status == "voided"
        assert episode.audio_path is None
        assert episode_payload(session, brief_id)["audio_url"] is None
        replacement = session.get(TaskRun, payload["id"])
        assert replacement is not None and replacement.kind == "daily_brief"
        assert replacement.payload_json["date"] == target_date.isoformat()
        assert set(replacement.payload_json["recovery_source_task_ids"]) == {
            old_task_id,
            interrupted_id,
        }


def test_fresh_daily_run_recovers_same_date_interrupted_pools(client, monkeypatch) -> None:
    from frontier.agent_runner import build_prompt
    from frontier.api import system

    target = date(2041, 2, 4)
    with SessionLocal() as session:
        sources = [
            TaskRun(
                kind="daily_brief",
                status=status,
                payload_json={
                    "date": target.isoformat(),
                    "candidate_pool": {"paper_ids": [f"candidate-{status}"]},
                },
            )
            for status in ("failed", "cancelled")
        ]
        other_day = TaskRun(
            kind="daily_brief",
            status="failed",
            payload_json={
                "date": "2041-02-02",
                "candidate_pool": {"paper_ids": ["unrelated-candidate"]},
            },
        )
        session.add_all([*sources, other_day])
        session.commit()
        source_ids = {task.id for task in sources}
        other_id = other_day.id

    monkeypatch.setattr(settings, "enable_codex_runs", True)
    submitted = []
    monkeypatch.setattr(system, "submit_task", submitted.append)
    response = client.post("/api/tasks/run-daily", params={"target_date": target.isoformat()})
    assert response.status_code == 200
    task_id = response.json()["id"]
    assert submitted == [task_id]
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert set(task.payload_json["recovery_source_task_ids"]) == source_ids
        prompt = build_prompt(task)
        assert all(source_id in prompt for source_id in source_ids)
        assert other_id not in prompt
        assert "Union and deduplicate their saved candidate_pool.paper_ids" in prompt
        assert "Do NOT run arxiv-discover again" in prompt
        assert "retain only status=candidate papers" in prompt


def test_recover_failed_draft_rebuilds_ranking_and_preserves_papers_and_manual_items(
    client, monkeypatch
) -> None:
    from sqlalchemy import select

    from frontier.models import OperationLog

    target = date(2001, 2, 5)
    with SessionLocal() as session:
        paper = Paper(
            title="Scored candidate",
            normalized_title="scored candidate",
            source="manual",
            catalog_summary="已有摘要级总结",
            research_score=0.8,
            relevance_score=0.9,
            screening_feedback={"comment": "用户已有筛选意见"},
        )
        manual = Paper(title="Manual addition", normalized_title="manual addition", source="manual")
        session.add_all([paper, manual])
        session.flush()
        task = TaskRun(
            kind="daily_brief",
            status="failed",
            payload_json={
                "date": target.isoformat(),
                "candidate_pool": {"paper_ids": [paper.id], "discovery_complete": False},
            },
        )
        session.add(task)
        session.flush()
        brief = Brief(
            brief_date=target,
            title="Interrupted daily draft",
            status="draft",
            task_id=task.id,
            introduction="Old introduction",
            snapshot_json={"interest_context_digest_used": "old-digest"},
        )
        session.add(brief)
        session.flush()
        session.add_all([
            BriefItem(brief_id=brief.id, paper_id=paper.id, rank=1, tier="featured"),
            BriefItem(brief_id=brief.id, paper_id=manual.id, rank=2, tier="manual"),
        ])
        session.commit()
        brief_id, paper_id, manual_id, old_task_id = brief.id, paper.id, manual.id, task.id

    monkeypatch.setattr(settings, "enable_codex_runs", True)
    submitted = []
    monkeypatch.setattr(briefs_api, "submit_task", submitted.append)
    response = client.post(f"/api/briefs/{brief_id}/recover-failed")
    assert response.status_code == 200, response.text
    task_id = response.json()["id"]
    assert submitted == [task_id]
    with SessionLocal() as session:
        brief = session.get(Brief, brief_id)
        assert brief.task_id == task_id
        assert brief.snapshot_json == {}
        assert brief.introduction is None
        items = list(session.scalars(select(BriefItem).where(BriefItem.brief_id == brief_id)))
        assert [(item.paper_id, item.tier) for item in items] == [(manual_id, "manual")]
        paper = session.get(Paper, paper_id)
        assert paper.catalog_summary == "已有摘要级总结"
        assert paper.research_score == 0.8
        assert paper.screening_feedback == {"comment": "用户已有筛选意见"}
        task = session.get(TaskRun, task_id)
        assert task.payload_json["recovery_source_task_ids"] == [old_task_id]
        audit = session.scalar(select(OperationLog).where(
            OperationLog.action == "brief.failed_publication_recovered",
            OperationLog.entity_id == brief_id,
        ))
        assert audit.detail_json["previous_publication"]["snapshot_json"][
            "interest_context_digest_used"
        ] == "old-digest"

    repeated = client.post(f"/api/briefs/{brief_id}/recover-failed")
    assert repeated.status_code == 409
