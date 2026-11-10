from frontier import cli


def test_existing_draft_accepts_replacement_title_and_introduction(client):
    brief = client.post(
        "/api/briefs",
        json={
            "brief_date": "2051-01-01",
            "title": "无可推荐论文",
            "introduction": "旧说明",
        },
    ).json()
    updated = client.post(
        "/api/briefs",
        json={
            "brief_date": "2051-01-01",
            "title": "重新发现论文",
            "introduction": "覆盖不完整",
        },
    ).json()
    assert updated["id"] == brief["id"]
    assert updated["title"] == "重新发现论文"
    assert updated["introduction"] == "覆盖不完整"
    unchanged = client.post("/api/briefs", json={"brief_date": "2051-01-01"}).json()
    assert unchanged["title"] == "重新发现论文"
    assert unchanged["introduction"] == "覆盖不完整"


def test_published_brief_text_edit_preserves_membership_and_publication(client):
    paper = client.post("/api/papers", json={"title": "Metadata correction regression"}).json()[
        "paper"
    ]
    brief = client.post(
        "/api/briefs",
        json={
            "brief_date": "2051-01-02",
            "title": "旧标题",
        },
    ).json()
    url = f"/api/briefs/{brief['id']}"
    assert client.post(url + "/items", json={"paper_id": paper["id"], "rank": 1}).status_code == 200
    published = client.post(url + "/publish").json()
    response = client.patch(
        url, json={"title": "正确标题", "introduction": "RSS 可用，API 覆盖不完整"}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "正确标题"
    assert updated["introduction"] == "RSS 可用，API 覆盖不完整"
    for field in ("items", "status", "published_at", "task_id", "interest_context_digest"):
        assert updated[field] == published[field]
    for body in (
        {"title": None},
        {"title": ""},
        {"title": "  "},
        {"status": "draft"},
        {"items": []},
    ):
        assert client.patch(url, json=body).status_code == 422
    assert client.patch(url, json={"introduction": None}).json()["introduction"] is None
    assert client.get(url).json()["title"] == "正确标题"


def test_brief_update_cli_forwards_only_requested_metadata(monkeypatch):
    captured = {}

    def request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {}

    monkeypatch.setattr(cli.client, "request", request)
    cli.brief_update("brief-1", '{"title":"正确标题"}')
    assert captured == {
        "method": "PATCH",
        "path": "/api/briefs/brief-1",
        "json": {"title": "正确标题"},
    }
