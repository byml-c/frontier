from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import feedparser
import httpx

from ..config import PROJECT_ROOT, settings
from .ingest import ARXIV_API, USER_AGENT
from .outbound_network import agent_environment, external_httpx_kwargs
from .translation_provider import load_translation_provider_config, stream_translation_prompt

ServiceName = Literal["codex", "translation", "arxiv"]
ServiceStatus = Literal["ok", "error", "disabled"]


def _result(
    service: ServiceName,
    status: ServiceStatus,
    started_at: float,
    message: str,
) -> dict[str, Any]:
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


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _codex_runtime_config(environment: dict[str, str]) -> dict[str, str]:
    """Resolve the model settings that the service-check command will use."""
    home = Path(environment.get("CODEX_HOME") or Path(environment.get("HOME", "~")).expanduser() / ".codex")
    base = _load_toml(home / "config.toml")
    resolved: dict[str, Any] = dict(base)
    profile = settings.codex_profile
    if profile:
        inline_profiles = base.get("profiles")
        if isinstance(inline_profiles, dict) and isinstance(inline_profiles.get(profile), dict):
            resolved.update(inline_profiles[profile])
        resolved.update(_load_toml(home / f"{profile}.config.toml"))
    result: dict[str, str] = {}
    model = resolved.get("model")
    reasoning_effort = resolved.get("model_reasoning_effort")
    if isinstance(model, str) and model.strip():
        result["model"] = model.strip()
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        result["reasoning_effort"] = reasoning_effort.strip()
    return result


def _codex_command(runtime: dict[str, str] | None = None) -> list[str]:
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
    runtime = runtime or {}
    if runtime.get("model"):
        command.extend(["--model", runtime["model"]])
    if runtime.get("reasoning_effort"):
        command.extend(
            ["--config", f'model_reasoning_effort="{runtime["reasoning_effort"]}"']
        )
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


def check_codex_service(timeout_seconds: float = 30) -> dict[str, Any]:
    started_at = perf_counter()
    if not settings.enable_codex_runs:
        return _result(
            "codex",
            "disabled",
            started_at,
            "Codex Agent 已在服务器配置中停用",
        )
    environment = agent_environment()
    runtime = _codex_runtime_config(environment)
    try:
        completed = subprocess.run(
            _codex_command(runtime),
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
    return {
        **_result("codex", "ok", started_at, "Codex 可正常调用"),
        **runtime,
    }


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


def check_arxiv_service(timeout_seconds: float = 15) -> dict[str, str | bool | int]:
    """Exercise the same Atom endpoint used by daily paper collection."""
    started_at = perf_counter()
    try:
        response = httpx.get(
            ARXIV_API,
            params={
                "search_query": "id:1706.03762",
                "start": 0,
                "max_results": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
            follow_redirects=True,
            **external_httpx_kwargs(ARXIV_API),
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if getattr(feed, "bozo", False):
            raise RuntimeError(f"Atom 响应无法解析：{feed.bozo_exception}")
        if not feed.entries:
            raise RuntimeError("arXiv API 已响应，但没有返回验证论文")
    except Exception as exc:  # noqa: BLE001 - diagnostics normalize all network failures
        return _result(
            "arxiv",
            "error",
            started_at,
            f"arXiv 数据接口不可用：{_public_error(exc)}",
        )
    return _result("arxiv", "ok", started_at, "arXiv 数据接口可正常访问")


SERVICE_CHECKS: dict[ServiceName, Callable[[], dict[str, str | bool | int]]] = {
    "codex": check_codex_service,
    "translation": check_translation_service,
    "arxiv": check_arxiv_service,
}


def check_service(service: ServiceName) -> dict[str, Any]:
    return SERVICE_CHECKS[service]()
