from __future__ import annotations

from frontier import interactive_stream
from frontier.interactive_stream import CodexStreamError, _tool_activity


def test_app_server_command_uses_configured_profile(monkeypatch) -> None:
    monkeypatch.setattr(interactive_stream.settings, "codex_profile", "openai")

    command = interactive_stream._app_server_command()

    assert command[:4] == [
        interactive_stream.settings.codex_command,
        "--profile",
        "openai",
        "app-server",
    ]


def test_failed_command_is_exposed_as_failed_activity() -> None:
    activity = _tool_activity(
        {
            "id": "command-1",
            "type": "commandExecution",
            "command": "uv run frontier paper context paper-1",
            "exitCode": 1,
            "durationMs": 250,
        },
        "completed",
    )

    assert activity is not None
    assert activity["status"] == "failed"
    assert activity["label"] == "载入论文上下文失败"


def test_python_command_is_exposed_as_computation_activity() -> None:
    activity = _tool_activity(
        {
            "id": "command-python",
            "type": "commandExecution",
            "command": "uv run python data/tasks/task-1/metrics.py",
            "exitCode": 0,
            "durationMs": 120,
        },
        "completed",
    )

    assert activity is not None
    assert activity["label"] == "已完成数据计算"
    assert activity["status"] == "completed"


def test_paper_commands_are_exposed_as_specific_activities() -> None:
    cases = [
        ("uv run frontier paper text paper-1", "已读取论文正文"),
        ("uv run frontier paper text paper-1 --page 7", "已读取论文第 7 页正文"),
        (
            "uv run frontier paper resources paper-1 --kind formula",
            "已读取论文公式 TeX",
        ),
        (
            "uv run frontier paper resources paper-1 --kind figure --block-id fig-2",
            "已读取论文图片",
        ),
        ("uv run frontier paper resources paper-1 --kind table", "已读取论文表格"),
        (
            "uv run frontier paper resources paper-1 --kind reference",
            "已读取论文引用关系",
        ),
        ("uv run frontier paper render-page paper-1 12 --dpi 144", "已读取论文第 12 页图像"),
    ]

    for command, expected in cases:
        activity = _tool_activity(
            {"id": command, "type": "commandExecution", "command": command, "exitCode": 0},
            "completed",
        )
        assert activity is not None
        assert activity["label"] == expected


def test_arxiv_search_activity_includes_a_short_query() -> None:
    activity = _tool_activity(
        {
            "id": "search-1",
            "type": "commandExecution",
            "command": (
                "uv run frontier source arxiv-search "
                "'ti:JEPA-WAM AND au:Smith' --max-results 10 --no-ingest"
            ),
            "aggregatedOutput": (
                '{"count": 2, "items": ['
                '{"title": "JEPA-WAM: World Models for Action"}, '
                '{"title": "A Comparison Baseline"}]}'
            ),
            "exitCode": 0,
        },
        "completed",
    )

    assert activity is not None
    assert activity["label"] == "已搜索 arXiv 论文 · 关键词：ti:JEPA-WAM AND au:Smith"
    assert activity["detail"] == (
        "找到 2 篇：JEPA-WAM: World Models for Action；A Comparison Baseline"
    )


def test_added_paper_activity_uses_returned_title() -> None:
    activity = _tool_activity(
        {
            "id": "add-1",
            "type": "commandExecution",
            "command": "uv run frontier paper add-arxiv 2608.10232",
            "aggregatedOutput": (
                'warning before json\n{"id": "paper-1", "title": "A Generalist Tactile Policy"}'
            ),
            "exitCode": 0,
        },
        "completed",
    )

    assert activity is not None
    assert activity["label"] == "已添加论文：A Generalist Tactile Policy"


