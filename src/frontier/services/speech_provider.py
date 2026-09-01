from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

import httpx

from ..config import settings
from .outbound_network import external_httpx_kwargs


class SpeechProviderError(RuntimeError):
    """A safe, user-facing speech synthesis failure.

    Error messages deliberately exclude request headers, response bodies, and
    transport exception text because any of those can contain credentials.
    """


@dataclass(frozen=True)
class SpeechProviderConfig:
    api_key: str = field(repr=False)
    endpoint: str
    model: str
    voice: str
    instructions: str | None = None
    timeout_seconds: float = 120
    max_retries: int = 2
    audio_max_bytes: int = 32 * 1024**2


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio_bytes: bytes
    request_id: str | None = None
    audio_id: str | None = None
    input_characters: int | None = None


@runtime_checkable
class SpeechProvider(Protocol):
    provider_name: str
    model: str
    voice: str

    def synthesize(self, text: str) -> SynthesizedSpeech:
        """Synthesize one provider-sized chunk into a complete WAV file."""


def load_speech_provider_config() -> SpeechProviderConfig:
    api_key = (settings.aliyun_tts_api_key or "").strip()
    if not api_key:
        raise SpeechProviderError(
            "未配置阿里云语音密钥，请在 .env 中填写 "
            "FRONTIER_ALIYUN_TTS_API_KEY"
        )
    endpoint = settings.aliyun_tts_endpoint.strip()
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.hostname
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
    ):
        raise SpeechProviderError("阿里云语音 endpoint 必须是有效的 HTTPS URL")
    model = settings.aliyun_tts_model.strip()
    voice = settings.aliyun_tts_voice.strip()
    if not model or not voice:
        raise SpeechProviderError("阿里云语音 model 和 voice 不能为空")
    return SpeechProviderConfig(
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        voice=voice,
        instructions=settings.aliyun_tts_instructions.strip() or None,
        timeout_seconds=settings.aliyun_tts_timeout_seconds,
        max_retries=settings.aliyun_tts_max_retries,
        audio_max_bytes=settings.aliyun_tts_audio_max_bytes,
    )


class AliyunSpeechProvider:
    """Alibaba Cloud Model Studio non-streaming Qwen3-TTS provider."""

    provider_name = "aliyun"
    _retryable_statuses = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        config: SpeechProviderConfig | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or load_speech_provider_config()
        self.model = self.config.model
        self.voice = self.config.voice
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(self.config.timeout_seconds, connect=min(10, self.config.timeout_seconds)),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            follow_redirects=False,
            **external_httpx_kwargs(self.config.endpoint),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def synthesize(self, text: str) -> SynthesizedSpeech:
        normalized = text.strip()
        if not normalized:
            raise SpeechProviderError("语音合成文本不能为空")
        if len(normalized) >= 600:
            raise SpeechProviderError("单次 Qwen3-TTS 请求必须少于 600 个字符")

        audio_url, metadata = self._request_audio(normalized)
        audio_bytes = self._download_audio(audio_url)
        return SynthesizedSpeech(
            audio_bytes=audio_bytes,
            request_id=metadata.get("request_id"),
            audio_id=metadata.get("audio_id"),
            input_characters=metadata.get("input_characters"),
        )

    def _request_audio(self, text: str) -> tuple[str, dict[str, str | int | None]]:
        request_input: dict[str, str | bool] = {
            "text": text,
            "voice": self.config.voice,
            "language_type": "Auto",
        }
        if self.config.instructions:
            request_input["instructions"] = self.config.instructions
            request_input["optimize_instructions"] = True
        payload = {"model": self.config.model, "input": request_input}
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = self._retry_request(
            lambda: self._client.post(
                self.config.endpoint,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(self.config.timeout_seconds),
                follow_redirects=False,
            ),
            operation="语音合成",
        )
        try:
            body = response.json()
            output = body["output"]
            audio = output["audio"]
            audio_url = audio["url"]
        except (ValueError, KeyError, TypeError):
            raise SpeechProviderError("阿里云语音响应缺少有效的音频 URL") from None
        if not isinstance(audio_url, str) or not audio_url.strip():
            raise SpeechProviderError("阿里云语音响应缺少有效的音频 URL")

        usage = body.get("usage") if isinstance(body, dict) else None
        characters = usage.get("characters") if isinstance(usage, dict) else None
        metadata: dict[str, str | int | None] = {
            "request_id": _safe_identifier(body.get("request_id")),
            "audio_id": _safe_identifier(audio.get("id")),
            "input_characters": characters if isinstance(characters, int) else None,
        }
        return audio_url, metadata

    def _download_audio(self, audio_url: str) -> bytes:
        _validate_aliyun_audio_url(audio_url)
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._client.stream(
                    "GET",
                    audio_url,
                    headers={"Accept": "audio/wav, audio/*;q=0.9"},
                    timeout=httpx.Timeout(self.config.timeout_seconds),
                    follow_redirects=False,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        if (
                            response.status_code not in self._retryable_statuses
                            or attempt >= self.config.max_retries
                        ):
                            raise SpeechProviderError(
                                f"阿里云音频下载失败（HTTP {response.status_code}）"
                            )
                        delay = _retry_delay(attempt, response.headers.get("retry-after"))
                    else:
                        declared_size = _content_length(response.headers.get("content-length"))
                        if declared_size > self.config.audio_max_bytes:
                            raise SpeechProviderError("阿里云返回的音频超过大小限制")
                        content = bytearray()
                        for block in response.iter_bytes():
                            content.extend(block)
                            if len(content) > self.config.audio_max_bytes:
                                raise SpeechProviderError(
                                    "阿里云返回的音频超过大小限制"
                                )
                        if not content:
                            raise SpeechProviderError("阿里云返回的音频为空")
                        return bytes(content)
            except SpeechProviderError:
                raise
            except httpx.HTTPError as exc:
                if attempt >= self.config.max_retries:
                    raise SpeechProviderError(
                        f"阿里云音频下载网络失败（{type(exc).__name__}）"
                    ) from None
                delay = _retry_delay(attempt, None)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _retry_request(
        self,
        request: Callable[[], httpx.Response],
        *,
        operation: str,
    ) -> httpx.Response:
        for attempt in range(self.config.max_retries + 1):
            try:
                response = request()
            except httpx.HTTPError as exc:
                if attempt >= self.config.max_retries:
                    raise SpeechProviderError(
                        f"阿里云{operation}网络失败（{type(exc).__name__}）"
                    ) from None
                self._sleep(_retry_delay(attempt, None))
                continue

            if 200 <= response.status_code < 300:
                return response
            if (
                response.status_code not in self._retryable_statuses
                or attempt >= self.config.max_retries
            ):
                request_id = _safe_identifier(response.headers.get("x-request-id"))
                suffix = f"，请求 ID {request_id}" if request_id else ""
                raise SpeechProviderError(
                    f"阿里云{operation}失败（HTTP {response.status_code}{suffix}）"
                )
            self._sleep(_retry_delay(attempt, response.headers.get("retry-after")))
        raise AssertionError("unreachable")


def _validate_aliyun_audio_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname.endswith(".aliyuncs.com")
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SpeechProviderError("阿里云返回了不受信任的音频下载地址")


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", stripped):
        return None
    return stripped


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0), 10)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 8)


def _content_length(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(int(value), 0)
    except ValueError:
        return 0
