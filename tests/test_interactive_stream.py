from __future__ import annotations

import threading

import pytest

from frontier import interactive_stream
from frontier.interactive_stream import (
    CodexAppServerPool,
    CodexStreamError,
    _architecture_ui_event,
    _codex_status_activity,
    _public_item_activity,
    _tool_activity,
)


class _FakeProcess:
    def __init__(self) -> None:
        self.alive = True

    def poll(self) -> None:
        return None if self.alive else 1


def test_app_server_command_uses_configured_profile(monkeypatch) -> None:
    monkeypatch.setattr(interactive_stream.settings, "codex_profile", "openai")

    command = interactive_stream._app_server_command()

    assert command[:4] == [
        interactive_stream.settings.codex_command,
        "--strict-config",
        "--profile",
        "openai",
    ]
    assert "agents.max_concurrent_threads_per_session=1" in command
    assert "agents.max_depth=1" in command
    assert "agents.job_max_runtime_seconds=1800" in command
    assert command.index("app-server") > command.index(
        "agents.job_max_runtime_seconds=1800"
    )
    assert command[-1] == "--stdio"


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


def test_architecture_command_result_becomes_safe_ui_event() -> None:
    event = _architecture_ui_event(
        {
            "type": "commandExecution",
            "command": "uv run frontier architecture command paper-1 --data @commands.json",
            "exitCode": 0,
            "aggregatedOutput": (
                '{"paperId":"paper-1","revision":3,'
                '"architectureCommands":[{"type":"focus","target":"encoder"}]}'
            ),
        }
    )

    assert event == {
        "type": "architecture_command",
        "paper_id": "paper-1",
        "revision": 3,
        "commands": [{"type": "focus", "target": "encoder"}],
    }


