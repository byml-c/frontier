from fastapi.testclient import TestClient
from sqlalchemy import select

from frontier.database import SessionLocal
from frontier.models import Paper, TaskRun
from frontier.services.paper_metadata import (
    dispatch_next_metadata_batch,
    queue_metadata_enrichment,
)


def test_agent_paper_update_creates_and_updates_research_group(client: TestClient) -> None:
    paper = client.post(
        "/api/papers",
        json={
            "title": "Group profile paper",
            "authors": [{"name": "Ada Researcher"}],
            "first_author": "Ada Researcher",
            "primary_affiliation": "Frontier Robotics Institute",
        },
    ).json()["paper"]
    saved = client.patch(
        f"/api/papers/{paper['id']}",
        json={
            "research_group_summary": "该组代表性工作覆盖触觉操作与真实机器人控制。",
            "metadata_json": {
                "research_group_profile": {
                    "name": "Frontier Robotics Lab",
                    "institution": "Frontier Robotics Institute",
                    "research_focus": ["触觉操作", "真实机器人控制"],
                    "paper_contribution": "提出面向触觉操作的跨传感器策略。",
                    "is_representative": True,
                    "confidence": "high",
                    "evidence": "论文首页单位与方法实验部分。",
                }
            },
        },
    )
    assert saved.status_code == 200, saved.text
    groups = client.get("/api/research-groups", params={"q": "Frontier Robotics Lab"}).json()
    assert len(groups) == 1
    assert groups[0]["name"] == "Frontier Robotics Lab"
    assert groups[0]["paper_count"] == 1
    detail = client.get(f"/api/research-groups/{groups[0]['id']}").json()
    assert detail["papers"][0]["is_representative"] is True
    assert "触觉操作" in detail["research_focus"]


