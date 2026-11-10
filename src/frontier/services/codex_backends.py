from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Conversation, TaskRun


@dataclass(frozen=True)
class CodexBackend:
    name: str
    home: Path
    command: str
    profile: str | None = None

    @property
    def fingerprint(self) -> tuple:
        config = self.home / "config.toml"
        profile = self.home / f"{self.profile}.config.toml" if self.profile else config
        return (
            self.name,
            str(self.home),
            self.command,
            self.profile,
            config.stat().st_mtime_ns if config.is_file() else 0,
            profile.stat().st_mtime_ns if profile.is_file() else 0,
        )


def configured_backends() -> tuple[str, dict[str, CodexBackend]]:
    path = settings.codex_backends_file.expanduser()
    if not path.is_file():
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        return "codex", {
            "codex": CodexBackend("codex", home, settings.codex_command, settings.codex_profile)
        }
    try:
        data = json.loads(path.read_text())
        rows = data["backends"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("backends must be a nonempty array")
        backends = {}
        for row in rows:
            name, home = row["name"], row["codex_home"]
            if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in backends:
                raise ValueError("backend names must be unique nonempty strings")
            if not isinstance(home, str) or not home.strip():
                raise ValueError("codex_home must be a nonempty path")
            root = Path(home).expanduser()
            if not root.is_absolute():
                root = path.parent / root
            command = row.get("command", settings.codex_command)
            profile = row.get("profile", settings.codex_profile if name == "codex" else None)
            if (
                not isinstance(command, str)
                or not command
                or (profile is not None and not isinstance(profile, str))
            ):
                raise ValueError("invalid backend command or profile")
            backends[name] = CodexBackend(name, root.resolve(), command, profile)
        default = data.get("default_backend", rows[0]["name"])
        if default not in backends:
            raise ValueError("default_backend must name a configured backend")
        return default, backends
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid codex-backends.json configuration") from exc


def _config(backend: CodexBackend) -> dict[str, Any]:
    with (backend.home / "config.toml").open("rb") as stream:
        config = tomllib.load(stream)
    if backend.profile:
        config.update(config.get("profiles", {}).get(backend.profile, {}))
        profile = backend.home / f"{backend.profile}.config.toml"
        if profile.is_file():
            with profile.open("rb") as stream:
                config.update(tomllib.load(stream))
    return config


def backend_catalog() -> dict:
    default, backends = configured_backends()
    result = []
    for backend in backends.values():
        public: dict[str, Any] = {"name": backend.name, "available": False, "models": []}
        try:
            config = _config(backend)
            model = config.get("model")
            effort = config.get("model_reasoning_effort")
            provider = config.get("model_provider") or "openai"
            catalog_path = Path(
                config.get("model_catalog_json") or backend.home / "models_cache.json"
            ).expanduser()
            if not catalog_path.is_absolute():
                catalog_path = backend.home / catalog_path
            catalog = json.loads(catalog_path.read_text()) if catalog_path.is_file() else {}
            models = catalog.get("models", []) if isinstance(catalog, dict) else catalog
            options = []
            for item in models:
                identifier = item.get("slug") or item.get("model") or item.get("id")
                if not isinstance(identifier, str) or item.get("visibility") in {"hide", "hidden"}:
                    continue
                levels = (
                    item.get("supported_reasoning_levels")
                    or item.get("supportedReasoningEfforts")
                    or []
                )
                efforts = [
                    level.get("effort") if isinstance(level, dict) else level for level in levels
                ]
                efforts = list(dict.fromkeys(level for level in efforts if isinstance(level, str)))
                default_effort = (
                    effort
                    if identifier == model and effort in efforts
                    else item.get("default_reasoning_level") or item.get("defaultReasoningEffort")
                )
                options.append(
                    {
                        "id": identifier,
                        "name": item.get("display_name") or item.get("displayName") or identifier,
                        "reasoning_efforts": efforts,
                        "default_reasoning_effort": default_effort
                        if default_effort in efforts
                        else (efforts[0] if efforts else None),
                    }
                )
            if (
                isinstance(model, str)
                and model
                and not any(item["id"] == model for item in options)
            ):
                options.insert(
                    0,
                    {
                        "id": model,
                        "name": model,
                        "reasoning_efforts": [effort] if isinstance(effort, str) else [],
                        "default_reasoning_effort": effort,
                    },
                )
            if not options:
                raise ValueError("No configured model catalog")
            public.update(
                available=True,
                models=options,
                default_model=model or options[0]["id"],
                provider=provider,
            )
        except (OSError, ValueError, TypeError, AttributeError):
            public["error"] = "后端配置或模型目录不可用"
        result.append(public)
    return {"default_backend": default, "backends": result}


def resolve_selection(value: dict | None = None) -> tuple[CodexBackend, dict]:
    catalog = backend_catalog()
    default, backends = configured_backends()
    value = value or {}
    name = value.get("backend") or default
    backend = backends.get(name)
    route = next((item for item in catalog["backends"] if item["name"] == name), None)
    if backend is None or route is None or not route["available"]:
        raise ValueError("所选 Codex 后端未配置或不可用")
    model = value.get("model") or route["default_model"]
    option = next((item for item in route["models"] if item["id"] == model), None)
    if option is None:
        raise ValueError("所选模型不在该后端的模型目录中")
    effort = value.get("reasoning_effort") or option["default_reasoning_effort"]
    if effort is not None and effort not in option["reasoning_efforts"]:
        raise ValueError("所选模型不支持该思考强度")
    return backend, {"backend": name, "model": model, "reasoning_effort": effort}


def conversation_selection(session: Session, conversation_id: str) -> dict | None:
    value = session.scalar(
        select(TaskRun.payload_json["codex"])
        .where(
            TaskRun.payload_json["conversation_id"].as_string() == conversation_id,
            TaskRun.payload_json["codex"]["backend"].as_string().is_not(None),
        )
        .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
        .limit(1)
    )
    return value if isinstance(value, dict) else None


def prepare_conversation_selection(
    session: Session, conversation: Conversation, requested: dict | None
) -> dict:
    previous = conversation_selection(session, conversation.id)
    _backend, selected = resolve_selection(requested if requested is not None else previous)
    default, _ = configured_backends()
    if selected["backend"] != (previous or {}).get("backend", default):
        # CODEX_HOME owns its thread files. Recover visible history in the new backend;
        # never ask another backend to resume an unrelated private thread.
        conversation.codex_session_id = None
    return selected
