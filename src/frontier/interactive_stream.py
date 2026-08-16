from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

from .config import PROJECT_ROOT, settings
from .services.agent_runtime import (
    attach_scope_to_process,
    close_process_scope,
    codex_subagent_limit_args,
    create_agent_scope,
    root_agent_slot,
)
from .services.outbound_network import agent_environment


class CodexStreamError(RuntimeError):
    pass


class CodexRunCancelled(CodexStreamError):
    pass


_ACTIVE_RUNS: dict[str, Any] = {}
_CANCELLED_RUNS: dict[str, float] = {}
_RUNS_LOCK = threading.Lock()
_CANCEL_MARK_TTL_SECONDS = 600


def _prune_cancelled_runs(now: float) -> None:
    expired = [
        run_id
        for run_id, cancelled_at in _CANCELLED_RUNS.items()
        if now - cancelled_at > _CANCEL_MARK_TTL_SECONDS
    ]
    for run_id in expired:
        _CANCELLED_RUNS.pop(run_id, None)


def cancel_codex_run(run_id: str) -> bool:
    """Cancel one Codex run and remember early cancellation races."""
    with _RUNS_LOCK:
        now = time.monotonic()
        _prune_cancelled_runs(now)
        _CANCELLED_RUNS[run_id] = now
        target = _ACTIVE_RUNS.pop(run_id, None)
    _cancel_run_target(target)
    return target is not None


def register_codex_run(run_id: str, target: Any) -> None:
    with _RUNS_LOCK:
        _prune_cancelled_runs(time.monotonic())
        cancelled = run_id in _CANCELLED_RUNS
        if not cancelled:
            _ACTIVE_RUNS[run_id] = target
    if cancelled:
        _cancel_run_target(target)
        raise CodexRunCancelled("Agent 工作流已由用户中断")


def unregister_codex_run(run_id: str, target: Any) -> None:
    with _RUNS_LOCK:
        if _ACTIVE_RUNS.get(run_id) is target:
            _ACTIVE_RUNS.pop(run_id, None)
        _CANCELLED_RUNS.pop(run_id, None)


def is_unusable_codex_thread_error(error: BaseException | str) -> bool:
    """Return whether a persisted thread must be rebuilt under current config.

    A rollout may disappear locally, or it may still exist while referring to a
    provider that the user has since removed.  Both make only that persisted
    thread unusable; a fresh thread can still run with the current Codex
    provider and recover its visible history from Frontier's database.
    """
    detail = str(error).lower()
    return (
        "no rollout found for thread id" in detail
        or "rollout not found" in detail
        or "thread not found" in detail
        or ("model provider" in detail and "not found" in detail)
    )


def _app_server_command() -> list[str]:
    command = [settings.codex_command, "--strict-config"]
    if settings.codex_profile:
        command.extend(["--profile", settings.codex_profile])
    command.extend(codex_subagent_limit_args())
    command.append("app-server")
    if settings.codex_sandbox == "workspace-write" and settings.codex_network_access:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    command.append("--stdio")
    return command


def _environment() -> dict[str, str]:
    return agent_environment()


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=settings.agent_timeout_grace_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        return
                else:
                    process.kill()
                process.wait(timeout=3)
    finally:
        close_process_scope(process)


