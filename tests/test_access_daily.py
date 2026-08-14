from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from frontier.auth import INTERNAL_TOKEN_HEADER, internal_api_token
from frontier.config import settings
from frontier.database import SessionLocal
from frontier.models import DailySelection, DailySelectionItem
from frontier.services import daily_selections as daily_selection_service
from frontier.services.daily_selections import migrate_legacy_daily_selections


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
    structured = {
        "research_question": "研究问题 [[page:1]]。",
        "evidence_and_contribution": "证据与贡献 [[page:2]]。",
        "experiments": "实验结果 [[page:3]]。",
        "limitations": "局限性 [[page:4]]。",
        "user_attitude": "用户非常感兴趣，这是私有内容。",
    }
    finished = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "completion_summary_structured": structured,
            "suggested_interested": True,
            "suggested_quality": True,
        },
    )
    assert finished.status_code == 200, finished.text
    structured = {
        **structured,
        "research_question": "用户编辑后的研究问题 [[page:1]]。",
        "user_attitude": "用户编辑后的态度仍然是私有内容。",
    }
    edited = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"completion_summary_structured": structured},
    )
    assert edited.status_code == 200, edited.text
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
    assert len(body["selections"]) == 1
    assert body["selections"][0] == body["selection"]
    assert body["selection"]["paper"]["title"] == "A Public Daily Paper"
    assert body["selection"]["annotations"][0]["content"] == "我的笔记"
    assert "position_json" not in body["selection"]["annotations"][0]
    assert "pdf_path" not in body["selection"]["paper"]
    assert "metadata_json" not in body["selection"]["paper"]
    public_paper = body["selection"]["paper"]
    assert public_paper["completion_summary_structured"] == {
        key: value for key, value in structured.items() if key != "user_attitude"
    }
    assert "user_attitude" not in public_paper["completion_summary_structured"]
    serialized_public_paper = str(public_paper)
    assert "用户编辑后的态度" not in serialized_public_paper
    assert "completion_summary" not in public_paper
    assert "is_interested" not in public_paper
    assert "is_quality" not in public_paper


def test_daily_selection_appends_multiple_papers_in_stable_order(client: TestClient) -> None:
    first = _paper(client)
    response = client.post(
        "/api/papers",
        json={
            "title": "A Second Public Daily Paper",
            "source": "arxiv",
            "source_url": "https://arxiv.org/abs/2608.00002",
            "arxiv_id": "2608.00002",
        },
    )
    assert response.status_code == 200, response.text
    second = response.json()["paper"]
    target = "2026-08-16"

    first_add = client.put(f"/api/daily/{target}", json={"paper_id": first["id"]})
    second_add = client.put(f"/api/daily/{target}", json={"paper_id": second["id"]})
    duplicate = client.put(f"/api/daily/{target}", json={"paper_id": first["id"]})

    assert first_add.status_code == 200, first_add.text
    assert second_add.status_code == 200, second_add.text
    assert duplicate.status_code == 200, duplicate.text
    assert first_add.json()["rank"] == 1
    assert second_add.json()["rank"] == 2
    assert duplicate.json()["rank"] == 1

    body = client.get("/api/daily", params={"target_date": target}).json()
    assert [item["paper"]["id"] for item in body["selections"]] == [
        first["id"],
        second["id"],
    ]
    assert body["selection"] == body["selections"][0]
    assert body["available_dates"].count(target) == 1


def test_daily_selection_retries_a_stale_rank_without_poisoning_session(
    client: TestClient,
    monkeypatch,
) -> None:
    papers = []
    for index in (5, 6):
        response = client.post(
            "/api/papers",
            json={
                "title": f"Concurrent Daily Paper {index}",
                "source": "arxiv",
                "source_url": f"https://arxiv.org/abs/2608.0000{index}",
                "arxiv_id": f"2608.0000{index}",
            },
        )
        assert response.status_code == 200, response.text
        papers.append(response.json()["paper"])
    first, second = papers
    target = "2026-08-18"
    assert client.put(
        f"/api/daily/{target}", json={"paper_id": first["id"]}
    ).status_code == 200

    actual_next_rank = daily_selection_service._next_daily_rank
    calls = 0

    def stale_once(session, selection_date):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 1
        return actual_next_rank(session, selection_date)

    monkeypatch.setattr(daily_selection_service, "_next_daily_rank", stale_once)
    appended = client.put(f"/api/daily/{target}", json={"paper_id": second["id"]})

    assert appended.status_code == 200, appended.text
    assert appended.json()["rank"] == 2
    assert calls == 2
    body = client.get("/api/daily", params={"target_date": target}).json()
    assert [item["paper"]["id"] for item in body["selections"]] == [
        first["id"],
        second["id"],
    ]


def test_daily_selection_returns_conflict_after_bounded_rank_retries(
    client: TestClient,
    monkeypatch,
) -> None:
    papers = []
    for index in (7, 8):
        response = client.post(
            "/api/papers",
            json={
                "title": f"Bounded Conflict Daily Paper {index}",
                "source": "arxiv",
                "source_url": f"https://arxiv.org/abs/2608.0000{index}",
                "arxiv_id": f"2608.0000{index}",
            },
        )
        assert response.status_code == 200, response.text
        papers.append(response.json()["paper"])
    first, second = papers
    target = "2026-08-19"
    assert client.put(
        f"/api/daily/{target}", json={"paper_id": first["id"]}
    ).status_code == 200

    monkeypatch.setattr(daily_selection_service, "_next_daily_rank", lambda *_args: 1)
    conflicted = client.put(f"/api/daily/{target}", json={"paper_id": second["id"]})

    assert conflicted.status_code == 409, conflicted.text
    assert "请重试" in conflicted.json()["detail"]
    healthy = client.get("/api/daily", params={"target_date": target})
    assert healthy.status_code == 200, healthy.text
    assert [item["paper"]["id"] for item in healthy.json()["selections"]] == [first["id"]]