def test_paper_commands_are_exposed_as_specific_activities() -> None:
    cases = [
        ("uv run frontier paper agent-context paper-1", "已同步论文上下文"),
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


def test_shell_page_placeholders_are_not_shown_as_page_numbers() -> None:
    for command, expected in [
        ("uv run frontier paper text paper-1 --page $page_num", "已读取论文正文"),
        (
            "uv run frontier paper render-page paper-1 ${page_num} --dpi 144",
            "已读取论文页面图像",
        ),
    ]:
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


def test_codex_status_activities_expose_only_safe_lifecycle_state() -> None:
    sensitive = {
        "prompt": "secret delegation prompt",
        "senderThreadId": "private-parent-thread",
        "receiverThreadIds": ["private-child-thread"],
        "agentsStates": {
            "private-child-thread": {
                "status": "completed",
                "message": "private child response",
            }
        },
    }
    cases = [
        (
            {"id": "compact-1", "type": "contextCompaction"},
            "running",
            ("正在压缩上下文", "", "running"),
        ),
        (
            {"id": "compact-1", "type": "contextCompaction"},
            "completed",
            ("已压缩上下文", "", "completed"),
        ),
        (
            {
                "id": "collab-1",
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "status": "inProgress",
                **sensitive,
            },
            "running",
            ("正在委派子 Agent", "", "running"),
        ),
        (
            {
                "id": "collab-1",
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "status": "completed",
                **sensitive,
            },
            "completed",
            ("已委派子 Agent", "", "completed"),
        ),
        (
            {
                "id": "collab-failed",
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "status": "failed",
                **sensitive,
            },
            "completed",
            ("委派子 Agent 失败", "", "failed"),
        ),
        (
            {
                "id": "subagent-1",
                "type": "subAgentActivity",
                "agentPath": "private/agent/path",
                "agentThreadId": "private-child-thread",
                "kind": "started",
            },
            "completed",
            ("子 Agent 工作状态", "已开始工作", "completed"),
        ),
        (
            {
                "id": "subagent-interrupted",
                "type": "subAgentActivity",
                "agentPath": "private/agent/path",
                "agentThreadId": "private-child-thread",
                "kind": "interrupted",
            },
            "completed",
            ("子 Agent 工作状态", "工作已中断", "completed"),
        ),
    ]

    for item, phase, expected in cases:
        activity = _codex_status_activity(item, phase)
        assert activity is not None
        assert (activity["label"], activity["detail"], activity["status"]) == expected
        public_text = str(activity)
        assert not any(
            secret in public_text
            for secret in [
                "secret delegation prompt",
                "private-parent-thread",
                "private-child-thread",
                "private child response",
                "private/agent/path",
            ]
        )


def test_public_item_activity_keeps_command_and_mcp_mapping() -> None:
    command = _public_item_activity(
        {
            "id": "command-1",
            "type": "commandExecution",
            "command": "uv run frontier paper context paper-1",
            "exitCode": 0,
        },
        "completed",
    )
    mcp = _public_item_activity(
        {
            "id": "mcp-1",
            "type": "mcpToolCall",
            "tool": "paper_lookup",
            "success": True,
        },
        "completed",
    )

    assert command is not None
    assert command["kind"] == "tool"
    assert command["label"] == "已载入论文上下文"
    assert mcp is not None
    assert mcp["kind"] == "tool"
    assert mcp["label"] == "已使用 paper_lookup"


def test_stream_turn_emits_compaction_and_subagent_activities(monkeypatch) -> None:
    responses = iter([{"thread": {"id": "thread-1"}}, {}])
    items = [
        {"id": "compact-1", "type": "contextCompaction"},
        {
            "id": "collab-1",
            "type": "collabAgentToolCall",
            "tool": "spawnAgent",
            "status": "completed",
            "prompt": "must remain private",
            "senderThreadId": "parent-thread",
            "receiverThreadIds": ["child-thread"],
            "agentsStates": {},
        },
        {
            "id": "subagent-1",
            "type": "subAgentActivity",
            "agentPath": "paper_searcher",
            "agentThreadId": "child-thread",
            "kind": "interacted",
        },
    ]
    messages = iter(
        [
            {"method": method, "params": {"item": item}}
            for item in items
            for method in ("item/started", "item/completed")
        ]
        + [{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}]
    )
    monkeypatch.setattr(interactive_stream, "_write_message", lambda *_args: None)
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    events = list(interactive_stream._stream_turn(object(), "test prompt"))  # type: ignore[arg-type]
    activities = [event for event in events if event["type"] == "activity"]

    assert [(event["activity_id"], event["label"], event["status"]) for event in activities] == [
        ("compact-1", "正在压缩上下文", "running"),
        ("compact-1", "已压缩上下文", "completed"),
        ("collab-1", "正在委派子 Agent", "running"),
        ("collab-1", "已委派子 Agent", "completed"),
        ("subagent-1", "子 Agent 工作状态", "running"),
        ("subagent-1", "子 Agent 工作状态", "completed"),
    ]
    assert activities[-1]["detail"] == "已更新进展"
    assert not any(
        private in str(activities)
        for private in ["must remain private", "parent-thread", "child-thread", "paper_searcher"]
    )


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


def test_stream_turn_rebuilds_thread_after_provider_switch(monkeypatch) -> None:
    writes: list[dict] = []
    responses = iter(
        [
            CodexStreamError(
                "failed to load configuration: Model provider `deepseek` not found"
            ),
            {
                "thread": {"id": "thread-openai"},
                "model": "gpt-5.6-sol",
                "modelProvider": "openai",
                "reasoningEffort": "medium",
            },
            {},
        ]
    )
    messages = iter(
        [{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}]
    )

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
            "thread-deepseek",
            recovery_prompt="persisted history and current prompt",
        )
    )

    assert [message["method"] for message in writes[:3]] == [
        "thread/resume",
        "thread/start",
        "turn/start",
    ]
    assert writes[2]["params"]["input"][0]["text"] == (
        "persisted history and current prompt"
    )
    assert next(event for event in events if event["type"] == "runtime") == {
        "type": "runtime",
        "model": "gpt-5.6-sol",
        "model_provider": "openai",
        "reasoning_effort": "medium",
    }


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


