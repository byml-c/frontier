from fastapi.testclient import TestClient
from sqlalchemy import select

from frontier.database import SessionLocal
from frontier.models import Paper, TaskRun


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

    page = client.get(f"/api/papers/{paper['id']}/first-page", params={"max_chars": 300})
    assert page.status_code == 200
    assert page.json()["paper_id"] == paper["id"]
    assert page.json()["text"] == "Frontier Robotics Lab"

    assert "metadata_enrichment" not in context.json()


def test_ingestion_never_queues_independent_metadata_agent(client: TestClient) -> None:
    with SessionLocal() as session:
        before = list(session.scalars(select(TaskRun.id)).all())
    paper = client.post("/api/papers", json={"title": "Serial daily metadata"}).json()["paper"]
    with SessionLocal() as session:
        assert list(session.scalars(select(TaskRun.id)).all()) == before
        saved = session.get(Paper, paper["id"])
        assert "metadata_enrichment" not in (saved.metadata_json or {})
    for suffix in ("metadata-enrichment/complete", "metadata-enrichment/retry"):
        assert client.post(f"/api/papers/{paper['id']}/{suffix}", json={}).status_code in {404, 405}
    assert client.post("/api/papers/metadata-enrichment/retry-failed").status_code in {404, 405}
