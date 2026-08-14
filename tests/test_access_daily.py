from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from frontier.auth import INTERNAL_TOKEN_HEADER, internal_api_token
from frontier.config import settings


def _paper(client: TestClient) -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": "A Public Daily Paper",
            "source": "arxiv",
            "source_url": "https://arxiv.org/abs/2608.00001",
            "arxiv_id": "2608.00001",
            "first_author": "Ada",
            "primary_affiliation": "Frontier Lab",
            "summary": "公开简介",
            "agent_comment": "公开评语",
            "problem": "研究问题",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def test_daily_selection_exposes_only_read_only_share_data(client: TestClient) -> None:
    paper = _paper(client)
    annotation = client.post(
        f"/api/papers/{paper['id']}/annotations",
        json={
            "kind": "note",
            "page_number": 3,
            "selected_text": "evidence",
            "content": "我的笔记",
            "position_json": {"rects": [[0.1, 0.2, 0.3, 0.04]]},
            "color": "#fff19c",
        },
    )
    assert annotation.status_code == 200
    target = date(2026, 8, 14).isoformat()
    selected = client.put(f"/api/daily/{target}", json={"paper_id": paper["id"]})
    assert selected.status_code == 200

    response = client.get("/api/daily", params={"target_date": target})
    assert response.status_code == 200
    body = response.json()
    assert body["available_dates"] == [target]
    assert body["selection"]["paper"]["title"] == "A Public Daily Paper"
    assert body["selection"]["annotations"][0]["content"] == "我的笔记"
    assert "position_json" not in body["selection"]["annotations"][0]
    assert "pdf_path" not in body["selection"]["paper"]
    assert "metadata_json" not in body["selection"]["paper"]


def test_private_api_requires_a_trusted_device_while_daily_stays_public(
    client: TestClient,
) -> None:
    previous = settings.access_password
    settings.access_password = "test-frontier-password"
    client.cookies.clear()
    try:
        assert client.get("/api/daily").status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert client.get("/openapi.json").status_code == 401
        assert client.put(
            "/api/daily/2026-08-14", json={"paper_id": "missing"}
        ).status_code == 401
        assert client.get(
            "/api/settings", headers={INTERNAL_TOKEN_HEADER: internal_api_token()}
        ).status_code == 200
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        login = client.post(
            "/api/auth/login", json={"password": "test-frontier-password"}
        )
        assert login.status_code == 200
        assert login.json()["trusted"] is True
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/auth/status").json()["trusted"] is True
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/settings").status_code == 401
    finally:
        settings.access_password = previous
        client.cookies.clear()


def test_daily_discussion_is_bound_to_the_paper_across_dates(client: TestClient) -> None:
    paper = _paper(client)
    for target in ("2026-08-14", "2026-08-15"):
        assert client.put(
            f"/api/daily/{target}", json={"paper_id": paper["id"]}
        ).status_code == 200

    created = client.post(
        f"/api/daily/papers/{paper['id']}/reviews",
        json={"score": 5, "confidence": 4, "content": "**有价值**，但还需验证。"},
    )
    assert created.status_code == 200, created.text
    review = created.json()
    assert review["is_mine"] is True
    assert review["initial_score"] == 5
    assert len(review["author"]["name"]) == 4
    assert review["author"]["color"].startswith("#")

    duplicate = client.post(
        f"/api/daily/papers/{paper['id']}/reviews",
        json={"score": 3, "confidence": 3, "content": "不应创建第二份评审。"},
    )
    assert duplicate.status_code == 409

    reply = client.post(
        f"/api/daily/reviews/{review['id']}/replies",
        json={"content": f"@{review['author']['name']} 同意。"},
    )
    assert reply.status_code == 200
    assert "score" not in reply.json()

    updated = client.put(
        f"/api/daily/papers/{paper['id']}/reviews/mine",
        json={"score": 4, "confidence": 3, "content": "更新后的 **评审**。"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["initial_score"] == 5
    assert updated.json()["score"] == 4
    assert len(updated.json()["replies"]) == 1

    discussion = client.get(f"/api/daily/papers/{paper['id']}/reviews")
    assert discussion.status_code == 200
    body = discussion.json()
    assert body["paper_id"] == paper["id"]
    assert body["review_count"] == 1
    assert body["average_score"] == 4.0
    assert body["reviews"][0]["initial_score"] == 5
    assert body["reviews"][0]["replies"][0]["content"].endswith("同意。")
    assert client.post(
        f"/api/daily/papers/{paper['id']}/reviews",
        json={"score": 7, "confidence": 4, "content": "invalid"},
    ).status_code == 422
