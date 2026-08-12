from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from time import perf_counter
from typing import Any, Literal

from ..config import PROJECT_ROOT, settings
from .translation_provider import load_translation_provider_config, stream_translation_prompt

ServiceName = Literal["codex", "translation"]
ServiceStatus = Literal["ok", "error", "disabled"]


def _result(
    service: ServiceName,
    status: ServiceStatus,
    started_at: float,
    message: str,
) -> dict[str, str | bool | int]:
    return {
        "service": service,
        "ok": status == "ok",
        "status": status,
        "latency_ms": max(0, round((perf_counter() - started_at) * 1000)),
        "message": message,
    }


def _public_error(error: BaseException | str, *, secrets: tuple[str, ...] = ()) -> str:
    """Return a concise diagnostic without exposing provider credentials."""
    detail = " ".join(str(error).split())
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "***")
    detail = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1***", detail)
    detail = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "***", detail)
    if len(detail) > 400:
        detail = f"{detail[:399]}…"
    return detail or "未知错误"


def _environment_secrets(environment: dict[str, str]) -> tuple[str, ...]:
    sensitive_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    return tuple(
        value
        for key, value in environment.items()
        if len(value) >= 6 and any(marker in key.upper() for marker in sensitive_markers)
    )


def _codex_command() -> list[str]:
    command = [
        settings.codex_command,
        "exec",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "-C",
        str(PROJECT_ROOT),
        "-s",
        "read-only",
    ]
    if settings.codex_profile:
        command.extend(["--profile", settings.codex_profile])
    command.append("Reply with exactly OK. Do not use tools.")
    return command


def _has_codex_response(stdout: str) -> bool:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and str(item.get("text") or "").strip()
        ):
            return True
    return False


def check_codex_service(timeout_seconds: float = 30) -> dict[str, str | bool | int]:
    started_at = perf_counter()
    if not settings.enable_codex_runs:
        return _result(
            "codex",
            "disabled",
            started_at,
            "Codex Agent 已在服务器配置中停用",
        )
    environment = os.environ.copy()
    environment["FRONTIER_API_BASE_URL"] = settings.api_base_url
    environment["UV_CACHE_DIR"] = str(settings.task_dir / "uv-cache")
    try:
        completed = subprocess.run(
            _codex_command(),
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return _result(
            "codex",
            "error",
            started_at,
            "未找到 Codex 可执行文件，请检查服务器上的 FRONTIER_CODEX_COMMAND",
        )
    except subprocess.TimeoutExpired:
        return _result(
            "codex",
            "error",
            started_at,
            "Codex 检测超时，请检查登录状态和网络连接",
        )
    except OSError as exc:
        return _result(
            "codex",
            "error",
            started_at,
            f"无法启动 Codex：{_public_error(exc, secrets=_environment_secrets(environment))}",
        )

    if completed.returncode != 0:
        detail = _public_error(
            completed.stderr or completed.stdout,
            secrets=_environment_secrets(environment),
        )
        return _result(
            "codex",
            "error",
            started_at,
            f"Codex 调用失败（退出码 {completed.returncode}）：{detail}",
        )
    if not _has_codex_response(completed.stdout):
        return _result(
            "codex",
            "error",
            started_at,
            "Codex 已退出，但没有返回模型响应",
        )
    return _result("codex", "ok", started_at, "Codex 可正常调用")


def check_translation_service(timeout_seconds: float = 15) -> dict[str, str | bool | int]:
    started_at = perf_counter()
    secret = ""
    try:
        secret = load_translation_provider_config().api_key
        content = "".join(
            stream_translation_prompt(
                "将 hello 翻译为中文，只回复译文。",
                max_tokens=8,
                timeout_seconds=timeout_seconds,
            )
        ).strip()
        if not content:
            raise RuntimeError("翻译模型没有返回文本")
    except Exception as exc:  # noqa: BLE001 - diagnostics must normalize provider failures
        detail = _public_error(exc, secrets=(secret,))
        return _result(
            "translation",
            "error",
            started_at,
            f"翻译服务不可用：{detail}",
        )
    return _result("translation", "ok", started_at, "翻译服务可正常调用")


SERVICE_CHECKS: dict[ServiceName, Callable[[], dict[str, str | bool | int]]] = {
    "codex": check_codex_service,
    "translation": check_translation_service,
}


def check_service(service: ServiceName) -> dict[str, Any]:
    return SERVICE_CHECKS[service]()
