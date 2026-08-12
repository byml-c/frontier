from __future__ import annotations

from frontier import interactive_stream
from frontier.interactive_stream import CodexStreamError


def test_stream_turn_exposes_public_activity_without_mixing_commentary(
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(interactive_stream, "_write_message", lambda *_args: None)
    monkeypatch.setattr(interactive_stream, "_await_response", lambda *_args: next(responses))
    monkeypatch.setattr(interactive_stream, "_read_message", lambda *_args: next(messages))

    events = list(interactive_stream._stream_turn(object(), "test prompt"))  # type: ignore[arg-type]
    assert any(event["type"] == "activity" and event.get("kind") == "reasoning" for event in events)
    assert any(
        event["type"] == "activity" and event.get("kind") == "commentary" for event in events
    )
    assert [event["delta"] for event in events if event["type"] == "delta"] == ["**结论** 正文"]


def test_stream_turn_recovers_missing_rollout_with_new_thread(monkeypatch) -> None:
    writes: list[dict] = []
    responses = iter(
        [
            CodexStreamError(
                "no rollout found for thread id 019ff168-56dd-7622-8f68-fe5d25b3f6d0"
            ),
            {"thread": {"id": "thread-recovered"}},
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
    assert any(
        event.get("activity_id") == "conversation-recovery" for event in events
    )
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