def test_app_server_pool_reuses_process_and_loaded_thread(monkeypatch) -> None:
    process = _FakeProcess()
    starts: list[object] = []
    stops: list[object] = []
    writes: list[dict] = []
    responses = iter(
        [
            {
                "thread": {"id": "thread-persistent"},
                "model": "gpt-5.4",
                "modelProvider": "openai",
            },
            {"turn": {"id": "turn-1", "status": "inProgress"}},
            {"turn": {"id": "turn-2", "status": "inProgress"}},
        ]
    )
    messages = iter(
        [
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )

    def start_process():
        starts.append(process)
        return process

    pool = CodexAppServerPool(max_workers=1)
    monkeypatch.setattr(interactive_stream, "_APP_SERVER_POOL", pool)
    monkeypatch.setattr(interactive_stream, "_start_process", start_process)
    monkeypatch.setattr(interactive_stream, "_stop_process", stops.append)
    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _connection, message: writes.append(message),
    )
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    first = list(interactive_stream.stream_codex_prompt("first"))
    second = list(
        interactive_stream.stream_codex_prompt(
            "second", session_id="thread-persistent"
        )
    )

    assert len(starts) == 1
    assert stops == []
    assert [message["method"] for message in writes] == [
        "thread/start",
        "turn/start",
        "turn/start",
    ]
    assert next(event for event in first if event["type"] == "thread")["thread_id"] == (
        "thread-persistent"
    )
    assert next(event for event in second if event["type"] == "runtime")["model"] == (
        "gpt-5.4"
    )

    pool.shutdown()
    assert stops == [process]


def test_pooled_run_cancellation_interrupts_only_its_turn(monkeypatch) -> None:
    process = _FakeProcess()
    worker = interactive_stream._CodexAppServerWorker(process)  # type: ignore[arg-type]
    handle = interactive_stream._PooledTurnHandle(worker)
    writes: list[dict] = []
    stops: list[object] = []
    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _connection, message: writes.append(message),
    )
    monkeypatch.setattr(interactive_stream, "_stop_process", stops.append)
    handle.bind_thread("thread-1")
    handle.bind_turn("turn-1")
    interactive_stream.register_codex_run("pooled-run", handle)

    assert interactive_stream.cancel_codex_run("pooled-run") is True
    interactive_stream.unregister_codex_run("pooled-run", handle)

    assert stops == []
    assert writes == [
        {
            "id": 2,
            "method": "turn/interrupt",
            "params": {"threadId": "thread-1", "turnId": "turn-1"},
        }
    ]


def test_cancel_watchdog_kills_worker_when_turn_never_binds(monkeypatch) -> None:
    class ImmediateTimer:
        def __init__(self, _seconds, callback) -> None:
            self.callback = callback
            self.daemon = False

        def start(self) -> None:
            self.callback()

        def cancel(self) -> None:
            return None

    process = _FakeProcess()
    pool = CodexAppServerPool(max_workers=1)
    stopped: list[object] = []
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(interactive_stream, "_stop_process", stopped.append)
    worker = pool.acquire()
    handle = interactive_stream._PooledTurnHandle(worker, pool)
    handle.bind_thread("thread-1")
    handle.mark_turn_requested()

    handle.cancel()

    assert stopped == [process]


def test_turn_watchdog_discards_unresponsive_worker(monkeypatch) -> None:
    class ImmediateTimer:
        def __init__(self, _seconds, callback) -> None:
            self.callback = callback
            self.daemon = False

        def start(self) -> None:
            self.callback()

        def cancel(self) -> None:
            return None

    process = _FakeProcess()
    pool = CodexAppServerPool(max_workers=1)
    stopped: list[object] = []

    def stop(target) -> None:
        stopped.append(target)
        target.alive = False

    def unresponsive_turn(*_args, run_handle, **_kwargs):
        run_handle.mark_turn_requested()
        raise CodexStreamError("worker stopped")
        yield  # pragma: no cover

    monkeypatch.setattr(interactive_stream, "_APP_SERVER_POOL", pool)
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", stop)
    monkeypatch.setattr(interactive_stream, "_stream_turn", unresponsive_turn)
    monkeypatch.setattr(interactive_stream.threading, "Timer", ImmediateTimer)

    with pytest.raises(CodexStreamError, match="wall-clock timeout"):
        list(interactive_stream.stream_codex_prompt("prompt"))

    assert stopped and pool._workers == []


