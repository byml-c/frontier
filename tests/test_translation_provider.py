from __future__ import annotations

import pytest

from frontier.config import settings
from frontier.services import translation_provider
from frontier.services.translation_provider import (
    TranslationProviderError,
    load_translation_provider_config,
)


def test_translation_provider_reads_dedicated_openai_compatible_config(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "translation.env"
    path.write_text(
        "OPENAI_API_KEY=test-secret\n"
        "BASE_URL=https://translation.example/v1\n"
        "MODEL=translation-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "translation_env_file", path)
    config = load_translation_provider_config()
    assert config.api_key == "test-secret"
    assert config.model == "translation-model"
    assert config.chat_completions_url == "https://translation.example/v1/chat/completions"


def test_translation_request_disables_thinking_for_low_latency(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_text(self):
            yield 'data: {"choices":[{"delta":{"content":"译文"}}]}\n'
            yield "data: [DONE]\n"

    class Client:
        def stream(self, *_args, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    config = translation_provider.TranslationProviderConfig(
        api_key="secret",
        base_url="https://translation.example/v1",
        model="qwen-flash",
    )
    monkeypatch.setattr(translation_provider, "load_translation_provider_config", lambda: config)
    monkeypatch.setattr(translation_provider, "_client_for", lambda _config: Client())
    assert (
        "".join(
            translation_provider.stream_translation_prompt(
                "translate", max_tokens=8, timeout_seconds=3
            )
        )
        == "译文"
    )
    assert captured["enable_thinking"] is False
    assert captured["stream"] is True
    assert captured["max_tokens"] == 8


def test_translation_stream_enforces_a_total_deadline(monkeypatch) -> None:
    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_text(self):
            yield 'data: {"choices":[{"delta":{"content":"迟到的译文"}}]}\n'

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    config = translation_provider.TranslationProviderConfig(
        api_key="secret",
        base_url="https://translation.example/v1",
        model="translation-model",
    )
    clock = iter([10.0, 12.1])
    monkeypatch.setattr(translation_provider, "load_translation_provider_config", lambda: config)
    monkeypatch.setattr(translation_provider, "_client_for", lambda _config: Client())
    monkeypatch.setattr(translation_provider.time, "monotonic", lambda: next(clock))

    with pytest.raises(TranslationProviderError, match="超过总时限"):
        list(translation_provider.stream_translation_prompt("translate", timeout_seconds=2))
