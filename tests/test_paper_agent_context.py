from uuid import uuid4

from frontier.database import SessionLocal
from frontier.models import Conversation


def test_current_paper_context_tracks_only_canonical_markdown_and_scores(client):
    p = client.post("/api/papers", json={"title": str(uuid4())}).json()["paper"]
    url = f"/api/papers/{p['id']}"
    client.patch(
        url,
        json={
            "pre_read_summary": "读前简介",
            "post_read_summary": "读后结论 [[page:2]]",
            "agent_score": 0.731,
        },
    )
    client.patch(url + "/metadata", json={"user_score": 0})
    context = client.get(url + "/agent-context").json()
    assert context["paper"]["pre_read_summary"] == "读前简介"
    assert context["paper"]["post_read_summary"] == "读后结论 [[page:2]]"
    assert context["paper"]["user_score"] == 0
    assert "full_analysis" not in context
    assert "completion_summary_structured" not in context


def test_incremental_context_sync_survives_the_schema_cleanup(client):
    p = client.post("/api/papers", json={"title": str(uuid4())}).json()["paper"]
    with SessionLocal() as session:
        conv = Conversation(
            kind="paper", reference_id=p["id"], codex_session_id="thread-" + str(uuid4())
        )
        session.add(conv)
        session.commit()
        cid = conv.id
    endpoint = f"/api/papers/{p['id']}/agent-context/sync"
    payload = {"conversation_id": cid}
    first = client.post(endpoint, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["sync"]["mode"] == "full"
    assert client.post(endpoint, json=payload).json()["sync"]["mode"] == "unchanged"
    client.patch(f"/api/papers/{p['id']}/metadata", json={"post_read_summary": "修改后的总结"})
    delta = client.post(endpoint, json=payload).json()
    assert delta["sync"]["mode"] == "delta"
    assert delta["context"]["paper"]["post_read_summary"] == "修改后的总结"
    assert client.get("/api/papers/missing/agent-context").status_code == 404