def test_abandoned_stream_discards_worker_with_unfinished_turn(monkeypatch) -> None:
    process = _FakeProcess()
    stops: list[object] = []
    writes: list[dict] = []
    responses = iter(
        [
            {"thread": {"id": "thread-1"}},
            {"turn": {"id": "turn-1", "status": "inProgress"}},
        ]
    )
    messages = iter(
        [
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "answer-1", "delta": "partial"},
            }
        ]
    )
    pool = CodexAppServerPool(max_workers=1)
    monkeypatch.setattr(interactive_stream, "_APP_SERVER_POOL", pool)
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", stops.append)
    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _connection, message: writes.append(message),
    )
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    stream = interactive_stream.stream_codex_prompt("prompt", run_id="abandoned-run")
    assert next(stream)["type"] == "thread"
    assert next(stream) == {"type": "delta", "delta": "partial"}
    stream.close()

    assert any(message["method"] == "turn/interrupt" for message in writes)
    assert stops == [process]


def test_busy_affinity_does_not_block_a_different_thread_from_scaling(monkeypatch) -> None:
    processes = iter([_FakeProcess(), _FakeProcess()])
    starts: list[_FakeProcess] = []

    def start_process() -> _FakeProcess:
        process = next(processes)
        starts.append(process)
        return process

    monkeypatch.setattr(interactive_stream, "_start_process", start_process)
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=2)

    worker = pool.acquire()
    pool.release(worker, thread_id="thread-a")
    worker = pool.acquire()
    pool.release(worker, thread_id="thread-b")
    active_a = pool.acquire("thread-a")
    active_b = pool.acquire("thread-b")

    assert active_a is not active_b
    assert len(starts) == 2

    pool.release(active_a, thread_id="thread-a", previous_thread_id="thread-a")
    pool.release(active_b, thread_id="thread-b", previous_thread_id="thread-b")
    pool.shutdown()


def test_worker_start_failure_releases_capacity_for_retry(monkeypatch) -> None:
    process = _FakeProcess()
    attempts = iter([CodexStreamError("initialize failed"), process])

    def start_process():
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(interactive_stream, "_start_process", start_process)
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)

    with pytest.raises(CodexStreamError, match="initialize failed"):
        pool.acquire()
    worker = pool.acquire()

    assert worker.process is process
    pool.release(worker)
    pool.shutdown()


def test_malformed_turn_start_response_discards_worker(monkeypatch) -> None:
    process = _FakeProcess()
    stops: list[object] = []
    responses = iter([{"thread": {"id": "thread-1"}}, {}])
    pool = CodexAppServerPool(max_workers=1)
    monkeypatch.setattr(interactive_stream, "_APP_SERVER_POOL", pool)
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", stops.append)
    monkeypatch.setattr(interactive_stream, "_write_message", lambda *_args: None)
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))

    with pytest.raises(CodexStreamError, match="did not return a turn id"):
        list(interactive_stream.stream_codex_prompt("prompt"))

    assert stops == [process]


def test_dead_worker_loses_affinity_and_is_replaced(monkeypatch) -> None:
    first = _FakeProcess()
    second = _FakeProcess()
    processes = iter([first, second])
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: next(processes))
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)

    worker = pool.acquire()
    pool.release(worker, thread_id="thread-1")
    first.alive = False
    replacement = pool.acquire("thread-1")

    assert replacement.process is second
    pool.release(replacement, thread_id="thread-1", previous_thread_id="thread-1")
    pool.shutdown()


def test_cancel_before_turn_id_interrupts_after_binding(monkeypatch) -> None:
    worker = interactive_stream._CodexAppServerWorker(_FakeProcess())  # type: ignore[arg-type]
    handle = interactive_stream._PooledTurnHandle(worker)
    writes: list[dict] = []
    monkeypatch.setattr(
        interactive_stream,
        "_write_message",
        lambda _connection, message: writes.append(message),
    )

    handle.bind_thread("thread-1")
    handle.mark_turn_requested()
    handle.cancel()
    assert writes == []
    handle.bind_turn("turn-1")

    assert [message["method"] for message in writes] == ["turn/interrupt"]


def test_pool_shutdown_wakes_waiting_acquire(monkeypatch) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)
    active = pool.acquire()
    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def wait_for_worker() -> None:
        started.set()
        try:
            pool.acquire()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            finished.set()

    waiter = threading.Thread(target=wait_for_worker)
    waiter.start()
    assert started.wait(timeout=1)
    pool.shutdown()
    assert finished.wait(timeout=1)
    waiter.join(timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], CodexStreamError)
    pool.release(active)


