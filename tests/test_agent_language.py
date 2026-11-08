import pytest
from typer.testing import CliRunner

from frontier.agent_runner import build_prompt
from frontier.cli import app
from frontier.models import TaskRun


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("daily_brief", {"date": "2026-11-08"}),
        ("manual_paper_analysis", {"paper_id": "p"}),
        ("paper_metadata_analysis", {"paper_id": "p"}),
        ("finish_reading", {"paper_id": "p"}),
        ("paper_chat", {"paper_id": "p", "message": "解释论文"}),
        ("global_chat", {"message": "研究偏好"}),
    ],
)
def test_agents_share_the_current_contract_and_language(kind, payload):
    prompt = build_prompt(TaskRun(id="task", kind=kind, payload_json=payload))
    assert "Simplified Chinese" in prompt
    assert "pre_read_summary" in prompt and "post_read_summary" in prompt
    assert "user_score" in prompt and "agent_score" in prompt and "model_score" in prompt
    assert "Never infer user_score" in prompt
    assert "Never access SQLite" in prompt
    assert "frontier paper list --query" in prompt
    assert "no fixed form" in prompt or "exactly two Markdown fields" in prompt


def test_daily_is_single_background_workflow_and_preserves_post_read_summary():
    prompt = build_prompt(
        TaskRun(id="daily", kind="daily_brief", payload_json={"date": "2026-11-08"})
    )
    assert "Never spawn subagents" in prompt
    assert "Preserve existing post_read_summary" in prompt
    assert "first-page" in prompt and "primary_affiliation" in prompt
    assert "never create a paper-content snapshot" in prompt


def test_reading_sync_respects_existing_thread_and_allows_bounded_child():
    prompt = build_prompt(
        TaskRun(
            id="chat",
            kind="paper_chat",
            payload_json={
                "paper_id": "p",
                "conversation_id": "c",
                "paper_context_sync_required": False,
                "message": "继续",
            },
        )
    )
    assert "Do not call `frontier paper agent-context p --conversation-id c`" in prompt
    assert "at most one concurrent" in prompt
    assert "no nested delegation" in prompt
    assert "[[page:N]]" in prompt


@pytest.mark.parametrize("field", ["pre_read_summary", "post_read_summary"])
def test_cli_rejects_non_chinese_generated_markdown(field, monkeypatch):
    calls = []
    monkeypatch.setattr("frontier.cli.client.request", lambda *a, **k: calls.append(a) or {})
    result = CliRunner().invoke(
        app, ["paper", "update", "p", "--data", f'{{"{field}":"English only"}}']
    )
    assert result.exit_code == 1
    assert calls == []
