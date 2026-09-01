from __future__ import annotations

from frontier.config import settings
from frontier.services.outbound_network import (
    agent_environment,
    external_httpx_kwargs,
    outbound_proxy_for,
)


def test_shared_proxy_covers_agents_and_external_http(monkeypatch) -> None:
    monkeypatch.setattr(settings, "outbound_proxy", "http://127.0.0.1:7890")
    monkeypatch.setattr(settings, "api_base_url", "http://127.0.0.1:12101")

    environment = agent_environment({"NO_PROXY": "internal.example"})

    assert environment["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert environment["ALL_PROXY"] == "http://127.0.0.1:7890"
    assert environment["http_proxy"] == "http://127.0.0.1:7890"
    assert {"internal.example", "127.0.0.1", "localhost", "::1"} <= set(
        environment["NO_PROXY"].split(",")
    )
    assert external_httpx_kwargs("https://arxiv.org/api/query") == {
        "proxy": "http://127.0.0.1:7890"
    }


def test_local_frontier_requests_bypass_shared_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "outbound_proxy", "http://127.0.0.1:7890")
    monkeypatch.setattr(settings, "api_base_url", "http://127.0.0.1:12101")

    assert outbound_proxy_for("http://127.0.0.1:12101/api/papers") is None
    assert external_httpx_kwargs("http://localhost:12101/api/health") == {}


def test_agent_environment_does_not_forward_tts_api_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "outbound_proxy", None)

    environment = agent_environment(
        {"FRONTIER_ALIYUN_TTS_API_KEY": "secret-value", "KEEP_ME": "yes"}
    )

    assert "FRONTIER_ALIYUN_TTS_API_KEY" not in environment
    assert environment["KEEP_ME"] == "yes"