def test_dead_worker_owner_release_cannot_miss_waiter_or_clear_new_owner(
    monkeypatch,
) -> None:
    first = _FakeProcess()
    second = _FakeProcess()
    processes = iter([first, second])
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: next(processes))
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)
    old_worker = pool.acquire("thread-1")
    old_lease = old_worker.active_lease_id
    first.alive = False
    acquired = threading.Event()
    replacement: list[interactive_stream._CodexAppServerWorker] = []

    def wait_for_replacement() -> None:
        replacement.append(pool.acquire("thread-1"))
        acquired.set()

    waiter = threading.Thread(target=wait_for_replacement)
    waiter.start()
    assert acquired.wait(timeout=1)
    pool.release(
        old_worker,
        thread_id="thread-1",
        previous_thread_id="thread-1",
        lease_id=old_lease,
    )

    new_worker = replacement[0]
    assert new_worker.process is second
    assert pool._active_threads["thread-1"][0] is new_worker
    pool.release(
        new_worker,
        thread_id="thread-1",
        previous_thread_id="thread-1",
        lease_id=new_worker.active_lease_id,
    )
    waiter.join(timeout=1)
    pool.shutdown()


def test_stale_lease_timeout_cannot_kill_reassigned_worker(monkeypatch) -> None:
    process = _FakeProcess()
    stopped: list[object] = []
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", stopped.append)
    pool = CodexAppServerPool(max_workers=1)
    worker = pool.acquire()
    stale_lease = worker.active_lease_id
    pool.release(worker, lease_id=stale_lease)
    reassigned = pool.acquire()

    assert reassigned is worker
    assert reassigned.active_lease_id != stale_lease
    assert pool.invalidate(
        worker,
        stale_lease,
        termination_reason="turn_timeout",
    ) is False
    assert stopped == []

    pool.release(reassigned, lease_id=reassigned.active_lease_id)
    pool.shutdown()


def test_pool_wait_is_cancelled_without_releasing_current_owner(monkeypatch) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)
    owner = pool.acquire()
    cancelled = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def wait_for_worker() -> None:
        try:
            pool.acquire(cancelled=cancelled)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            finished.set()

    waiter = threading.Thread(target=wait_for_worker)
    waiter.start()
    cancelled.set()
    assert finished.wait(timeout=1)
    assert len(errors) == 1
    assert isinstance(errors[0], interactive_stream.CodexRunCancelled)

    pool.release(owner, lease_id=owner.active_lease_id)
    waiter.join(timeout=1)
    pool.shutdown()


def test_evict_idle_workers_reclaims_only_unowned_app_servers(monkeypatch) -> None:
    first = _FakeProcess()
    second = _FakeProcess()
    processes = iter([first, second])
    stopped: list[object] = []
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: next(processes))
    monkeypatch.setattr(interactive_stream, "_stop_process", stopped.append)
    pool = CodexAppServerPool(max_workers=2)
    idle = pool.acquire("thread-idle")
    active = pool.acquire("thread-active")
    pool.release(
        idle,
        thread_id="thread-idle",
        lease_id=idle.active_lease_id,
    )

    assert pool.evict_idle_workers() == 1
    assert stopped == [first]
    assert pool._workers == [active]

    pool.release(
        active,
        thread_id="thread-active",
        lease_id=active.active_lease_id,
    )
    pool.shutdown()


def test_cancelled_turn_does_not_restore_abandoned_thread_affinity(monkeypatch) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(interactive_stream, "_start_process", lambda: process)
    monkeypatch.setattr(interactive_stream, "_stop_process", lambda _process: None)
    pool = CodexAppServerPool(max_workers=1)
    worker = pool.acquire("thread-abandoned")
    handle = interactive_stream._PooledTurnHandle(worker, pool)
    handle.bind_thread("thread-abandoned")
    worker.loaded_threads["thread-abandoned"] = {
        "thread": {"id": "thread-abandoned"}
    }
    handle.mark_terminal()
    handle.cancel()
    handle.finish()

    pool.release(
        worker,
        thread_id="thread-abandoned",
        previous_thread_id="thread-abandoned",
        lease_id=handle.lease_id,
    )

    assert "thread-abandoned" not in pool._thread_workers
    assert "thread-abandoned" not in worker.loaded_threads
    pool.shutdown()
