from fastapi.testclient import TestClient


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
