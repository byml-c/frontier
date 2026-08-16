from __future__ import annotations

import errno
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from ..config import settings

logger = logging.getLogger(__name__)

_CGROUP_CONTROLLERS = ("cpu", "io", "memory", "pids")
_CGROUP_PERIOD_US = 100_000
_ROOT_AGENT_SLOTS = threading.BoundedSemaphore(settings.agent_max_concurrent_runs)
_LAYOUT_LOCK = threading.Lock()
_LAYOUT: _CgroupLayout | None = None
_INITIALIZATION_ATTEMPTED = False
_INITIALIZATION_ERROR: str | None = None


class AgentResourceUnavailable(RuntimeError):
    """Raised when production requires isolation but cgroup setup is unavailable."""


@dataclass(frozen=True)
class _CgroupLayout:
    root: Path
    server: Path
    agents: Path


def codex_subagent_limit_args() -> list[str]:
    """Return invocation-level limits that profiles cannot accidentally relax."""
    if settings.codex_max_concurrent_subagents == 0:
        concurrency = ["-c", "agents.enabled=false"]
    else:
        concurrency = [
            "-c",
            (
                "agents.max_concurrent_threads_per_session="
                f"{settings.codex_max_concurrent_subagents}"
            ),
        ]
    return [
        *concurrency,
        "-c",
        f"agents.max_depth={settings.codex_subagent_max_depth}",
        "-c",
        f"agents.job_max_runtime_seconds={settings.codex_subagent_job_timeout_seconds}",
    ]


@contextmanager
def root_agent_slot() -> Iterator[None]:
    """Serialize root Codex work across exec tasks and interactive turns."""
    _ROOT_AGENT_SLOTS.acquire()
    try:
        yield
    finally:
        _ROOT_AGENT_SLOTS.release()


def initialize_agent_resource_manager() -> bool:
    """Create the delegated Web/Agent cgroup hierarchy once per service process.

    Failure does not take down the Web service. In ``required`` mode every later
    Agent launch fails closed through :func:`create_agent_scope`.
    """
    global _INITIALIZATION_ATTEMPTED, _INITIALIZATION_ERROR, _LAYOUT

    with _LAYOUT_LOCK:
        if _INITIALIZATION_ATTEMPTED:
            return _LAYOUT is not None
        _INITIALIZATION_ATTEMPTED = True
        if settings.agent_cgroup_mode == "disabled":
            return False
        try:
            _LAYOUT = _initialize_cgroup_layout(_current_cgroup_root())
        except (OSError, ValueError) as exc:
            _INITIALIZATION_ERROR = str(exc)
            logger.warning("Agent cgroup isolation is unavailable: %s", exc)
            return False
        return True


def _current_cgroup_root() -> Path:
    if settings.agent_cgroup_root is not None:
        return settings.agent_cgroup_root
    for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, _controllers, relative = line.split(":", 2)
        if hierarchy == "0":
            return Path("/sys/fs/cgroup") / relative.lstrip("/")
    raise ValueError("The current process is not running in a cgroup v2 hierarchy")


def _initialize_cgroup_layout(root: Path) -> _CgroupLayout:
    controllers = set(_read_control(root / "cgroup.controllers").split())
    missing = set(_CGROUP_CONTROLLERS) - controllers
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"The service did not delegate required cgroup controllers: {names}")

    server = root / "frontier-server"
    agents = root / "frontier-agents"
    io_devices = _data_device_ids()
    server.mkdir(exist_ok=True)
    moved = False
    controllers_enabled = False
    try:
        _write_control(server / "cgroup.procs", str(os.getpid()))
        moved = True
        _enable_controllers(root)
        controllers_enabled = True
        agents.mkdir(exist_ok=True)
        _write_budget(
            server,
            memory_high=settings.agent_server_memory_high_bytes,
            memory_max=settings.agent_server_memory_max_bytes,
            swap_max=settings.agent_server_memory_swap_max_bytes,
            cpu_percent=settings.agent_server_cpu_quota_percent,
            io_weight=settings.agent_io_weight,
            io_devices=io_devices,
            io_read_bps=settings.agent_io_read_bps,
            io_write_bps=settings.agent_io_write_bps,
            tasks_max=settings.agent_server_tasks_max,
        )
        _write_budget(
            agents,
            memory_high=settings.agent_pool_memory_high_bytes,
            memory_max=settings.agent_pool_memory_max_bytes,
            swap_max=settings.agent_pool_memory_swap_max_bytes,
            cpu_percent=settings.agent_pool_cpu_quota_percent,
            io_weight=settings.agent_io_weight,
            io_devices=io_devices,
            io_read_bps=settings.agent_io_read_bps,
            io_write_bps=settings.agent_io_write_bps,
            tasks_max=settings.agent_pool_tasks_max,
        )
        _enable_controllers(agents)
    except (OSError, ValueError):
        if moved and not controllers_enabled:
            try:
                _write_control(root / "cgroup.procs", str(os.getpid()))
            except OSError:
                logger.exception("Failed to roll the server process back to its service cgroup")
        raise
    return _CgroupLayout(root=root, server=server, agents=agents)


def _enable_controllers(path: Path) -> None:
    enabled = set(_read_control(path / "cgroup.subtree_control").split())
    missing = [name for name in _CGROUP_CONTROLLERS if name not in enabled]
    if missing:
        _write_control(path / "cgroup.subtree_control", " ".join(f"+{name}" for name in missing))