def test_paper_owns_canonical_group_and_group_tag(client: TestClient) -> None:
    paper = client.post(
        "/api/papers",
        json={
            "title": "Canonical group ownership",
            "authors": [{"name": "Ada Researcher"}],
            "first_author": "Ada Researcher",
        },
    ).json()["paper"]
    group = client.post(
        "/api/research-groups",
        json={
            "name": "Embodied Control Lab",
            "institution": "Frontier University",
            "research_focus": ["VLA", "触觉操作"],
            "verification_status": "verified",
        },
    ).json()

    saved = client.patch(
        f"/api/papers/{paper['id']}",
        json={"research_group_id": group["id"]},
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["research_group_id"] == group["id"]
    assert body["research_group"]["id"] == group["id"]
    assert [tag["name"] for tag in body["tags"]] == ["Embodied Control Lab"]

    detail = client.get(f"/api/research-groups/{group['id']}").json()
    assert [item["id"] for item in detail["papers"]] == [paper["id"]]

    searched = client.get("/api/research-groups", params={"q": "触觉操作"}).json()
    assert group["id"] in [item["id"] for item in searched]


def test_metadata_context_and_first_page_endpoints(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "frontier.api.papers.paper_first_page_text",
        lambda _session, paper, *, max_chars: {
            "paper_id": paper.id,
            "source": "arxiv_html",
            "text": "Frontier Robotics Lab"[:max_chars],
            "truncated": False,
        },
    )
    paper = client.post(
        "/api/papers",
        json={
            "title": "Metadata context paper",
            "abstract": "A robotics paper.",
            "authors": [{"name": "Ada Researcher"}],
            "source": "arxiv",
            "arxiv_id": "2608.19995",
            "arxiv_version": 1,
        },
    ).json()["paper"]

    context = client.get(f"/api/papers/{paper['id']}/metadata-context")
    assert context.status_code == 200
    assert context.json()["id"] == paper["id"]
    assert context.json()["research_group"] is None

    page = client.get(
        f"/api/papers/{paper['id']}/first-page", params={"max_chars": 300}
    )
    assert page.status_code == 200
    assert page.json()["paper_id"] == paper["id"]
    assert page.json()["text"] == "Frontier Robotics Lab"

    completed = client.post(
        f"/api/papers/{paper['id']}/metadata-enrichment/complete",
        json={"status": "completed", "note": "Affiliation was explicit on the HTML page."},
    )
    assert completed.status_code == 200
    assert (
        completed.json()["metadata_json"]["metadata_enrichment"]["status"] == "completed"
    )


def test_metadata_enrichment_task_is_bounded(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("frontier.services.paper_metadata.settings.enable_codex_runs", True)
    submitted: dict[str, object] = {}

    def submit(task_id: str, **kwargs: object) -> bool:
        submitted["task_id"] = task_id
        submitted.update(kwargs)
        return True

    monkeypatch.setattr("frontier.services.paper_metadata.submit_task", submit)
    paper = client.post(
        "/api/papers",
        json={"title": "Metadata task paper", "authors": [{"name": "Ada Researcher"}]},
    ).json()["paper"]
    with SessionLocal() as session:
        tasks = queue_metadata_enrichment(session, [paper["id"]], reason="test")
        assert tasks == []
        pending = session.get(Paper, paper["id"])
        assert pending.metadata_json["metadata_enrichment"]["status"] == "pending"
        task = dispatch_next_metadata_batch(session)
        assert task is not None
        assert task.kind == "paper_metadata_enrichment"
        assert paper["id"] in task.payload_json["paper_ids"]
        assert len(task.payload_json["paper_ids"]) <= 10
        assert task.payload_json.get("reason") == "coordinator"
        assert submitted["lane"] == "metadata"


def test_metadata_batch_stays_pending_when_agent_queue_is_full(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr("frontier.services.paper_metadata.settings.enable_codex_runs", True)
    monkeypatch.setattr("frontier.services.paper_metadata.submit_task", lambda *_args, **_kwargs: False)
    paper = client.post(
        "/api/papers",
        json={"title": "Queue pressure paper", "authors": [{"name": "Ada Researcher"}]},
    ).json()["paper"]
    with SessionLocal() as session:
        queue_metadata_enrichment(session, [paper["id"]], reason="test")
        assert dispatch_next_metadata_batch(session) is None
        pending = session.get(Paper, paper["id"])
        assert pending.metadata_json["metadata_enrichment"]["status"] == "pending"


def test_failed_metadata_enrichment_can_be_retried(client: TestClient) -> None:
    paper = client.post(
        "/api/papers",
        json={"title": "Retry metadata paper", "authors": [{"name": "Ada Researcher"}]},
    ).json()["paper"]
    with SessionLocal() as session:
        item = session.get(Paper, paper["id"])
        item.metadata_json = {
            "metadata_enrichment": {"status": "failed", "error": "queue full"}
        }
        session.commit()

    retried = client.post(f"/api/papers/{paper['id']}/metadata-enrichment/retry")
    assert retried.status_code == 200
    assert retried.json()["metadata_json"]["metadata_enrichment"]["status"] == "pending"


def test_metadata_coordinator_uses_two_parallel_batches(monkeypatch) -> None:
    monkeypatch.setattr("frontier.services.paper_metadata.settings.enable_codex_runs", True)
    monkeypatch.setattr(
        "frontier.services.paper_metadata.settings.metadata_agent_max_concurrent_runs", 2
    )
    monkeypatch.setattr(
        "frontier.services.paper_metadata.submit_task",
        lambda *_args, **_kwargs: True,
    )
    with SessionLocal() as session:
        for task in session.scalars(
            select(TaskRun).where(
                TaskRun.kind == "paper_metadata_enrichment",
                TaskRun.status.in_({"queued", "running"}),
            )
        ).all():
            task.status = "cancelled"
        session.commit()
        papers = [
            Paper(
                title=f"Metadata parallel {index}",
                normalized_title=f"metadata parallel {index}",
                authors=[{"name": "Ada Researcher"}],
            )
            for index in range(25)
        ]
        session.add_all(papers)
        session.commit()
        queue_metadata_enrichment(session, [paper.id for paper in papers], reason="test")
        first = dispatch_next_metadata_batch(session)
        second = dispatch_next_metadata_batch(session)
        third = dispatch_next_metadata_batch(session)
        assert first is not None and second is not None
        assert third is None
        assert len(first.payload_json["paper_ids"]) <= 10
        assert len(second.payload_json["paper_ids"]) <= 10
