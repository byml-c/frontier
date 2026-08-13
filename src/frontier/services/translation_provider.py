from __future__ import annotations

import json
import threading
from atexit import register
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import settings
from .outbound_network import external_httpx_kwargs, outbound_proxy_for


class TranslationProviderError(RuntimeError):
    pass


_provider_lock = threading.Lock()
_cached_config: tuple[Path, int, int, TranslationProviderConfig] | None = None
_http_client: httpx.Client | None = None
_http_client_key: tuple[str, str, str | None] | None = None


@dataclass(frozen=True)
class TranslationProviderConfig:
    api_key: str
    base_url: str
    model: str

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise TranslationProviderError(f"翻译模型配置不存在：{path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_translation_provider_config() -> TranslationProviderConfig:
    global _cached_config
    path = settings.translation_env_file
    try:
        stat = path.stat()
    except OSError as exc:
        raise TranslationProviderError(f"翻译模型配置不存在：{path}") from exc
    with _provider_lock:
        if (
            _cached_config
            and _cached_config[0] == path
            and _cached_config[1] == stat.st_mtime_ns
            and _cached_config[2] == stat.st_size
        ):
            return _cached_config[3]
    values = _read_env_file(settings.translation_env_file)
    missing = [key for key in ("OPENAI_API_KEY", "BASE_URL", "MODEL") if not values.get(key)]
    if missing:
        raise TranslationProviderError(
            f"请在 {settings.translation_env_file} 中填写：{', '.join(missing)}"
        )
    base_url = values["BASE_URL"]
    if not base_url.startswith(("http://", "https://")):
        raise TranslationProviderError("翻译模型 BASE_URL 必须以 http:// 或 https:// 开头")
    config = TranslationProviderConfig(
        api_key=values["OPENAI_API_KEY"],
        base_url=base_url,
        model=values["MODEL"],
    )
    with _provider_lock:
        _cached_config = (path, stat.st_mtime_ns, stat.st_size, config)
    return config


def _client_for(config: TranslationProviderConfig) -> httpx.Client:
    global _http_client, _http_client_key
    key = (config.base_url, config.api_key, outbound_proxy_for(config.base_url))
    with _provider_lock:
        if _http_client is None or _http_client_key != key:
            if _http_client is not None:
                _http_client.close()
            _http_client = httpx.Client(
                timeout=httpx.Timeout(60, connect=8),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"Accept": "text/event-stream"},
                **external_httpx_kwargs(config.base_url),
            )
            _http_client_key = key
        return _http_client


def warm_translation_provider() -> bool:
    """Preload the small config and reusable HTTP client without making a model request."""
    try:
        _client_for(load_translation_provider_config())
    except TranslationProviderError:
        return False
    return True


def close_translation_provider() -> None:
    global _http_client, _http_client_key
    with _provider_lock:
        if _http_client is not None:
            _http_client.close()
        _http_client = None
        _http_client_key = None


register(close_translation_provider)


def stream_translation_prompt(
    prompt: str,
    *,
    max_tokens: int = 256,
    timeout_seconds: float | None = None,
) -> Iterator[str]:
    config = load_translation_provider_config()
    client = _client_for(config)
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": "你是严谨的学术翻译助手，只完成用户要求的翻译任务。",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if "qwen" in config.model.lower() or "dashscope.aliyuncs.com" in config.base_url.lower():
        # Qwen hybrid models otherwise spend time generating hidden reasoning for
        # a deterministic translation task. Do not send this provider extension to
        # unrelated OpenAI-compatible services that may reject unknown fields.
        payload["enable_thinking"] = False
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    emitted = False
    try:
        request_options = {}
        if timeout_seconds is not None:
            request_options["timeout"] = httpx.Timeout(timeout_seconds)
        with client.stream(
            "POST",
            config.chat_completions_url,
            headers=headers,
            json=payload,
            **request_options,
        ) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", errors="replace")
                raise TranslationProviderError(
                    f"翻译模型请求失败（{response.status_code}）：{body[:500]}"
                )
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                    delta = event.get("choices", [{}])[0].get("delta", {}).get("content")
                except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
                    continue
                if isinstance(delta, str) and delta:
                    emitted = True
                    yield delta
            if not emitted:
                raise TranslationProviderError("翻译模型已响应，但没有返回可显示的文本")
    except httpx.HTTPError as exc:
        raise TranslationProviderError(f"无法连接翻译模型：{exc}") from exc