def _write_budget(
    path: Path,
    *,
    memory_high: int,
    memory_max: int,
    swap_max: int,
    cpu_percent: int,
    io_weight: int,
    io_devices: Sequence[str],
    io_read_bps: int,
    io_write_bps: int,
    tasks_max: int,
) -> None:
    if memory_high > memory_max:
        raise ValueError(f"memory.high exceeds memory.max for {path.name}")
    _write_control(path / "memory.high", str(memory_high))
    _write_control(path / "memory.max", str(memory_max))
    _write_control(path / "memory.swap.max", str(swap_max))
    _write_control(path / "cpu.max", _cpu_max(cpu_percent))
    _write_control(path / "io.weight", f"default {io_weight}")
    _write_io_max(
        path,
        io_devices=io_devices,
        read_bps=io_read_bps,
        write_bps=io_write_bps,
    )
    _write_control(path / "pids.max", str(tasks_max))


def _cpu_max(percent: int) -> str:
    quota = max(1, round(_CGROUP_PERIOD_US * percent / 100))
    return f"{quota} {_CGROUP_PERIOD_US}"


def _data_device_ids() -> tuple[str, ...]:
    """Return possible io.max device IDs for the data filesystem.

    Some virtio hosts expose an ext4 filesystem on a partition but only accept
    the containing whole-disk device in cgroup ``io.max``. Keep the filesystem
    device first, then fall back to its parent block device when available.
    """
    device = settings.data_dir.stat().st_dev
    filesystem_device = f"{os.major(device)}:{os.minor(device)}"
    candidates = [filesystem_device]
    sysfs_device = Path("/sys/dev/block") / filesystem_device
    try:
        if (sysfs_device / "partition").exists():
            parent_dev = (sysfs_device.resolve().parent / "dev").read_text(
                encoding="utf-8"
            ).strip()
            if re.fullmatch(r"\d+:\d+", parent_dev) and parent_dev not in candidates:
                candidates.append(parent_dev)
    except OSError:
        # sysfs is optional in development; retain the stat-derived device.
        pass
    return tuple(candidates)


def _write_io_max(
    path: Path,
    *,
    io_devices: Sequence[str],
    read_bps: int,
    write_bps: int,
) -> None:
    unsupported: OSError | None = None
    for io_device in io_devices:
        try:
            _write_control(
                path / "io.max",
                f"{io_device} rbps={read_bps} wbps={write_bps}",
            )
            return
        except OSError as exc:
            if exc.errno != errno.ENODEV:
                raise
            unsupported = exc
    if unsupported is not None:
        logger.warning(
            "cgroup io.max is unsupported for %s; retaining io.weight only",
            ", ".join(io_devices),
        )


def _read_control(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _write_control(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


class AgentCgroupScope:
    """One cgroup containing a Codex process and all of its descendants."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._closed = False
        self._close_lock = threading.Lock()

    def wrap_command(self, command: Sequence[str]) -> list[str]:
        if self.path is None:
            return list(command)
        return [
            sys.executable,
            "-m",
            "frontier.cgroup_exec",
            str(self.path),
            "--",
            *command,
        ]

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            if self.path is None:
                self._closed = True
                return
            kill = self.path / "cgroup.kill"
            if kill.exists():
                try:
                    _write_control(kill, "1")
                except OSError:
                    logger.exception("Failed to kill remaining processes in %s", self.path)
            for _attempt in range(20):
                try:
                    self.path.rmdir()
                    self._closed = True
                    return
                except FileNotFoundError:
                    self._closed = True
                    return
                except OSError:
                    time.sleep(0.05)
            logger.warning("Agent cgroup remained populated after cleanup: %s", self.path)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def create_agent_scope(run_id: str) -> AgentCgroupScope:
    initialize_agent_resource_manager()
    if _LAYOUT is None:
        if settings.agent_cgroup_mode == "required":
            detail = _INITIALIZATION_ERROR or "cgroup initialization was not attempted"
            raise AgentResourceUnavailable(f"Agent resource isolation is required: {detail}")
        return AgentCgroupScope(None)

    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)[:48].strip("-.") or "run"
    path = _LAYOUT.agents / f"run-{safe_id}-{uuid.uuid4().hex[:8]}"
    io_devices = _data_device_ids()
    path.mkdir()
    try:
        _write_budget(
            path,
            memory_high=settings.agent_memory_high_bytes,
            memory_max=settings.agent_memory_max_bytes,
            swap_max=settings.agent_memory_swap_max_bytes,
            cpu_percent=settings.agent_cpu_quota_percent,
            io_weight=settings.agent_io_weight,
            io_devices=io_devices,
            io_read_bps=settings.agent_io_read_bps,
            io_write_bps=settings.agent_io_write_bps,
            tasks_max=settings.agent_tasks_max,
        )
        _write_control(path / "memory.oom.group", "1")
    except (OSError, ValueError):
        try:
            path.rmdir()
        except OSError:
            logger.exception("Failed to clean an incomplete Agent cgroup: %s", path)
        raise
    return AgentCgroupScope(path)


def attach_scope_to_process(process: Any, scope: AgentCgroupScope) -> None:
    process._frontier_agent_cgroup_scope = scope


def close_process_scope(process: object | None) -> None:
    if process is None:
        return
    scope = getattr(process, "_frontier_agent_cgroup_scope", None)
    if isinstance(scope, AgentCgroupScope):
        scope.close()
