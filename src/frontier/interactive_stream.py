from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from typing import Any

from .config import PROJECT_ROOT, settings


class CodexStreamError(RuntimeError):
    pass


def is_missing_codex_thread_error(error: BaseException | str) -> bool:
    """Return whether Codex can no longer resume a persisted thread locally."""
    detail = str(error).lower()
    return (
        "no rollout found for thread id" in detail
        or "rollout not found" in detail
        or "thread not found" in detail
    )


def _app_server_command() -> list[str]:
    command = [settings.codex_command, "app-server"]
    if settings.codex_sandbox == "workspace-write" and settings.codex_network_access:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    command.append("--stdio")
    return command


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["FRONTIER_API_BASE_URL"] = settings.api_base_url
    environment["UV_CACHE_DIR"] = str(settings.task_dir / "uv-cache")
    return environment


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _start_process() -> subprocess.Popen[str]:
    process = subprocess.Popen(
        _app_server_command(),
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    _write_message(
        process,
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "frontier", "title": "Frontier", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        },
    )
    _await_response(process, 1)
    _write_message(process, {"method": "initialized"})
    return process


def encode_stream_event(event_type: str, **payload: Any) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n"


def _write_message(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if process.stdin is None:
        raise CodexStreamError("Codex app-server stdin is unavailable")
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _read_message(process: subprocess.Popen[str]) -> dict[str, Any]:
    if process.stdout is None:
        raise CodexStreamError("Codex app-server stdout is unavailable")
    while True:
        line = process.stdout.readline()
        if not line:
            code = process.poll()
            raise CodexStreamError(f"Codex app-server closed unexpectedly (status {code})")
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue


def _await_response(process: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
    while True:
        message = _read_message(process)
        if message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise CodexStreamError(detail or "Codex app-server request failed")
        return message.get("result", {})


def _thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread", {})
    value = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(value, str) or not value:
        raise CodexStreamError("Codex app-server did not return a thread id")
    return value


def _short_public_text(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _tool_activity(item: dict[str, Any], status: str) -> dict[str, Any] | None:
    item_type = item.get("type")
    activity_id = str(item.get("id") or f"tool-{id(item)}")
    detail = ""
    failed = status == "failed"
    if item_type == "commandExecution":
        command = str(item.get("command") or "")
        if "frontier paper context" in command or "frontier paper show" in command:
            subject = "论文资料"
        elif "frontier paper" in command:
            subject = "论文信息"
        elif "frontier graph" in command:
            subject = "研究图谱"
        elif "frontier memory" in command:
            subject = "研究记忆"
        elif "uv run python" in command or command.lstrip().startswith("python"):
            subject = "计算工具"
        else:
            subject = "研究工具"
        exit_code = item.get("exitCode")
        duration = item.get("durationMs")
        failed = failed or (exit_code is not None and exit_code != 0)
        label = (
            f"正在读取{subject}"
            if status == "running"
            else f"{subject}调用失败" if failed else f"已完成{subject}调用"
        )
        if status != "running":
            detail = "调用失败" if failed else "调用成功"
            if isinstance(duration, int):
                detail += f" · {duration / 1000:.1f} 秒"
    elif item_type in {"mcpToolCall", "dynamicToolCall"}:
        tool = _short_public_text(item.get("tool") or "外部工具", 80)
        label = f"正在使用 {tool}" if status == "running" else f"已使用 {tool}"
        duration = item.get("durationMs")
        error = item.get("error")
        failed = failed or bool(error) or item.get("success") is False
        if status != "running":
            detail = "调用失败" if failed else "调用完成"
            if isinstance(duration, int):
                detail += f" · {duration / 1000:.1f} 秒"
    else:
        return None
    return {
        "type": "activity",
        "activity_id": activity_id,
        "kind": "tool",
        "label": label,
        "detail": detail,
        "status": "failed" if failed else status,
    }


def _stream_turn(
    process: subprocess.Popen[str],
    prompt: str,
    session_id: str | None = None,
    *,
    recovery_prompt: str | None = None,
    reasoning_effort: str | None = None,
    ephemeral: bool = False,
) -> Iterator[dict[str, Any]]:
    thread_request_id = 2
    turn_request_id = 3
    recovered = False
    if session_id:
        _write_message(
            process,
            {
                "id": 2,
                "method": "thread/resume",
                "params": {
                    "threadId": session_id,
                    "cwd": str(PROJECT_ROOT),
                    "approvalPolicy": "never",
                    "sandbox": settings.codex_sandbox,
                },
            },
        )
        try:
            result = _await_response(process, thread_request_id)
        except CodexStreamError as exc:
            if not is_missing_codex_thread_error(exc):
                raise
            recovered = True
            thread_request_id += 1
            turn_request_id += 1
            _write_message(
                process,
                {
                    "id": thread_request_id,
                    "method": "thread/start",
                    "params": {
                        "cwd": str(PROJECT_ROOT),
                        "approvalPolicy": "never",
                        "sandbox": settings.codex_sandbox,
                        "ephemeral": ephemeral,
                    },
                },
            )
            result = _await_response(process, thread_request_id)
    else:
        _write_message(
            process,
            {
                "id": 2,
                "method": "thread/start",
                "params": {
                    "cwd": str(PROJECT_ROOT),
                    "approvalPolicy": "never",
                    "sandbox": settings.codex_sandbox,
                    "ephemeral": ephemeral,
                },
            },
        )
        result = _await_response(process, thread_request_id)
    thread_id = _thread_id(result)
    yield {"type": "thread", "thread_id": thread_id}
    if recovered:
        yield {
            "type": "activity",
            "activity_id": "conversation-recovery",
            "kind": "status",
            "label": "已从历史消息恢复对话",
            "status": "completed",
        }

    turn_params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [
            {
                "type": "text",
                "text": (recovery_prompt or prompt) if recovered else prompt,
            }
        ],
    }
    if reasoning_effort:
        turn_params["effort"] = reasoning_effort
    _write_message(
        process,
        {"id": turn_request_id, "method": "turn/start", "params": turn_params},
    )
    _await_response(process, turn_request_id)
    streamed = ""
    completed_text = ""
    item_phases: dict[str, str | None] = {}
    reasoning_summaries: dict[str, str] = {}
    commentary: dict[str, str] = {}
    while True:
        message = _read_message(process)
        method = message.get("method")
        params = message.get("params", {})
        if method == "item/started":
            item = params.get("item", {})
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item.get("type") == "agentMessage":
                item_phases[item_id] = item.get("phase")
            elif item.get("type") == "reasoning":
                yield {
                    "type": "activity",
                    "activity_id": item_id,
                    "kind": "reasoning",
                    "label": "正在分析问题",
                    "status": "running",
                }
            else:
                activity = _tool_activity(item, "running")
                if activity:
                    yield activity
        elif method == "item/reasoning/summaryTextDelta":
            item_id = str(params.get("itemId") or "reasoning")
            reasoning_summaries[item_id] = reasoning_summaries.get(item_id, "") + str(
                params.get("delta") or ""
            )
            yield {
                "type": "activity",
                "activity_id": item_id,
                "kind": "reasoning",
                "label": "正在分析问题",
                "detail": _short_public_text(reasoning_summaries[item_id]),
                "status": "running",
            }
        elif method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            item_id = str(params.get("itemId") or "")
            if delta and item_phases.get(item_id) == "commentary":
                commentary[item_id] = commentary.get(item_id, "") + delta
                yield {
                    "type": "activity",
                    "activity_id": item_id,
                    "kind": "commentary",
                    "label": "Agent 更新",
                    "detail": _short_public_text(commentary[item_id]),
                    "status": "running",
                }
            elif delta:
                streamed += delta
                yield {"type": "delta", "delta": delta}
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                item_id = str(item.get("id") or "")
                phase = item.get("phase") or item_phases.get(item_id)
                if phase == "commentary":
                    yield {
                        "type": "activity",
                        "activity_id": item_id,
                        "kind": "commentary",
                        "label": "Agent 更新",
                        "detail": _short_public_text(item.get("text") or commentary.get(item_id)),
                        "status": "completed",
                    }
                else:
                    completed_text = item.get("text", "")
            elif item.get("type") == "reasoning":
                item_id = str(item.get("id") or "reasoning")
                summaries = item.get("summary") or []
                summary = "\n".join(str(value) for value in summaries if value)
                yield {
                    "type": "activity",
                    "activity_id": item_id,
                    "kind": "reasoning",
                    "label": "已完成分析",
                    "detail": _short_public_text(summary or reasoning_summaries.get(item_id)),
                    "status": "completed",
                }
            else:
                activity = _tool_activity(item, "completed")
                if activity:
                    yield activity
        elif method == "turn/completed":
            turn = params.get("turn", {})
            status = turn.get("status") if isinstance(turn, dict) else None
            if status == "failed":
                error = turn.get("error", {}) if isinstance(turn, dict) else {}
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise CodexStreamError(detail or "Codex turn failed")
            if completed_text and not streamed:
                yield {"type": "delta", "delta": completed_text}
            return


def stream_codex_prompt(
    prompt: str,
    session_id: str | None = None,
    *,
    recovery_prompt: str | None = None,
    reasoning_effort: str | None = None,
    ephemeral: bool = False,
) -> Iterator[dict[str, Any]]:
    process = _start_process()
    try:
        yield from _stream_turn(
            process,
            prompt,
            session_id,
            recovery_prompt=recovery_prompt,
            reasoning_effort=reasoning_effort,
            ephemeral=ephemeral,
        )
    finally:
        _stop_process(process)
