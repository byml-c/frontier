from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from frontier.services import speech_provider
from frontier.services.podcast_audio import (
    PodcastAudioError,
    chunk_speech_text,
    render_podcast_audio,
)
from frontier.services.speech_provider import (
    AliyunSpeechProvider,
    SpeechProviderConfig,
    SpeechProviderError,
    SynthesizedSpeech,
)

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
AUDIO_URL = "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/generated.wav?Signature=x"


def _wav_bytes(*, frames: int = 8, frame_rate: int = 8) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


def _config(**overrides: object) -> SpeechProviderConfig:
    values: dict[str, object] = {
        "api_key": "sk-private-value",
        "endpoint": API_URL,
        "model": "qwen3-tts-instruct-flash",
        "voice": "Cherry",
        "instructions": "自然清晰地朗读",
        "timeout_seconds": 2,
        "max_retries": 2,
        "audio_max_bytes": 1024 * 1024,
    }
    values.update(overrides)
    return SpeechProviderConfig(**values)  # type: ignore[arg-type]


def test_aliyun_provider_posts_then_downloads_complete_audio() -> None:
    wav_audio = _wav_bytes()
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.headers["authorization"] == "Bearer sk-private-value"
            payload = json.loads(request.content)
            assert payload == {
                "model": "qwen3-tts-instruct-flash",
                "input": {
                    "text": "今天我们讲一篇论文。",
                    "voice": "Cherry",
                    "language_type": "Auto",
                    "instructions": "自然清晰地朗读",
                    "optimize_instructions": True,
                },
            }
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "output": {"audio": {"url": AUDIO_URL, "id": "audio-1"}},
                    "usage": {"characters": 11},
                },
            )
        assert str(request.url) == AUDIO_URL
        assert "authorization" not in request.headers
        return httpx.Response(200, content=wav_audio, headers={"content-type": "audio/wav"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = AliyunSpeechProvider(_config(), client=client, sleep=lambda _delay: None)
        result = provider.synthesize("今天我们讲一篇论文。")

    assert result.audio_bytes == wav_audio
    assert result.request_id == "request-1"
    assert result.audio_id == "audio-1"
    assert result.input_characters == 11
    assert [request.method for request in requests] == ["POST", "GET"]


def test_aliyun_provider_retries_rate_limit_without_resynthesizing_download() -> None:
    attempts = {"post": 0, "get": 0}
    delays: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            attempts["post"] += 1
            if attempts["post"] == 1:
                return httpx.Response(429, headers={"retry-after": "0"})
            return httpx.Response(200, json={"output": {"audio": {"url": AUDIO_URL}}})
        attempts["get"] += 1
        if attempts["get"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=_wav_bytes())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = AliyunSpeechProvider(_config(), client=client, sleep=delays.append)
        provider.synthesize("限流重试。")

    assert attempts == {"post": 2, "get": 2}
    assert delays == [0.0, 0.5]


def test_aliyun_provider_reports_non_retryable_failure_without_secret() -> None:
    secret = "sk-do-not-leak"

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"invalid key: {secret}")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = AliyunSpeechProvider(
            _config(api_key=secret),
            client=client,
            sleep=lambda _delay: None,
        )
        with pytest.raises(SpeechProviderError) as caught:
            provider.synthesize("请求失败。")

    assert "HTTP 400" in str(caught.value)
    assert secret not in str(caught.value)
    assert secret not in repr(provider.config)


def test_missing_api_key_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech_provider.settings, "aliyun_tts_api_key", None)

    with pytest.raises(SpeechProviderError, match="FRONTIER_ALIYUN_TTS_API_KEY"):
        speech_provider.load_speech_provider_config()


def test_provider_rejects_non_aliyun_audio_download_url() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"output": {"audio": {"url": "http://127.0.0.1/private"}}},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = AliyunSpeechProvider(_config(), client=client)
        with pytest.raises(SpeechProviderError, match="不受信任"):
            provider.synthesize("防止 SSRF。")


class _FakeProvider:
    provider_name = "fake"
    model = "fake-tts"
    voice = "narrator"

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at

    def synthesize(self, text: str) -> SynthesizedSpeech:
        index = len(self.calls)
        self.calls.append(text)
        if self.fail_at == index:
            raise SpeechProviderError("模拟分段失败")
        return SynthesizedSpeech(
            audio_bytes=_wav_bytes(),
            request_id=f"request-{index}",
            input_characters=len(text),
        )


def test_chunker_respects_paragraph_sentences_and_hard_limit() -> None:
    text = "第一句。第二句！\n\n" + "很长的内容，" * 30

    chunks = chunk_speech_text(text, max_chars=50)

    assert chunks[0] == "第一句。 第二句！"
    assert len(chunks) > 2
    assert all(0 < len(chunk) <= 50 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_render_combines_wav_chunks_and_replaces_target_atomically(tmp_path: Path) -> None:
    target = tmp_path / "daily.wav"
    target.write_bytes(b"old")
    provider = _FakeProvider()

    result = render_podcast_audio(
        "第一段。\n\n第二段。",
        target,
        provider=provider,
        max_chars=50,
    )

    assert provider.calls == ["第一段。", "第二段。"]
    assert result.path == target
    assert result.provider == "fake"
    assert result.duration_seconds == 2.0
    assert result.size_bytes == target.stat().st_size
    assert result.content_hash
    assert len(result.manifest["chunks"]) == 2
    with wave.open(str(target), "rb") as reader:
        assert reader.getnframes() == 16
    assert not list(tmp_path.glob("*.rendering"))
    assert not list(tmp_path.glob(".*.rendering"))


def test_render_failure_preserves_existing_target_and_removes_temp(tmp_path: Path) -> None:
    target = tmp_path / "daily.wav"
    target.write_bytes(b"previous-good-audio")
    provider = _FakeProvider(fail_at=1)

    with pytest.raises(SpeechProviderError, match="模拟分段失败"):
        render_podcast_audio(
            "第一段。\n\n第二段。",
            target,
            provider=provider,
            max_chars=50,
        )

    assert target.read_bytes() == b"previous-good-audio"
    assert not list(tmp_path.glob(".*.rendering"))


def test_render_rejects_misleading_mp3_extension(tmp_path: Path) -> None:
    with pytest.raises(PodcastAudioError, match="ffmpeg"):
        render_podcast_audio("文字稿", tmp_path / "daily.mp3", provider=_FakeProvider())
