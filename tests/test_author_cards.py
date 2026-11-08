from typer.testing import CliRunner

from frontier.cli import app


def test_paper_author_crud_preserves_order_identity_and_evidence(client):
    paper = client.post(
        "/api/papers",
        json={"title": "Curated authors", "authors": [{"name": "Ada"}, {"name": "Bob"}]},
    ).json()["paper"]
    url = f"/api/papers/{paper['id']}/authors"
    cards = client.get(url).json()
    ada_id, bob_id = [a["id"] for a in cards]
    edited = client.patch(
        f"{url}/{ada_id}",
        json={
            "name": "Ada Chen",
            "affiliation": "论文单位",
            "current_affiliation": "现单位",
            "evidence": "论文首页，用户已核对",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["id"] == ada_id
    assert [a["name"] for a in client.get(url).json()] == ["Ada Chen", "Bob"]
    assert client.get(url, params={"q": "论文单位"}).json()[0]["id"] == ada_id
    assert client.get(f"{url}/{bob_id}").json()["name"] == "Bob"
    created = client.post(url, json={"name": "Carol", "affiliation": "C"}).json()
    assert client.delete(f"{url}/{ada_id}").status_code == 200
    remaining = client.get(url).json()
    assert [a["id"] for a in remaining] == [bob_id, created["id"]]
    assert client.get(f"{url}/{ada_id}").status_code == 404
    assert client.patch(f"{url}/{bob_id}", json={"name": " "}).status_code == 422
    assert client.post(url, json={"name": "X", "research_group_id": "missing"}).status_code == 404


def test_delete_group_detaches_cards_papers_and_tags(client):
    group = client.post(
        "/api/research-groups", json={"name": "Curated Lab", "institution": "U"}
    ).json()
    gid = group["id"]
    paper = client.post(
        "/api/papers", json={"title": "Group detach", "authors": [{"name": "Ada"}]}
    ).json()["paper"]
    pid = paper["id"]
    assert client.patch(f"/api/papers/{pid}", json={"research_group_id": gid}).status_code == 200
    card = client.get(f"/api/papers/{pid}/authors").json()[0]
    assert (
        client.patch(
            f"/api/papers/{pid}/authors/{card['id']}", json={"research_group_id": gid}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/research-groups/{gid}").status_code == 200
    assert client.get(f"/api/research-groups/{gid}").status_code == 404
    saved = client.get(f"/api/papers/{pid}").json()
    assert saved["research_group_id"] is None
    assert client.get(f"/api/papers/{pid}/authors").json()[0]["research_group_id"] is None


def test_author_cli_routes_to_server(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "frontier.cli.client.request", lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    )
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["paper", "author", "update", "paper", "author", "--data", '{"affiliation":"U"}']
        ).exit_code
        == 0
    )
    assert calls[-1] == (
        ("PATCH", "/api/papers/paper/authors/author"),
        {"json": {"affiliation": "U"}},
    )
    assert runner.invoke(app, ["research-group", "delete", "group"]).exit_code == 0
    assert calls[-1][0] == ("DELETE", "/api/research-groups/group")


def test_group_curation_preserves_explicit_verification_and_evidence(client):
    created = client.post(
        "/api/research-groups",
        json={
            "name": "Evidence Lab",
            "institution": "University",
            "evidence_json": {"source": "lab homepage"},
        },
    ).json()
    url = f"/api/research-groups/{created['id']}"
    assert created["evidence"] == {"source": "lab homepage"}
    updated = client.patch(url, json={"representative_summary": "用户讨论中补充的方向"}).json()
    assert updated["verification_status"] == "candidate"
    assert updated["evidence"] == created["evidence"]
    assert (
        client.patch(url, json={"verification_status": "verified"}).json()["verification_status"]
        == "verified"
    )
