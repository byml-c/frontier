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
        session.add_all([paper, task])
        session.flush()
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
