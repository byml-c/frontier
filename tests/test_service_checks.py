from __future__ import annotations

import json
import subprocess

import httpx
from fastapi.testclient import TestClient

from frontier.api import system as system_api
from frontier.config import settings
from frontier.services import service_checks
from frontier.services.translation_provider import TranslationProviderConfig


def test_service_check_api_returns_structured_result(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        system_api,
        "check_service",
        lambda service: {
            "service": service,
            "ok": True,
            "status": "ok",
            "latency_ms": 12,
            "message": "服务可用",
        },
    )

    response = client.post("/api/service-checks/codex")

    assert response.status_code == 200
    payload = response.json()
    assert payload.pop("checked_at")
    assert payload == {
        "service": "codex",
        "ok": True,
        "status": "ok",
        "latency_ms": 12,
        "message": "服务可用",
    }
    latest = client.get("/api/service-checks")
    assert latest.status_code == 200
    assert latest.json()["codex"]["status"] == "ok"
    assert latest.json()["codex"]["checked_at"]
    assert "arxiv" in latest.json()
    assert client.post("/api/service-checks/unknown").status_code == 422


def test_codex_check_runs_a_real_ephemeral_turn(monkeypatch) -> None:
    captured: dict = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "OK"},
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

    monkeypatch.setattr(settings, "enable_codex_runs", True)
    monkeypatch.setattr(service_checks.subprocess, "run", run)

    result = service_checks.check_codex_service()

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["latency_ms"] >= 0
    assert "--ephemeral" in captured["command"]
    assert "read-only" in captured["command"]
    assert captured["timeout"] == 30


def test_codex_check_reports_disabled_without_starting_process(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_codex_runs", False)

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("disabled Codex must not be started")

    monkeypatch.setattr(service_checks.subprocess, "run", unexpected_run)

    result = service_checks.check_codex_service()

    assert result["ok"] is False
    assert result["status"] == "disabled"


def test_codex_check_redacts_environment_credentials(monkeypatch) -> None:
    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="authentication rejected env-secret-value",
        )

    monkeypatch.setattr(settings, "enable_codex_runs", True)
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret-value")
    monkeypatch.setattr(service_checks.subprocess, "run", run)

    result = service_checks.check_codex_service()

    assert result["ok"] is False
    assert "env-secret-value" not in result["message"]
    assert "***" in result["message"]


def test_translation_check_uses_short_request_and_redacts_secret(monkeypatch) -> None:
    config = TranslationProviderConfig(
        api_key="test-super-secret",
        base_url="https://translation.example/v1",
        model="translation-model",
    )
    captured: dict = {}

    def fail_stream(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        raise RuntimeError("Bearer test-super-secret rejected")
        yield  # pragma: no cover

    monkeypatch.setattr(service_checks, "load_translation_provider_config", lambda: config)
    monkeypatch.setattr(service_checks, "stream_translation_prompt", fail_stream)

    result = service_checks.check_translation_service()

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "test-super-secret" not in result["message"]
    assert "Bearer ***" in result["message"]
    assert captured["max_tokens"] == 8
    assert captured["timeout_seconds"] == 15


def test_arxiv_check_uses_collection_endpoint_and_validates_atom(monkeypatch) -> None:
    captured: dict = {}

    def get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(
            200,
            headers={"content-type": "application/atom+xml"},
            content=b"""<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <title>arXiv Query</title>
              <entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention</title></entry>
            </feed>""",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(service_checks.httpx, "get", get)

    result = service_checks.check_arxiv_service()

    assert result["ok"] is True
    assert captured["url"] == "https://export.arxiv.org/api/query"
    assert captured["params"]["max_results"] == 1
    assert captured["timeout"] == 15


def test_arxiv_check_reports_invalid_response(monkeypatch) -> None:
    def get(url, **_kwargs):
        return httpx.Response(200, text="not an atom feed", request=httpx.Request("GET", url))

    monkeypatch.setattr(service_checks.httpx, "get", get)

    result = service_checks.check_arxiv_service()

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "arXiv 数据接口不可用" in result["message"]