def test_daily_discussions_are_independent_for_papers_on_the_same_day(
    client: TestClient,
) -> None:
    papers = []
    for index in (3, 4):
        response = client.post(
            "/api/papers",
            json={
                "title": f"Independent Discussion Daily Paper {index}",
                "source": "arxiv",
                "source_url": f"https://arxiv.org/abs/2608.0000{index}",
                "arxiv_id": f"2608.0000{index}",
            },
        )
        assert response.status_code == 200, response.text
        papers.append(response.json()["paper"])
    first, second = papers
    target = "2026-08-17"
    for paper in (first, second):
        assert client.put(
            f"/api/daily/{target}", json={"paper_id": paper["id"]}
        ).status_code == 200

    first_review = client.post(
        f"/api/daily/papers/{first['id']}/reviews",
        json={"score": 5, "confidence": 4, "content": "第一篇的独立评审。"},
    )
    second_review = client.post(
        f"/api/daily/papers/{second['id']}/reviews",
        json={"score": 3, "confidence": 2, "content": "第二篇的独立评审。"},
    )
    assert first_review.status_code == 200, first_review.text
    assert second_review.status_code == 200, second_review.text

    first_discussion = client.get(f"/api/daily/papers/{first['id']}/reviews").json()
    second_discussion = client.get(f"/api/daily/papers/{second['id']}/reviews").json()
    assert first_discussion["review_count"] == 1
    assert second_discussion["review_count"] == 1
    assert first_discussion["reviews"][0]["content"] == "第一篇的独立评审。"
    assert second_discussion["reviews"][0]["content"] == "第二篇的独立评审。"


def test_legacy_daily_selection_migration_is_idempotent_and_preserves_source(
    client: TestClient,
) -> None:
    paper = _paper(client)
    target = date(2026, 8, 9)
    with SessionLocal() as session:
        legacy = session.get(DailySelection, target)
        if not legacy:
            session.add(DailySelection(selection_date=target, paper_id=paper["id"]))
            session.commit()

        assert migrate_legacy_daily_selections(session) == 1
        assert migrate_legacy_daily_selections(session) == 0
        count = session.scalar(
            select(func.count(DailySelectionItem.id)).where(
                DailySelectionItem.selection_date == target,
                DailySelectionItem.paper_id == paper["id"],
            )
        )
        migrated = session.scalar(
            select(DailySelectionItem).where(
                DailySelectionItem.selection_date == target,
                DailySelectionItem.paper_id == paper["id"],
            )
        )
        assert count == 1
        assert migrated is not None and migrated.rank == 1
        assert session.get(DailySelection, target) is not None


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


def test_daily_legacy_fallback_never_uses_private_completion_summary(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/papers",
        json={
            "title": "Legacy private completion summary",
            "source": "manual",
        },
    )
    assert created.status_code == 200, created.text
    paper = created.json()["paper"]
    updated = client.patch(
        f"/api/papers/{paper['id']}",
        json={
            "problem": "旧论文的研究问题。",
            "innovation": "旧论文的证据与贡献。",
            "experiment": "旧论文的实验。",
            "limitations": "旧论文的局限。",
        },
    )
    assert updated.status_code == 200, updated.text
    private_marker = "用户态度私有标记-legacy"
    finished = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"summary": f"论文总结。{private_marker}"},
    )
    assert finished.status_code == 200, finished.text
    target = date(2026, 8, 12).isoformat()
    assert (
        client.put(f"/api/daily/{target}", json={"paper_id": paper["id"]}).status_code
        == 200
    )

    public_paper = client.get(
        "/api/daily", params={"target_date": target}
    ).json()["selection"]["paper"]

    assert "completion_summary" not in public_paper
    assert private_marker not in str(public_paper)
    assert public_paper["completion_summary_structured"] == {
        "research_question": "旧论文的研究问题。",
        "evidence_and_contribution": "旧论文的证据与贡献。",
        "experiments": "旧论文的实验。",
        "limitations": "旧论文的局限。",
    }


def test_daily_discussion_is_bound_to_the_paper_across_dates(client: TestClient) -> None:
    paper = _paper(client)
    for target in ("2026-08-14", "2026-08-15"):
        assert client.put(
            f"/api/daily/{target}", json={"paper_id": paper["id"]}
        ).status_code == 200

    initial_discussion = client.get(f"/api/daily/papers/{paper['id']}/reviews").json()
    assert initial_discussion["can_rotate_identity"] is True
    old_identity = client.cookies.get("frontier_anonymous_device")
    rotated = client.post("/api/daily/identity/rotate")
    assert rotated.status_code == 200
    assert rotated.json()["can_rotate_identity"] is True
    assert len(rotated.json()["viewer"]["name"]) == 4
    assert client.cookies.get("frontier_anonymous_device") != old_identity

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
    assert client.post("/api/daily/identity/rotate").status_code == 409

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