def test_stream_turn_exposes_public_activity_without_mixing_commentary(
    monkeypatch,
) -> None:
    writes: list[dict] = []
    responses = iter([{"thread": {"id": "thread-1"}}, {}])
    messages = iter(
        [
            {
                "method": "item/started",
                "params": {"item": {"id": "reason-1", "type": "reasoning"}},
            },
            {
                "method": "item/reasoning/summaryTextDelta",
                "params": {"itemId": "reason-1", "delta": "检查论文上下文"},
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "reason-1",
                        "type": "reasoning",
                        "summary": ["检查论文上下文"],
                    }
                },
            },
            {
                "method": "item/started",
                "params": {
                    "item": {
                        "id": "comment-1",
                        "type": "agentMessage",
                        "phase": "commentary",
                        "text": "",
                    }
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "comment-1", "delta": "正在读取论文。"},
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "comment-1",
                        "type": "agentMessage",
                        "phase": "commentary",
                        "text": "正在读取论文。",
                    }
                },
            },
            {
                "method": "item/started",
                "params": {
                    "item": {
                        "id": "answer-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "",
                    }
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "answer-1", "delta": "**结论** 正文"},
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": "answer-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "**结论** 正文",
                    }
                },
            },
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )
    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _process, message: writes.append(message),
    )
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    events = list(interactive_stream._stream_turn(object(), "test prompt"))  # type: ignore[arg-type]
    assert any(event["type"] == "activity" and event.get("kind") == "reasoning" for event in events)
    assert any(
        event["type"] == "activity" and event.get("kind") == "commentary" for event in events
    )
    assert [event["delta"] for event in events if event["type"] == "delta"] == ["**结论** 正文"]
    turn_start = next(message for message in writes if message.get("method") == "turn/start")
    assert turn_start["params"]["summary"] == "concise"


def test_stream_turn_exposes_runtime_and_normalized_token_usage(monkeypatch) -> None:
    responses = iter(
        [
            {
                "thread": {"id": "thread-runtime"},
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "reasoningEffort": "high",
                "baseUrl": "https://must-not-be-exposed.example",
            },
            {},
        ]
    )
    messages = iter(
        [
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread-runtime",
                    "tokenUsage": {
                        "last": {"inputTokens": 90, "totalTokens": 128},
                        "total": {"inputTokens": 190, "totalTokens": 256},
                        "modelContextWindow": 4096,
                    },
                },
            },
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )
    monkeypatch.setattr(interactive_stream, "_write_message", lambda *_args: None)
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    events = list(interactive_stream._stream_turn(object(), "test prompt"))  # type: ignore[arg-type]

    assert next(event for event in events if event["type"] == "runtime") == {
        "type": "runtime",
        "model": "gpt-5.4",
        "model_provider": "openai",
        "reasoning_effort": "high",
    }
    assert next(event for event in events if event["type"] == "usage") == {
        "type": "usage",
        "current_tokens": 128,
        "context_window": 4096,
        "total_tokens": 256,
    }


def test_stream_turn_recovers_missing_rollout_with_new_thread(monkeypatch) -> None:
    writes: list[dict] = []
    responses = iter(
        [
            CodexStreamError("no rollout found for thread id 019ff168-56dd-7622-8f68-fe5d25b3f6d0"),
            {"thread": {"id": "thread-recovered"}},
            {},
        ]
    )
    messages = iter([{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}])

    def await_response(*_args):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _process, message: writes.append(message),
    )
    monkeypatch.setattr(interactive_stream, "_await_response", await_response)
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    events = list(
        interactive_stream._stream_turn(  # type: ignore[arg-type]
            object(),
            "current prompt",
            "missing-thread",
            recovery_prompt="persisted history and current prompt",
        )
    )

    assert [message["method"] for message in writes[:3]] == [
        "thread/resume",
        "thread/start",
        "turn/start",
    ]
    assert writes[2]["params"]["input"][0]["text"] == "persisted history and current prompt"
    assert any(event.get("activity_id") == "conversation-recovery" for event in events)
    assert next(event for event in events if event["type"] == "thread")["thread_id"] == (
        "thread-recovered"
    )


def test_stream_turn_does_not_hide_other_resume_errors(monkeypatch) -> None:
    monkeypatch.setattr(interactive_stream, "_write_message", lambda *_args: None)
    monkeypatch.setattr(
        interactive_stream,
        "_await_response",
        lambda *_args: (_ for _ in ()).throw(CodexStreamError("authentication failed")),
    )

    try:
        list(
            interactive_stream._stream_turn(  # type: ignore[arg-type]
                object(), "prompt", "thread-1", recovery_prompt="history"
            )
        )
    except CodexStreamError as exc:
        assert str(exc) == "authentication failed"
    else:
        raise AssertionError("Non-recoverable resume errors must still fail")
