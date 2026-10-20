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
    assert "store only incremental deep knowledge in `Paper.full_analysis`" in prompt
    assert "Do not use `metadata_json` as another knowledge store" in prompt


@pytest.mark.parametrize("kind", ["paper_chat", "global_chat"])
def test_interactive_agent_requires_chinese_public_reasoning(kind: str) -> None:
    prompt = build_prompt(TaskRun(id="task-chat", kind=kind, payload_json={"message": "test"}))

    assert "final answer and every public progress or reasoning summary" in prompt
    assert "Simplified Chinese" in prompt


def test_global_chat_requires_verified_explicit_preference_updates() -> None:
    prompt = build_prompt(
        TaskRun(
            id="preference-chat",
            kind="global_chat",
            payload_json={"message": "请把具身智能加入我的兴趣偏好，以后多推荐"},
        )
    )
    prompt = " ".join(prompt.split())

    assert "Read `frontier interest context` first" in prompt
    assert "`interest_profile` memory" in prompt
    assert "`frontier memory create` or `frontier memory update`" in prompt
    assert "`source=user_conversation`" in prompt
    assert "Re-read `frontier interest context` after the mutation" in prompt
    assert "Do not write the derived `derived-interest-profile-v1` directly" in prompt
    assert "must not reorder an already published brief" in prompt
    assert "without explicitly requesting a preference change" in prompt
    assert "do not mutate the interest profile" in prompt


def test_architecture_generation_separates_symbol_model_and_properties() -> None:
    prompt = build_prompt(
        TaskRun(id="architecture-task", kind="architecture_generation", payload_json={"paper_id": "paper-1"})
    )

    assert "is only its TeX identifier" in prompt
    assert "`architecture` is the concrete model family" in prompt
    assert "Never place a model architecture such as ViT" in prompt
    assert 'architecture: "DINOv2-B/14"' in prompt
    assert "use `\\(...\\)` for inline math and `\\[...\\]` for display math" in prompt


def test_paper_chat_uses_compact_agent_context_and_bounded_delegation() -> None:
    prompt = build_prompt(
        TaskRun(
            id="paper-task",
            kind="paper_chat",
            payload_json={"paper_id": "paper-1", "message": "解释实验结果"},
        )
    )

    assert "agent-context paper-1" in prompt
    assert "do not reload the complete paper by default" in prompt
    assert "use `\\(...\\)` for inline math and `\\[...\\]` for display math" in prompt
    assert "Use Codex subagents selectively" in prompt
    assert "only the main Agent may run canonical Frontier mutations" in prompt
    assert "can increase total token use" in prompt


def test_paper_chat_skips_context_tool_when_server_revision_is_current() -> None:
    prompt = build_prompt(
        TaskRun(
            id="paper-task-current",
            kind="paper_chat",
            payload_json={
                "paper_id": "paper-1",
                "conversation_id": "conversation-1",
                "paper_context_sync_required": False,
                "message": "继续解释",
            },
        )
    )

    command = "frontier paper agent-context paper-1 --conversation-id conversation-1"
    assert f"Do not call `{command}` in this turn" in prompt
    assert "previous Codex thread is unavailable" in prompt


def test_paper_chat_requests_incremental_context_when_state_changed() -> None:
    prompt = build_prompt(
        TaskRun(
            id="paper-task-delta",
            kind="paper_chat",
            payload_json={
                "paper_id": "paper-1",
                "conversation_id": "conversation-1",
                "paper_context_sync_required": True,
                "message": "批注更新了吗？",
            },
        )
    )

    assert (
        "Start with `frontier paper agent-context paper-1 --conversation-id conversation-1`"
        in prompt
    )


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


def test_finish_prompt_requires_the_structured_completion_schema() -> None:
    prompt = build_prompt(
        TaskRun(
            id="task-finish",
            kind="finish_reading",
            payload_json={"paper_id": "paper-1"},
        )
    )

    assert "completion_summary_structured" in prompt
    for field in (
        "problem",
        "innovation",
        "experiment",
        "limitations",
        "open_questions",
        "user_attitude",
    ):
        assert f"`{field}`" in prompt
    assert "as that paper's long-lived main Agent" in prompt
    assert "frontier paper agent-context paper-1" in prompt
    assert "do not reload the complete paper" in prompt


def test_daily_prompt_owns_candidate_metadata_and_can_delegate_reading() -> None:
    prompt = build_prompt(
        TaskRun(
            id="task-daily",
            kind="daily_brief",
            payload_json={"date": "2026-08-15"},
        )
    )

    assert "single daily-workflow root Agent" in prompt
    assert "candidate discovery, bibliographic" in prompt
    assert "canonical summary and analysis fields" in prompt
    assert "delegate independent full-text evidence extraction" in prompt
    assert "must not use or create a paper-reading Conversation" in prompt
    assert "Never write `completion_summary` or `completion_summary_structured`" in prompt
    assert "without user-confirmed feedback as missing data" in prompt
    assert "exclude those papers from hit-rate and calibration" in prompt
    assert "denominators" in prompt
    assert "Only `completed` feedback and explicit `ignored` actions are observations" in prompt
    assert "Legacy `is_interested=false` and `is_quality=false`" in prompt
    assert "are storage defaults, not user judgments" in prompt
    assert "owned by the ingestion metadata Agent" in prompt
    assert "`Paper.catalog_summary`" in prompt
    assert "`Paper.summary` is reserved" in prompt


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("manual_paper_analysis", {"paper_id": "paper-1", "date": "2026-08-15"}),
        ("paper_metadata_analysis", {"paper_id": "paper-1"}),
    ],
)
def test_catalog_analysis_does_not_use_paper_reading_agent(
    kind: str,
    payload: dict[str, str],
) -> None:
    prompt = build_prompt(TaskRun(id="catalog-task", kind=kind, payload_json=payload))

    assert "stateless catalog/brief analysis run" in prompt
    assert "create or resume a paper Conversation" in prompt
    assert "do not infer the user's attitude" in prompt


def test_finish_cli_rejects_english_structured_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = False

    def fake_request(*args: object, **kwargs: object) -> dict:
        nonlocal requested
        requested = True
        return {}

    monkeypatch.setattr(cli.client, "request", fake_request)

    with pytest.raises(typer.Exit):
        cli.paper_finish(
            "paper-1",
            json.dumps(
                {
                    "completion_summary_structured": {
                        "problem": "English-only question",
                        "innovation": "中文创新。",
                        "experiment": "中文实验。",
                        "limitations": "中文局限。",
                        "open_questions": "中文未解决问题。",
                        "user_attitude": "用户未明确表达。",
                    }
                }
            ),
        )

    assert requested is False
