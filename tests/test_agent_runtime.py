from __future__ import annotations

import threading
from pathlib import Path

import pytest

from frontier import agent_runner
from frontier.database import SessionLocal, create_tables
from frontier.models import TaskRun
from frontier.services import agent_runtime


def _reset_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_runtime, "_LAYOUT", None)
    monkeypatch.setattr(agent_runtime, "_INITIALIZATION_ATTEMPTED", False)
    monkeypatch.setattr(agent_runtime, "_INITIALIZATION_ERROR", None)


def test_codex_subagent_limits_are_invocation_overrides(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.settings, "codex_max_concurrent_subagents", 1)
    monkeypatch.setattr(agent_runtime.settings, "codex_subagent_max_depth", 1)
    monkeypatch.setattr(agent_runtime.settings, "codex_subagent_job_timeout_seconds", 900)

    args = agent_runtime.codex_subagent_limit_args()

    assert args == [
        "-c",
        "agents.max_concurrent_threads_per_session=1",
        "-c",
        "agents.max_depth=1",
        "-c",
        "agents.job_max_runtime_seconds=900",
    ]


def test_zero_subagent_limit_disables_delegation(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime.settings, "codex_max_concurrent_subagents", 0)

    args = agent_runtime.codex_subagent_limit_args()

    assert args[:2] == ["-c", "agents.enabled=false"]


def test_new_and_resumed_exec_commands_enforce_subagent_limits(monkeypatch) -> None:
    monkeypatch.setattr(agent_runner.settings, "codex_profile", "server")
    monkeypatch.setattr(agent_runner.settings, "codex_max_concurrent_subagents", 1)

    fresh = agent_runner._codex_exec_command("prompt", "/tmp/output.md")
    resumed = agent_runner._codex_exec_command(
        "prompt",
        "/tmp/output.md",
        session_id="thread-1",
    )

    for command in (fresh, resumed):
        assert command[:4] == [
            agent_runner.settings.codex_command,
            "--strict-config",
            "--profile",
            "server",
        ]
        assert "agents.max_concurrent_threads_per_session=1" in command
        assert command[-1] == "prompt"
    assert "resume" not in fresh
    assert resumed[resumed.index("exec") : resumed.index("exec") + 2] == ["exec", "resume"]
    assert resumed[-2] == "thread-1"


def test_required_cgroup_failure_is_fail_closed(monkeypatch, tmp_path: Path) -> None:
    _reset_runtime(monkeypatch)
    monkeypatch.setattr(agent_runtime.settings, "agent_cgroup_mode", "required")
    monkeypatch.setattr(agent_runtime.settings, "agent_cgroup_root", tmp_path)
    (tmp_path / "cgroup.controllers").write_text("cpu memory pids", encoding="utf-8")

    assert agent_runtime.initialize_agent_resource_manager() is False
    with pytest.raises(agent_runtime.AgentResourceUnavailable, match="did not delegate"):
        agent_runtime.create_agent_scope("daily-task")


def test_delegated_layout_writes_aggregate_and_per_run_budgets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_runtime(monkeypatch)
    monkeypatch.setattr(agent_runtime.settings, "agent_cgroup_mode", "required")
    monkeypatch.setattr(agent_runtime.settings, "agent_cgroup_root", tmp_path)

    def fake_read(path: Path) -> str:
        if path.name == "cgroup.controllers":
            return "cpu io memory pids"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    monkeypatch.setattr(agent_runtime, "_read_control", fake_read)

    assert agent_runtime.initialize_agent_resource_manager() is True
    scope = agent_runtime.create_agent_scope("daily/unsafe id")
    assert scope.path is not None
    assert scope.path.parent == tmp_path / "frontier-agents"
    assert scope.path.name.startswith("run-daily-unsafe-id-")
    assert (scope.path / "memory.max").read_text(encoding="utf-8") == str(
        agent_runtime.settings.agent_memory_max_bytes
    )
    assert (scope.path / "cpu.max").read_text(encoding="utf-8") == "100000 100000"
    assert "rbps=20971520 wbps=10485760" in (scope.path / "io.max").read_text(
        encoding="utf-8"
    )
    assert (scope.path / "memory.oom.group").read_text(encoding="utf-8") == "1"
    wrapped = scope.wrap_command(["codex", "exec", "prompt"])
    assert wrapped[-4:] == ["--", "codex", "exec", "prompt"]


def test_root_agent_slot_serializes_callers() -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with agent_runtime.root_agent_slot():
            first_entered.set()
            release_first.wait(timeout=1)

    def second() -> None:
        first_entered.wait(timeout=1)
        with agent_runtime.root_agent_slot():
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=1)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    assert second_entered.wait(timeout=1)
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)


def test_batch_agent_timeout_is_persisted_as_failure(monkeypatch, tmp_path: Path) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = iter(())
            self.alive = True

        def poll(self) -> None | int:
            return None if self.alive else -15

        def wait(self) -> int:
            return -15

    class ImmediateTimer:
        def __init__(self, _seconds: int, callback) -> None:
            self.callback = callback
            self.daemon = False

        def start(self) -> None:
            self.callback()

        def cancel(self) -> None:
            return None

    process = FakeProcess()
    monkeypatch.setattr(agent_runner.settings, "data_dir", tmp_path)
    monkeypatch.setattr(agent_runner.settings, "agent_task_timeout_seconds", 30)
    monkeypatch.setattr(agent_runner, "create_agent_scope", lambda _run_id: agent_runtime.AgentCgroupScope(None))
    monkeypatch.setattr(agent_runner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(agent_runner.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(agent_runner, "register_codex_run", lambda *_args: None)
    monkeypatch.setattr(agent_runner, "unregister_codex_run", lambda *_args: None)
    monkeypatch.setattr(
        agent_runner,
        "_stop_process",
        lambda _process: setattr(process, "alive", False),
    )
    create_tables()
    with SessionLocal() as session:
        task = TaskRun(
            kind="daily_brief",
            payload_json={"date": "2035-01-01"},
        )
        session.add(task)
        session.commit()
        task_id = task.id

    agent_runner._run_codex_task(task_id)

    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert task is not None
        assert task.status == "failed"
        assert task.finished_at is not None
        assert task.error is not None and "30 seconds" in task.error
