from __future__ import annotations

from datetime import date

from frontier.api import briefs as briefs_api
from frontier.config import settings
from frontier.database import SessionLocal
from frontier.models import Brief, BriefItem, Paper, TaskRun


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
            pre_read_summary="已有摘要级总结",
            agent_score=0.9,
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
            interest_context_digest="old-digest",
        )
        session.add(brief)
        session.flush()
        session.add_all(
            [
                BriefItem(brief_id=brief.id, paper_id=paper.id, rank=1, tier="featured"),
                BriefItem(brief_id=brief.id, paper_id=manual.id, rank=2, tier="manual"),
            ]
        )
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
        assert brief.interest_context_digest is None
        assert brief.introduction is None
        items = list(session.scalars(select(BriefItem).where(BriefItem.brief_id == brief_id)))
        assert [(item.paper_id, item.tier) for item in items] == [(manual_id, "manual")]
        paper = session.get(Paper, paper_id)
        assert paper.pre_read_summary == "已有摘要级总结"
        task = session.get(TaskRun, task_id)
        assert task.payload_json["recovery_source_task_ids"] == [old_task_id]
        audit = session.scalar(
            select(OperationLog).where(
                OperationLog.action == "brief.failed_publication_recovered",
                OperationLog.entity_id == brief_id,
            )
        )
        assert audit.detail_json["previous_publication"]["interest_context_digest"] == "old-digest"

    repeated = client.post(f"/api/briefs/{brief_id}/recover-failed")
    assert repeated.status_code == 409
