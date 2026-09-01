from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings
from .speech_provider import AliyunSpeechProvider, SpeechProvider, SpeechProviderError


class PodcastAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioRenderResult:
    path: Path
    content_hash: str
    size_bytes: int
    duration_seconds: float
    provider: str
    model: str
    voice: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class _WaveChunk:
    channels: int
    sample_width: int
    frame_rate: int
    compression_type: str
    frames: bytes
    frame_count: int


def chunk_speech_text(text: str, *, max_chars: int = 500) -> list[str]:
    """Split speech at stable paragraph/sentence boundaries below the API limit."""
    if max_chars < 1 or max_chars >= 600:
        raise ValueError("max_chars must be between 1 and 599")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    paragraphs = [
        re.sub(r"[ \t\f\v]+", " ", paragraph.replace("\n", " ")).strip()
        for paragraph in re.split(r"\n\s*\n", normalized)
    ]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        units = [part.strip() for part in _sentence_parts(paragraph) if part.strip()]
        current = ""
        for unit in units:
            for piece in _split_oversized(unit, max_chars=max_chars):
                candidate = f"{current} {piece}".strip() if current else piece
                if len(candidate) <= max_chars:
                    current = candidate
                    continue
                if current:
                    chunks.append(current)
                current = piece
        if current:
            chunks.append(current)
    return chunks


def render_podcast_audio(
    script_text: str,
    target_path: Path,
    *,
    provider: SpeechProvider | None = None,
    max_chars: int | None = None,
) -> AudioRenderResult:
    """Render a script to one WAV file and atomically replace the target.

    Every text chunk is independently synthesized, so provider throttling and
    transient failures are retried locally without discarding earlier chunks.
    The final target is untouched unless all chunks form one compatible WAV.
    """
    target = Path(target_path)
    if target.suffix.lower() != ".wav":
        raise PodcastAudioError("当前渲染器仅支持 WAV 输出；MP3 转码需要另行配置 ffmpeg")
    chunks = chunk_speech_text(
        script_text,
        max_chars=max_chars or settings.aliyun_tts_chunk_max_chars,
    )
    if not chunks:
        raise PodcastAudioError("播客文字稿不能为空")

    owned_provider = provider is None
    speech_provider = provider or AliyunSpeechProvider()
    wave_chunks: list[_WaveChunk] = []
    manifest_chunks: list[dict[str, Any]] = []
    try:
        for index, chunk in enumerate(chunks):
            synthesized = speech_provider.synthesize(chunk)
            wave_chunk = _read_wave_chunk(synthesized.audio_bytes, index=index)
            wave_chunks.append(wave_chunk)
            manifest_chunks.append(
                {
                    "index": index,
                    "textHash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    "characters": len(chunk),
                    "requestId": synthesized.request_id,
                    "audioId": synthesized.audio_id,
                    "billedCharacters": synthesized.input_characters,
                }
            )
        _validate_compatible_wave_chunks(wave_chunks)
        content_hash, size_bytes = _write_wave_atomically(target, wave_chunks)
    except (SpeechProviderError, PodcastAudioError):
        raise
    except (OSError, wave.Error, ValueError) as exc:
        raise PodcastAudioError(f"播客音频渲染失败：{type(exc).__name__}") from exc
    finally:
        if owned_provider and isinstance(speech_provider, AliyunSpeechProvider):
            speech_provider.close()

    first = wave_chunks[0]
    total_frames = sum(chunk.frame_count for chunk in wave_chunks)
    duration_seconds = total_frames / first.frame_rate
    manifest = {
        "version": 1,
        "format": "wav",
        "chunkMaxCharacters": max_chars or settings.aliyun_tts_chunk_max_chars,
        "sampleRate": first.frame_rate,
        "channels": first.channels,
        "sampleWidthBytes": first.sample_width,
        "chunks": manifest_chunks,
    }
    return AudioRenderResult(
        path=target,
        content_hash=content_hash,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        provider=speech_provider.provider_name,
        model=speech_provider.model,
        voice=speech_provider.voice,
        manifest=manifest,
    )


def _sentence_parts(paragraph: str) -> list[str]:
    # Chinese punctuation is a boundary even without following whitespace;
    # an English full stop is a boundary only when followed by whitespace.
    return re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+", paragraph)


def _split_oversized(text: str, *, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        search = remaining[: max_chars + 1]
        split_at = max(
            (search.rfind(delimiter) + 1 for delimiter in ("，", ",", "：", ":", "、", " ")),
            default=0,
        )
        if split_at < max_chars // 2:
            split_at = max_chars
        piece = remaining[:split_at].strip()
        if not piece:
            piece = remaining[:max_chars]
            split_at = max_chars
        pieces.append(piece)
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _read_wave_chunk(audio_bytes: bytes, *, index: int) -> _WaveChunk:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            if reader.getcomptype() != "NONE":
                raise PodcastAudioError(f"第 {index + 1} 个音频分段不是未压缩 PCM WAV")
            frame_count = reader.getnframes()
            if frame_count <= 0 or reader.getframerate() <= 0:
                raise PodcastAudioError(f"第 {index + 1} 个音频分段为空")
            frames = reader.readframes(frame_count)
            return _WaveChunk(
                channels=reader.getnchannels(),
                sample_width=reader.getsampwidth(),
                frame_rate=reader.getframerate(),
                compression_type=reader.getcomptype(),
                frames=frames,
                frame_count=frame_count,
            )
    except (EOFError, wave.Error) as exc:
        raise PodcastAudioError(f"第 {index + 1} 个音频分段不是有效的 WAV") from exc


def _validate_compatible_wave_chunks(chunks: list[_WaveChunk]) -> None:
    expected = (
        chunks[0].channels,
        chunks[0].sample_width,
        chunks[0].frame_rate,
        chunks[0].compression_type,
    )
    for index, chunk in enumerate(chunks[1:], start=1):
        actual = (
            chunk.channels,
            chunk.sample_width,
            chunk.frame_rate,
            chunk.compression_type,
        )
        if actual != expected:
            raise PodcastAudioError(f"第 {index + 1} 个音频分段的 WAV 格式与前文不一致")


def _write_wave_atomically(target: Path, chunks: list[_WaveChunk]) -> tuple[str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".rendering",
        dir=target.parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    first = chunks[0]
    try:
        with wave.open(str(temporary_path), "wb") as writer:
            writer.setnchannels(first.channels)
            writer.setsampwidth(first.sample_width)
            writer.setframerate(first.frame_rate)
            writer.setcomptype(first.compression_type, "not compressed")
            for chunk in chunks:
                writer.writeframesraw(chunk.frames)
        with temporary_path.open("rb") as rendered:
            audio_bytes = rendered.read()
            os.fsync(rendered.fileno())
        content_hash = hashlib.sha256(audio_bytes).hexdigest()
        os.replace(temporary_path, target)
        _fsync_directory(target.parent)
        return content_hash, len(audio_bytes)
    finally:
        temporary_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
