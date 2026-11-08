from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import Paper
from frontier.services.interest_evidence import build_interest_context


def test_only_explicit_human_scores_enter_feedback_context():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Paper(
                    id="rated",
                    title="Rated",
                    normalized_title="rated",
                    user_score=0.413,
                    agent_score=0.6,
                ),
                Paper(id="zero", title="Zero", normalized_title="zero", user_score=0),
                Paper(id="unknown", title="Unknown", normalized_title="unknown", agent_score=0.9),
                Paper(id="ignored", title="Ignored", normalized_title="ignored", status="ignored"),
            ]
        )
        session.commit()
        context = build_interest_context(session)
        scores = {s["paper_id"]: s["labels"]["user_score"] for s in context["recent_feedback"]}
        assert scores == {"rated": 0.413, "zero": 0}
        assert context["calibration"]["score_sample_count"] == 1
        assert abs(context["calibration"]["score_mse"] - (0.6 - 0.413) ** 2) < 1e-9
        assert "interest_level" not in str(context)
        assert "quality_level" not in str(context)


def test_feedback_digest_changes_when_score_is_corrected():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Score", normalized_title="score", user_score=0.312)
        session.add(paper)
        session.commit()
        first = build_interest_context(session)["feedback_digest"]
        paper.user_score = 0.313
        session.commit()
        assert build_interest_context(session)["feedback_digest"] != first


def test_invalid_graph_selection_does_not_confirm_or_create_nodes(client):
    paper = client.post("/api/papers", json={"title": str(uuid4())}).json()["paper"]
    url = f"/api/papers/{paper['id']}"
    client.post(url + "/start-reading", json={})
    client.post(url + "/finish", json={"post_read_summary": "结论"})
    response = client.post(
        url + "/confirm-feedback",
        json={"user_score": 0.7, "node_links": [{"node_id": "missing", "reason": "证据"}]},
    )
    assert response.status_code == 422
    assert client.get(url).json()["status"] == "feedback_pending"
    assert client.get(url).json()["user_score"] is None


def test_notes_without_scores_remain_explicit_evidence_without_a_training_label():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Paper(title="Note", normalized_title="note", user_note="更关注真实机器人实验。")
        )
        session.commit()
        context = build_interest_context(session)
        assert context["recent_feedback"][0]["user_note"] == "更关注真实机器人实验。"
        assert context["recent_feedback"][0]["labels"]["user_score"] is None
        assert context["layers"]["confirmed_topic_profile"]["rated_paper_count"] == 0
        assert context["calibration"]["score_sample_count"] == 0
