from __future__ import annotations

import json

import pytest
import typer

from frontier import cli
from frontier.agent_runner import build_prompt
from frontier.models import TaskRun
from frontier.services.generated_content import non_chinese_prose_fields


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("daily_brief", {"date": "2026-08-13"}),
        ("manual_paper_analysis", {"paper_id": "paper-1", "date": "2026-08-13"}),
        ("paper_metadata_analysis", {"paper_id": "paper-1"}),
        ("finish_reading", {"paper_id": "paper-1"}),
    ],
)
def test_persisted_agent_workflows_require_simplified_chinese(
    kind: str, payload: dict[str, str]
) -> None:
    prompt = build_prompt(TaskRun(id="task-1", kind=kind, payload_json=payload))

    assert "every persisted user-facing Agent-generated prose field" in prompt
    assert "Simplified Chinese" in prompt


@pytest.mark.parametrize("kind", ["paper_chat", "global_chat"])
def test_interactive_agent_requires_chinese_public_reasoning(kind: str) -> None:
    prompt = build_prompt(TaskRun(id="task-chat", kind=kind, payload_json={"message": "test"}))

    assert "final answer and every public progress or reasoning summary" in prompt
    assert "Simplified Chinese" in prompt


@pytest.mark.parametrize(
    ("kind", "payload", "ambiguity_action"),
    [
        (
            "daily_brief",
            {"date": "2026-08-13"},
            "do not import it; report that user confirmation is required",
        ),
        (
            "manual_paper_analysis",
            {"paper_id": "paper-1", "date": "2026-08-13"},
            "do not import it; report that user confirmation is required",
        ),
        (
            "paper_chat",
            {"paper_id": "paper-1", "message": "和 FTP-1 对比"},
            "ask the user to confirm the intended paper before importing anything",
        ),
        (
            "global_chat",
            {"message": "查找一篇指定论文"},
            "ask the user to confirm the intended paper before importing anything",
        ),
    ],
)
def test_research_prompts_expose_safe_computation_and_paper_lookup(
    kind: str, payload: dict[str, str], ambiguity_action: str
) -> None:
    prompt = build_prompt(TaskRun(id="task-42", kind=kind, payload_json=payload))

    assert "frontier paper list --query" in prompt
    assert "Reuse a reliable local match" in prompt
    assert "--no-ingest" in prompt
    assert ambiguity_action in prompt
    assert "Do not impose this confirmation step on broad topic discovery" in prompt
    assert "frontier paper resources PAPER_ID" in prompt
    assert "uv run python" in prompt
    assert "NumPy, SciPy, scikit-learn, pandas, and opencv-python" in prompt
    assert "data/tasks/task-42/" in prompt
    assert "Never run `uv add`" in prompt
    assert "calculation inputs, formula or metric definition" in prompt


def test_language_guard_allows_chinese_with_english_technical_terms() -> None:
    assert non_chinese_prose_fields(
        {
            "summary": "提出跨传感器的 tactile policy。",
            "agent_comment": "FTP-1 的迁移结果值得关注。",
            "recommendation_reason": None,
        }
    ) == []


def test_language_guard_reports_each_english_analysis_field() -> None:
    assert non_chinese_prose_fields(
        {
            "summary": "A generalist tactile policy.",
            "problem": "Cross-sensor transfer.",
            "research_score": 0.9,
        }
    ) == ["problem", "summary"]


def test_agent_cli_rejects_english_analysis_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = False

    def fake_request(*args: object, **kwargs: object) -> dict:
        nonlocal requested
        requested = True
        return {}

    monkeypatch.setattr(cli.client, "request", fake_request)

    with pytest.raises(typer.Exit):
        cli.paper_update("paper-1", json.dumps({"summary": "English-only summary"}))

    assert requested is False


def test_agent_cli_accepts_chinese_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        captured.update({"method": method, "path": path, **kwargs})
        return {"id": "paper-1"}

    monkeypatch.setattr(cli.client, "request", fake_request)

    cli.paper_update("paper-1", json.dumps({"summary": "这是一句中文总结。"}))

    assert captured["json"] == {"summary": "这是一句中文总结。"}