def _start_process() -> subprocess.Popen[str]:
    scope = create_agent_scope("appserver")
    try:
        process = subprocess.Popen(
            scope.wrap_command(_app_server_command()),
            cwd=PROJECT_ROOT,
            env=_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=os.name == "posix",
        )
    except BaseException:
        scope.close()
        raise
    attach_scope_to_process(process, scope)
    startup_timed_out = threading.Event()

    def stop_after_startup_timeout() -> None:
        if process.poll() is None:
            startup_timed_out.set()
            _stop_process(process)

    startup_timeout = threading.Timer(
        settings.agent_app_server_start_timeout_seconds,
        stop_after_startup_timeout,
    )
    startup_timeout.daemon = True
    startup_timeout.start()
    try:
        _write_message(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "frontier",
                        "title": "Frontier",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        _await_response(process, 1)
        _write_message(process, {"method": "initialized"})
    except BaseException:
        _stop_process(process)
        if startup_timed_out.is_set():
            raise CodexStreamError(
                "Codex app-server startup exceeded the configured timeout"
            ) from None
        raise
    finally:
        startup_timeout.cancel()
    return process


class _CodexAppServerWorker:
    """One initialized app-server connection with serialized turn access."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.busy = False
        self.loaded_threads: dict[str, dict[str, Any]] = {}
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._next_request_id = 2

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def next_request_id(self) -> int:
        with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            return request_id

    def write_message(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            _write_process_message(self.process, message)

    def remember_thread(self, result: dict[str, Any]) -> str:
        thread_id = _thread_id(result)
        # Do not retain the whole protocol result: future schemas may include
        # turns or items. Reuse needs only identity and the public runtime data.
        self.loaded_threads[thread_id] = {
            "thread": {"id": thread_id},
            **{
                key: result[key]
                for key in ("model", "modelProvider", "reasoningEffort")
                if key in result
            },
        }
        return thread_id

    def forget_thread(self, thread_id: str | None) -> None:
        if thread_id:
            self.loaded_threads.pop(thread_id, None)


class CodexAppServerPool:
    """Fixed-size pool of persistent app-server workers with thread affinity."""

    def __init__(self, max_workers: int | None = None) -> None:
        configured = (
            settings.codex_app_server_max_workers if max_workers is None else max_workers
        )
        self.max_workers = max(1, configured)
        self._condition = threading.Condition()
        self._workers: list[_CodexAppServerWorker] = []
        self._thread_workers: dict[str, _CodexAppServerWorker] = {}
        self._active_threads: set[str] = set()
        self._starting_workers = 0
        self._closed = False

    def start(self) -> None:
        with self._condition:
            self._closed = False

    def warm(self) -> None:
        worker = self.acquire()
        self.release(worker)

    def acquire(self, thread_id: str | None = None) -> _CodexAppServerWorker:
        while True:
            should_start = False
            with self._condition:
                if self._closed:
                    raise CodexStreamError("Codex app-server pool is closed")
                self._discard_dead_workers_locked()

                if thread_id and thread_id in self._active_threads:
                    self._condition.wait()
                    continue

                affinity_worker = self._thread_workers.get(thread_id) if thread_id else None
                if affinity_worker is not None and not affinity_worker.busy:
                    return self._claim_worker_locked(affinity_worker, thread_id)

                idle_worker = next((worker for worker in self._workers if not worker.busy), None)
                if idle_worker is not None:
                    return self._claim_worker_locked(idle_worker, thread_id)

                if len(self._workers) + self._starting_workers < self.max_workers:
                    self._starting_workers += 1
                    should_start = True
                else:
                    self._condition.wait()

            if not should_start:
                continue
            try:
                worker = _CodexAppServerWorker(_start_process())
            except BaseException:
                with self._condition:
                    self._starting_workers -= 1
                    self._condition.notify_all()
                raise
            with self._condition:
                self._starting_workers -= 1
                if self._closed:
                    self._condition.notify_all()
                    stop_worker = True
                else:
                    self._workers.append(worker)
                    self._condition.notify_all()
                    return self._claim_worker_locked(worker, thread_id)
            if stop_worker:
                _stop_process(worker.process)
                raise CodexStreamError("Codex app-server pool is closed")

    def release(
        self,
        worker: _CodexAppServerWorker,
        *,
        thread_id: str | None = None,
        previous_thread_id: str | None = None,
        discard: bool = False,
    ) -> None:
        stop_worker = False
        with self._condition:
            if previous_thread_id:
                self._active_threads.discard(previous_thread_id)
            if thread_id:
                self._active_threads.discard(thread_id)
            if previous_thread_id and previous_thread_id != thread_id:
                if self._thread_workers.get(previous_thread_id) is worker:
                    self._thread_workers.pop(previous_thread_id, None)
                worker.forget_thread(previous_thread_id)
            if worker not in self._workers:
                return
            if discard or not worker.alive or self._closed:
                self._remove_worker_locked(worker)
                stop_worker = worker.alive
            else:
                if thread_id:
                    self._thread_workers[thread_id] = worker
                worker.busy = False
            self._condition.notify_all()
        if stop_worker:
            _stop_process(worker.process)

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            workers = list(self._workers)
            self._workers.clear()
            self._thread_workers.clear()
            self._active_threads.clear()
            self._condition.notify_all()
        for worker in workers:
            _stop_process(worker.process)

    def bind_thread(self, worker: _CodexAppServerWorker, thread_id: str) -> None:
        """Bind a newly started/resumed thread before its first event is exposed."""
        with self._condition:
            if worker not in self._workers or self._closed:
                return
            self._active_threads.add(thread_id)
            self._thread_workers[thread_id] = worker

    def _discard_dead_workers_locked(self) -> None:
        for worker in list(self._workers):
            if not worker.alive:
                self._remove_worker_locked(worker)

    def _claim_worker_locked(
        self, worker: _CodexAppServerWorker, thread_id: str | None
    ) -> _CodexAppServerWorker:
        worker.busy = True
        if thread_id:
            self._active_threads.add(thread_id)
        return worker

    def _remove_worker_locked(self, worker: _CodexAppServerWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        stale_threads = [
            thread_id
            for thread_id, assigned in self._thread_workers.items()
            if assigned is worker
        ]
        for thread_id in stale_threads:
            self._thread_workers.pop(thread_id, None)
        close_process_scope(worker.process)


class _PooledTurnHandle:
    """Cancellation target for one turn without terminating its shared worker."""

    def __init__(
        self,
        worker: _CodexAppServerWorker,
        pool: CodexAppServerPool | None = None,
    ) -> None:
        self.worker = worker
        self.pool = pool
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._cancelled = False
        self._finished = False
        self._terminal = False
        self._turn_requested = False
        self._interrupt_sent = False
        self._cancel_watchdog: threading.Timer | None = None
        self._lock = threading.Lock()

    def bind_thread(self, thread_id: str) -> None:
        with self._lock:
            self.thread_id = thread_id
        if self.pool:
            self.pool.bind_thread(self.worker, thread_id)

    def bind_turn(self, turn_id: str) -> None:
        with self._lock:
            self.turn_id = turn_id
            should_interrupt = self._claim_interrupt_locked()
        if should_interrupt:
            self._interrupt(thread_id=self.thread_id, turn_id=turn_id)

    def raise_if_cancelled(self) -> None:
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            raise CodexRunCancelled("Agent 工作流已由用户中断")

    def cancel(self) -> None:
        watchdog: threading.Timer | None = None
        with self._lock:
            self._cancelled = True
            thread_id = self.thread_id
            turn_id = self.turn_id
            should_interrupt = self._claim_interrupt_locked()
            if (
                self.pool is not None
                and not self._finished
                and not self._terminal
                and self._cancel_watchdog is None
            ):
                watchdog = threading.Timer(
                    settings.agent_timeout_grace_seconds,
                    self._kill_unresponsive_worker,
                )
                watchdog.daemon = True
                self._cancel_watchdog = watchdog
        if should_interrupt:
            assert turn_id is not None
            self._interrupt(thread_id=thread_id, turn_id=turn_id)
        if watchdog:
            watchdog.start()

    @property
    def reusable(self) -> bool:
        with self._lock:
            return not self._turn_requested or self._terminal

    def mark_turn_requested(self) -> None:
        with self._lock:
            self._turn_requested = True

    def mark_terminal(self) -> None:
        with self._lock:
            self._terminal = True
            watchdog = self._cancel_watchdog
            self._cancel_watchdog = None
        if watchdog:
            watchdog.cancel()

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            watchdog = self._cancel_watchdog
            self._cancel_watchdog = None
        if watchdog:
            watchdog.cancel()

    def _kill_unresponsive_worker(self) -> None:
        with self._lock:
            if self._finished or self._terminal:
                return
        _stop_process(self.worker.process)

    def _claim_interrupt_locked(self) -> bool:
        should_interrupt = bool(
            self._cancelled
            and self.thread_id
            and self.turn_id
            and not self._finished
            and not self._terminal
            and not self._interrupt_sent
        )
        if should_interrupt:
            self._interrupt_sent = True
        return should_interrupt

    def _interrupt(self, *, thread_id: str | None, turn_id: str) -> None:
        if not thread_id or not self.worker.alive:
            return
        try:
            _write_message(
                self.worker,
                {
                    "id": self.worker.next_request_id(),
                    "method": "turn/interrupt",
                    "params": {"threadId": thread_id, "turnId": turn_id},
                },
            )
        except (CodexStreamError, OSError):
            # The streaming reader will observe the dead connection and evict
            # this worker. Cancellation itself must remain idempotent.
            return


_APP_SERVER_POOL = CodexAppServerPool()


def start_codex_app_server_pool() -> None:
    """Start the pool lifecycle; its first worker is created on demand."""
    _APP_SERVER_POOL.start()


def stop_codex_app_server_pool() -> None:
    _APP_SERVER_POOL.shutdown()


def _cancel_run_target(target: Any) -> None:
    if target is None:
        return
    if isinstance(target, _PooledTurnHandle):
        target.cancel()
    else:
        _stop_process(target)


def encode_stream_event(event_type: str, **payload: Any) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n"


def _write_process_message(
    process: subprocess.Popen[str], message: dict[str, Any]
) -> None:
    if process.stdin is None:
        raise CodexStreamError("Codex app-server stdin is unavailable")
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _write_message(
    connection: subprocess.Popen[str] | _CodexAppServerWorker,
    message: dict[str, Any],
) -> None:
    if isinstance(connection, _CodexAppServerWorker):
        connection.write_message(message)
    else:
        _write_process_message(connection, message)


def _read_message(
    connection: subprocess.Popen[str] | _CodexAppServerWorker,
) -> dict[str, Any]:
    process = connection.process if isinstance(connection, _CodexAppServerWorker) else connection
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


def _await_response(
    connection: subprocess.Popen[str] | _CodexAppServerWorker,
    request_id: int,
) -> dict[str, Any]:
    while True:
        message = _read_message(connection)
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


def _runtime_event(result: dict[str, Any]) -> dict[str, Any] | None:
    """Expose only non-sensitive runtime identity returned by Codex app-server."""
    fields = {
        "model": result.get("model"),
        "model_provider": result.get("modelProvider"),
        "reasoning_effort": result.get("reasoningEffort"),
    }
    runtime = {
        key: value.strip()
        for key, value in fields.items()
        if isinstance(value, str) and value.strip()
    }
    return {"type": "runtime", **runtime} if runtime else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _usage_event(params: Any) -> dict[str, Any] | None:
    """Normalize app-server token usage without forwarding provider payloads."""
    if not isinstance(params, dict):
        return None
    token_usage = params.get("tokenUsage")
    if not isinstance(token_usage, dict):
        return None
    last = token_usage.get("last")
    total = token_usage.get("total")
    fields = {
        "current_tokens": _nonnegative_int(
            last.get("totalTokens") if isinstance(last, dict) else None
        ),
        "context_window": _nonnegative_int(token_usage.get("modelContextWindow")),
        "total_tokens": _nonnegative_int(
            total.get("totalTokens") if isinstance(total, dict) else None
        ),
    }
    usage = {key: value for key, value in fields.items() if value is not None}
    return {"type": "usage", **usage} if usage else None


def _short_public_text(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _command_tokens(command: str) -> list[str]:
    """Return the Frontier CLI portion of a shell command without exposing wrappers."""
    match = re.search(r"\bfrontier\s+", command)
    if not match:
        return []
    try:
        return shlex.split(command[match.end() :])
    except ValueError:
        return command[match.end() :].split()


def _option_value(tokens: list[str], option: str) -> str | None:
    try:
        value = tokens[tokens.index(option) + 1]
    except (ValueError, IndexError):
        return None
    return value.strip("'\"") or None


def _page_number(value: str | None) -> str | None:
    """Keep only concrete positive page numbers for public activity labels."""
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    return value if int(value) > 0 else None


def _arxiv_query(tokens: list[str]) -> str | None:
    values: list[str] = []
    for token in tokens[2:]:
        if token.startswith("--"):
            break
        values.append(token)
    query = _short_public_text(" ".join(values).strip("'\""), 56)
    return query or None


def _command_output_payload(item: dict[str, Any]) -> Any:
    output = item.get("aggregatedOutput")
    if not isinstance(output, str) or not output.strip():
        return None
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        start = output.find(marker)
        if start < 0:
            continue
        try:
            value, _ = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def _paper_title(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    if not isinstance(title, str) and isinstance(payload.get("paper"), dict):
        title = payload["paper"].get("title")
    return _short_public_text(title, 76) if isinstance(title, str) and title.strip() else None


def _frontier_command_actions(command: str, payload: Any = None) -> tuple[str, str] | None:
    """Describe a Frontier CLI command as running/completed user-facing actions."""
    tokens = _command_tokens(command)
    if len(tokens) < 2:
        return None
    group, action = tokens[:2]
    if group == "paper":
        if action == "text":
            page = _page_number(_option_value(tokens, "--page"))
            target = f"论文第 {page} 页正文" if page else "论文正文"
            return f"正在读取{target}", f"已读取{target}"
        if action == "agent-context":
            return "正在同步论文上下文", "已同步论文上下文"
        if action == "context":
            return "正在载入论文上下文", "已载入论文上下文"
        if action == "render-page":
            page = _page_number(
                tokens[3] if len(tokens) > 3 and not tokens[3].startswith("--") else None
            )
            target = f"论文第 {page} 页图像" if page else "论文页面图像"
            return f"正在读取{target}", f"已读取{target}"
        if action == "resources":
            kind = _option_value(tokens, "--kind")
            resource = {
                "formula": "公式 TeX",
                "figure": "图片",
                "table": "表格",
                "reference": "引用关系",
            }.get(kind or "", "结构化内容")
            return f"正在读取论文{resource}", f"已读取论文{resource}"
        actions = {
            "get": ("正在读取论文元信息", "已读取论文元信息"),
            "list": ("正在查询论文库", "已查询论文库"),
            "download": ("正在下载论文原文", "已下载论文原文"),
            "update": ("正在更新论文元信息", "已更新论文元信息"),
            "finish": ("正在整理精读结论", "已整理精读结论"),
            "upload": ("正在上传论文", "已上传论文"),
        }
        if action in {"add-arxiv", "add-url", "create"}:
            supplied = tokens[2] if len(tokens) > 2 and not tokens[2].startswith("--") else None
            target = _paper_title(payload) or (
                _short_public_text(supplied, 76) if supplied else "待识别论文"
            )
            return f"正在添加论文：{target}", f"已添加论文：{target}"
        return actions.get(action, ("正在处理论文资料", "已处理论文资料"))
    if group == "source" and action == "arxiv-search":
        query = _arxiv_query(tokens)
        suffix = f" · 关键词：{query}" if query else ""
        return f"正在搜索 arXiv 论文{suffix}", f"已搜索 arXiv 论文{suffix}"
    if group == "graph":
        return "正在读取或更新研究图谱", "已完成研究图谱操作"
    if group == "architecture":
        return "正在读取或更新论文架构", "已完成论文架构操作"
    if group == "memory":
        return "正在读取或更新研究记忆", "已完成研究记忆操作"
    return None


def _frontier_result_detail(command: str, payload: Any) -> str | None:
    tokens = _command_tokens(command)
    if tokens[:2] == ["paper", "agent-context"] and isinstance(payload, dict):
        sync = payload.get("sync")
        if isinstance(sync, dict):
            mode = sync.get("mode")
            changed = sync.get("changed_sections")
            if mode == "unchanged":
                return "无状态变化，复用已有上下文"
            if mode == "full":
                return "当前 Agent 线程已完成首次同步"
            if mode == "delta" and isinstance(changed, list):
                return f"已更新 {len(changed)} 个上下文分区"
    if tokens[:2] != ["source", "arxiv-search"] or not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    titles = [
        _short_public_text(item.get("title"), 68)
        for item in items
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    ]
    if not titles:
        return "未找到匹配论文"
    visible = "；".join(titles[:3])
    remaining = len(titles) - 3
    return f"找到 {len(titles)} 篇：{visible}" + (f"；另有 {remaining} 篇" if remaining > 0 else "")


def _architecture_ui_event(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("type") != "commandExecution" or item.get("exitCode") not in {None, 0}:
        return None
    command = str(item.get("command") or "")
    tokens = _command_tokens(command)
    payload = _command_output_payload(item)
    if not isinstance(payload, dict):
        return None
    if tokens[:2] == ["architecture", "command"]:
        commands = payload.get("architectureCommands")
        paper_id = payload.get("paperId")
        if isinstance(commands, list) and isinstance(paper_id, str):
            return {
                "type": "architecture_command",
                "paper_id": paper_id,
                "revision": payload.get("revision"),
                "commands": commands,
            }
    if tokens[:2] == ["architecture", "commit"]:
        paper_id = payload.get("paperId")
        revision = payload.get("revision")
        if isinstance(paper_id, str) and isinstance(revision, int):
            return {
                "type": "architecture_scene_updated",
                "paper_id": paper_id,
                "revision": revision,
            }
    return None


def _tool_activity(item: dict[str, Any], status: str) -> dict[str, Any] | None:
    item_type = item.get("type")
    activity_id = str(item.get("id") or f"tool-{id(item)}")
    detail = ""
    failed = status == "failed"
    if item_type == "commandExecution":
        command = str(item.get("command") or "")
        output_payload = _command_output_payload(item)
        actions = _frontier_command_actions(command, output_payload)
        if actions is None and (
            "uv run python" in command or command.lstrip().startswith("python")
        ):
            actions = ("正在运行数据计算", "已完成数据计算")
        if actions is None:
            actions = ("正在使用研究工具", "已完成研究工具调用")
        exit_code = item.get("exitCode")
        duration = item.get("durationMs")
        failed = failed or (exit_code is not None and exit_code != 0)
        label = (
            actions[0]
            if status == "running"
            else f"{actions[0].removeprefix('正在')}失败"
            if failed
            else actions[1]
        )
        if status != "running":
            detail = (
                "调用失败"
                if failed
                else _frontier_result_detail(command, output_payload) or "调用成功"
            )
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


def _codex_status_activity(item: dict[str, Any], status: str) -> dict[str, Any] | None:
    """Expose safe lifecycle labels for Codex runtime and sub-Agent items.

    Collaboration items can contain prompts, thread identifiers, internal agent
    messages, model settings, and tool arguments.  Deliberately map only their
    type and finite-state enums into public activity text.
    """
    item_type = item.get("type")
    activity_id = str(item.get("id") or f"status-{id(item)}")
    detail = ""
    public_status = status

    if item_type == "contextCompaction":
        label = "正在压缩上下文" if status == "running" else "已压缩上下文"
    elif item_type == "collabAgentToolCall":
        failed = status == "failed" or item.get("status") == "failed"
        public_status = "failed" if failed else status
        label = (
            "正在委派子 Agent"
            if status == "running"
            else "委派子 Agent 失败"
            if failed
            else "已委派子 Agent"
        )
    elif item_type == "subAgentActivity":
        activity_kind = item.get("kind")
        if status == "running":
            label = "子 Agent 工作状态"
            detail = "正在更新"
        elif activity_kind == "interrupted":
            label = "子 Agent 工作状态"
            detail = "工作已中断"
        elif activity_kind == "started":
            label = "子 Agent 工作状态"
            detail = "已开始工作"
        else:
            label = "子 Agent 工作状态"
            detail = "已更新进展"
    else:
        return None

    return {
        "type": "activity",
        "activity_id": activity_id,
        "kind": "status",
        "label": label,
        "detail": detail,
        "status": public_status,
    }


def _public_item_activity(item: dict[str, Any], status: str) -> dict[str, Any] | None:
    """Normalize supported app-server items into the public activity shape."""
    return _codex_status_activity(item, status) or _tool_activity(item, status)


def _stream_turn(
    process: subprocess.Popen[str] | _CodexAppServerWorker,
    prompt: str,
    session_id: str | None = None,
    *,
    recovery_prompt: str | None = None,
    reasoning_effort: str | None = None,
    ephemeral: bool = False,
    run_handle: _PooledTurnHandle | None = None,
) -> Iterator[dict[str, Any]]:
    worker = process if isinstance(process, _CodexAppServerWorker) else None

    def next_request_id(fallback: int) -> int:
        return worker.next_request_id() if worker else fallback

    thread_request_id = next_request_id(2)
    recovered = False
    cached_result = worker.loaded_threads.get(session_id) if worker and session_id else None
    if cached_result is not None:
        result = cached_result
    elif session_id:
        _write_message(
            process,
            {
                "id": thread_request_id,
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
            if not is_unusable_codex_thread_error(exc):
                raise
            recovered = True
            if worker:
                worker.forget_thread(session_id)
            thread_request_id = next_request_id(thread_request_id + 1)
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
    thread_id = worker.remember_thread(result) if worker else _thread_id(result)
    if run_handle:
        run_handle.bind_thread(thread_id)
        run_handle.raise_if_cancelled()
    yield {"type": "thread", "thread_id": thread_id}
    runtime_event = _runtime_event(result)
    if runtime_event:
        yield runtime_event
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
        # Request the provider-supported public reasoning summary. Raw
        # `item/reasoning/textDelta` content is intentionally not forwarded.
        "summary": "concise",
    }
    if reasoning_effort:
        turn_params["effort"] = reasoning_effort
    turn_request_id = next_request_id(3 if thread_request_id == 2 else thread_request_id + 1)
    if run_handle:
        run_handle.mark_turn_requested()
    _write_message(
        process,
        {"id": turn_request_id, "method": "turn/start", "params": turn_params},
    )
    turn_result = _await_response(process, turn_request_id)
    turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    if run_handle:
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexStreamError("Codex app-server did not return a turn id")
        run_handle.bind_turn(turn_id)
    streamed = ""
    completed_text = ""
    item_phases: dict[str, str | None] = {}
    reasoning_summaries: dict[str, str] = {}
    commentary: dict[str, str] = {}
    while True:
        message = _read_message(process)
        method = message.get("method")
        params = message.get("params", {})
        if method == "thread/tokenUsage/updated":
            usage_event = _usage_event(params)
            if usage_event:
                yield usage_event
        elif method == "item/started":
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
                activity = _public_item_activity(item, "running")
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
                activity = _public_item_activity(item, "completed")
                if activity:
                    yield activity
                ui_event = _architecture_ui_event(item)
                if ui_event:
                    yield ui_event
        elif method == "turn/completed":
            turn = params.get("turn", {})
            status = turn.get("status") if isinstance(turn, dict) else None
            if run_handle:
                run_handle.mark_terminal()
            if status == "interrupted":
                raise CodexRunCancelled("Agent 工作流已由用户中断")
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
    run_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    with root_agent_slot():
        worker = _APP_SERVER_POOL.acquire(session_id)
        run_handle = _PooledTurnHandle(worker, _APP_SERVER_POOL)
        timed_out = threading.Event()
        finished = threading.Event()

        def stop_after_turn_timeout() -> None:
            if not finished.is_set() and worker.alive:
                timed_out.set()
                _stop_process(worker.process)

        turn_timeout = threading.Timer(
            settings.agent_interactive_timeout_seconds,
            stop_after_turn_timeout,
        )
        turn_timeout.daemon = True
        turn_timeout.start()
        try:
            if run_id:
                register_codex_run(run_id, run_handle)
            try:
                yield from _stream_turn(
                    worker,
                    prompt,
                    session_id,
                    recovery_prompt=recovery_prompt,
                    reasoning_effort=reasoning_effort,
                    ephemeral=ephemeral,
                    run_handle=run_handle,
                )
            except CodexStreamError:
                if timed_out.is_set():
                    raise CodexStreamError(
                        "Agent turn exceeded the configured wall-clock timeout "
                        f"of {settings.agent_interactive_timeout_seconds} seconds"
                    ) from None
                raise
        finally:
            finished.set()
            turn_timeout.cancel()
            reusable = run_handle.reusable
            if not reusable:
                run_handle.cancel()
            run_handle.finish()
            if run_id:
                unregister_codex_run(run_id, run_handle)
            _APP_SERVER_POOL.release(
                worker,
                thread_id=run_handle.thread_id,
                previous_thread_id=session_id,
                discard=not reusable,
            )
