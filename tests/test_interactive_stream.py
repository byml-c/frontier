from __future__ import annotations

from frontier import interactive_stream


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
