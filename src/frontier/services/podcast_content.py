from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Brief, BriefItem, Paper, PaperNarration, PodcastEpisode
from ..schemas import (
    PaperNarrationCommit,
    PodcastEpisodeCommit,
    PodcastEpisodeStatusUpdate,
)
from .paper_sources import paper_source_version


class PodcastContentError(RuntimeError):
    pass


class PodcastContentNotFoundError(PodcastContentError):
    pass


class PodcastContentRevisionError(PodcastContentError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Content revision changed from {expected} to {actual}")


class PodcastContentSourceError(PodcastContentError):
    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__("Source content changed while generated content was being prepared")


class PodcastContentValidationError(PodcastContentError):
    pass


def normalize_language(language: str) -> str:
    parts = language.strip().replace("_", "-").split("-")
    if not parts or not parts[0]:
        raise PodcastContentValidationError("language cannot be blank")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == 2 else part)
    return "-".join(normalized)


def _digest(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _pdf_version(paper: Paper) -> str | None:
    return paper_source_version(paper)


def paper_narration_source_digest(paper: Paper) -> str:
    """Hash only the durable paper evidence used to write a narration."""
    return _digest(
        {
            "id": paper.id,
            "title": paper.title,
            "abstract": paper.abstract,
            "authors": paper.authors,
            "published_at": paper.published_at,
            "summary": paper.summary,
            "agent_comment": paper.agent_comment,
            "problem": paper.problem,
            "innovation": paper.innovation,
            "experiment": paper.experiment,
            "limitations": paper.limitations,
            "full_analysis": paper.full_analysis,
            "recommendation_reason": paper.recommendation_reason,
            "pdf_version": _pdf_version(paper),
        }
    )


def brief_podcast_source_digest(
    session: Session, brief: Brief, *, language: str = "zh-CN"
) -> str:
    language = normalize_language(language)
    rows = session.execute(
        select(BriefItem, PaperNarration)
        .outerjoin(
            PaperNarration,
            (PaperNarration.paper_id == BriefItem.paper_id)
            & (PaperNarration.language == language),
        )
        .where(BriefItem.brief_id == brief.id)
        .order_by(BriefItem.rank, BriefItem.id)
    ).all()
    return _digest(
        {
            "brief": {
                "id": brief.id,
                "date": brief.brief_date,
                "title": brief.title,
                "introduction": brief.introduction,
                "status": brief.status,
                "snapshot": brief.snapshot_json,
            },
            "items": [
                {
                    "id": item.id,
                    "paper_id": item.paper_id,
                    "rank": item.rank,
                    "tier": item.tier,
                    "snapshot": item.snapshot_json,
                    "narration": (
                        {
                            "id": narration.id,
                            "revision": narration.revision,
                            "status": narration.status,
                            "content_hash": narration.content_hash,
                        }
                        if narration is not None
                        else None
                    ),
                }
                for item, narration in rows
            ],
        }
    )


def _safe_json(value: Any) -> Any:
    """Redact common credential-shaped fields before returning stored manifests."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            folded = key.casefold().replace("-", "_")
            if any(
                marker in folded
                for marker in ("api_key", "apikey", "secret", "authorization", "access_token")
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = _safe_json(item)
        return result
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    return value


def _safe_error(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
    configured_key = getattr(settings, "aliyun_tts_api_key", None)
    if isinstance(configured_key, str) and configured_key:
        value = value.replace(configured_key, "[REDACTED]")
    return value


def narration_payload(
    session: Session, paper_id: str, *, language: str = "zh-CN"
) -> dict[str, Any]:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise PodcastContentNotFoundError("Paper not found")
    language = normalize_language(language)
    current_source_digest = paper_narration_source_digest(paper)
    item = session.scalar(
        select(PaperNarration).where(
            PaperNarration.paper_id == paper_id,
            PaperNarration.language == language,
        )
    )
    if item is None:
        return {
            "id": None,
            "paper_id": paper_id,
            "language": language,
            "status": "missing",
            "revision": 0,
            "script_markdown": None,
            "speech_text": None,
            "sections": [],
            "citations": [],
            "pronunciations": [],
            "source_digest": None,
            "current_source_digest": current_source_digest,
            "source_pdf_version": None,
            "current_pdf_version": _pdf_version(paper),
            "content_hash": None,
            "task_id": None,
            "error": None,
            "stale": False,
            "updated_at": None,
        }
    return {
        "id": item.id,
        "paper_id": item.paper_id,
        "language": item.language,
        "status": item.status,
        "revision": item.revision,
        "script_markdown": item.script_markdown,
        "speech_text": item.speech_text,
        "sections": _safe_json(item.sections_json or []),
        "citations": _safe_json(item.citations_json or []),
        "pronunciations": _safe_json(item.pronunciations_json or []),
        "source_digest": item.source_digest,
        "current_source_digest": current_source_digest,
        "source_pdf_version": item.source_pdf_version,
        "current_pdf_version": _pdf_version(paper),
        "content_hash": item.content_hash,
        "task_id": item.generation_task_id,
        "error": _safe_error(item.error),
        "stale": item.source_digest != current_source_digest,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _narration_values(
    payload: PaperNarrationCommit, *, language: str, source_pdf_version: str | None
) -> dict[str, Any]:
    script_markdown = payload.script_markdown.strip() if payload.script_markdown else None
    speech_text = payload.speech_text.strip() if payload.speech_text else None
    content = {
        "script_markdown": script_markdown,
        "speech_text": speech_text,
        "sections": payload.sections,
        "citations": payload.citations,
        "pronunciations": payload.pronunciations,
    }
    return {
        "language": language,
        "status": payload.status,
        "script_markdown": script_markdown,
        "speech_text": speech_text,
        "sections_json": payload.sections,
        "citations_json": payload.citations,
        "pronunciations_json": payload.pronunciations,
        "source_digest": payload.source_digest,
        "source_pdf_version": source_pdf_version,
        "content_hash": _digest(content) if script_markdown or speech_text else None,
        "generation_task_id": payload.task_id,
        "error": payload.error,
    }


def commit_paper_narration(
    session: Session, paper_id: str, payload: PaperNarrationCommit
) -> tuple[PaperNarration, bool]:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise PodcastContentNotFoundError("Paper not found")
    language = normalize_language(payload.language)
    current_digest = paper_narration_source_digest(paper)
    if payload.source_digest != current_digest:
        raise PodcastContentSourceError(payload.source_digest, current_digest)
    current_pdf_version = _pdf_version(paper)
    if (
        payload.source_pdf_version is not None
        and payload.source_pdf_version != current_pdf_version
    ):
        raise PodcastContentValidationError("source_pdf_version is not current")
    values = _narration_values(
        payload, language=language, source_pdf_version=current_pdf_version
    )
    item = session.scalar(
        select(PaperNarration).where(
            PaperNarration.paper_id == paper_id,
            PaperNarration.language == language,
        )
    )
    if item is not None and all(getattr(item, key) == value for key, value in values.items()):
        return item, False
    actual_revision = item.revision if item is not None else 0
    if payload.expected_revision != actual_revision:
        raise PodcastContentRevisionError(payload.expected_revision, actual_revision)
    if item is None:
        item = PaperNarration(paper_id=paper_id, revision=1, **values)
        session.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
        item.revision += 1
    session.flush()
    return item, True


def episode_payload(
    session: Session, brief_id: str, *, language: str = "zh-CN"
) -> dict[str, Any]:
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise PodcastContentNotFoundError("Brief not found")
    language = normalize_language(language)
    current_source_digest = brief_podcast_source_digest(
        session, brief, language=language
    )
    item = session.scalar(
        select(PodcastEpisode).where(
            PodcastEpisode.brief_id == brief_id,
            PodcastEpisode.language == language,
        )
    )
    if item is None:
        return {
            "id": None,
            "brief_id": brief_id,
            "language": language,
            "status": "missing",
            "revision": 0,
            "title": None,
            "script_text": None,
            "speech_text": None,
            "manifest": {},
            "source_digest": None,
            "current_source_digest": current_source_digest,
            "content_hash": None,
            "provider": None,
            "model": None,
            "voice": None,
            "audio_url": None,
            "audio_content_hash": None,
            "duration_ms": None,
            "size_bytes": None,
            "task_id": None,
            "published_at": None,
            "error": None,
            "stale": False,
            "updated_at": None,
        }
    return {
        "id": item.id,
        "brief_id": item.brief_id,
        "language": item.language,
        "status": item.status,
        "revision": item.revision,
        "title": item.title,
        "script_text": item.script_text,
        "speech_text": item.speech_text,
        "manifest": _safe_json(item.manifest_json or {}),
        "source_digest": item.source_digest,
        "current_source_digest": current_source_digest,
        "content_hash": item.content_hash,
        "provider": item.provider,
        "model": item.model,
        "voice": item.voice,
        "audio_url": (
            f"/api/podcasts/{item.id}/audio"
            if item.status == "ready" and item.audio_path
            else None
        ),
        "audio_content_hash": item.audio_content_hash,
        "duration_ms": item.duration_ms,
        "size_bytes": item.size_bytes,
        "task_id": item.generation_task_id,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "error": _safe_error(item.error),
        "stale": item.source_digest != current_source_digest,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _validated_audio_path(value: str | None, *, require_file: bool) -> str | None:
    if value is None:
        return None
    audio_root = Path(getattr(settings, "audio_dir", settings.data_dir / "audio")).resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = audio_root / candidate
    candidate = candidate.resolve()
    if candidate != audio_root and audio_root not in candidate.parents:
        raise PodcastContentValidationError("audio_path must be inside the audio directory")
    if candidate.suffix.casefold() != ".wav":
        raise PodcastContentValidationError("audio_path must point to a WAV file")
    if require_file and not candidate.is_file():
        raise PodcastContentValidationError("ready episode audio file does not exist")
    return str(candidate)


def _normalized_content_hash(value: str | None) -> str | None:
    if value is None or value.startswith("sha256:"):
        return value
    return f"sha256:{value}"


def _episode_values(payload: PodcastEpisodeCommit, *, language: str) -> dict[str, Any]:
    title = payload.title.strip()
    script_text = payload.script_text.strip() if payload.script_text else None
    speech_text = payload.speech_text.strip() if payload.speech_text else None
    return {
        "language": language,
        "status": payload.status,
        "title": title,
        "script_text": script_text,
        "speech_text": speech_text,
        "manifest_json": payload.manifest,
        "source_digest": payload.source_digest,
        "content_hash": _digest(
            {"title": title, "script_text": script_text, "speech_text": speech_text}
        ),
        "provider": payload.provider,
        "model": payload.model,
        "voice": payload.voice,
        "audio_path": _validated_audio_path(
            payload.audio_path, require_file=payload.status == "ready"
        ),
        "audio_content_hash": _normalized_content_hash(payload.audio_content_hash),
        "duration_ms": payload.duration_ms,
        "size_bytes": payload.size_bytes,
        "generation_task_id": payload.task_id,
        "error": payload.error,
    }


def commit_podcast_episode(
    session: Session, brief_id: str, payload: PodcastEpisodeCommit
) -> tuple[PodcastEpisode, bool]:
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise PodcastContentNotFoundError("Brief not found")
    language = normalize_language(payload.language)
    current_digest = brief_podcast_source_digest(session, brief, language=language)
    if payload.source_digest != current_digest:
        raise PodcastContentSourceError(payload.source_digest, current_digest)
    values = _episode_values(payload, language=language)
    existing_for_brief = session.scalar(
        select(PodcastEpisode).where(PodcastEpisode.brief_id == brief_id)
    )
    if existing_for_brief is not None and existing_for_brief.language != language:
        raise PodcastContentValidationError(
            "a podcast episode already exists for this brief in another language"
        )
    item = session.scalar(
        select(PodcastEpisode).where(
            PodcastEpisode.brief_id == brief_id,
            PodcastEpisode.language == language,
        )
    )
    if item is not None and all(getattr(item, key) == value for key, value in values.items()):
        return item, False
    actual_revision = item.revision if item is not None else 0
    if payload.expected_revision != actual_revision:
        raise PodcastContentRevisionError(payload.expected_revision, actual_revision)
    if item is None:
        item = PodcastEpisode(brief_id=brief_id, revision=1, **values)
        session.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
        item.revision += 1
    if item.status == "ready" and item.published_at is None:
        item.published_at = datetime.now(UTC)
    elif item.status != "ready":
        item.published_at = None
    session.flush()
    return item, True


def update_podcast_episode_status(
    session: Session,
    brief_id: str,
    payload: PodcastEpisodeStatusUpdate,
    *,
    language: str = "zh-CN",
) -> tuple[PodcastEpisode, bool]:
    language = normalize_language(language)
    item = session.scalar(
        select(PodcastEpisode).where(
            PodcastEpisode.brief_id == brief_id,
            PodcastEpisode.language == language,
        )
    )
    if item is None:
        if session.get(Brief, brief_id) is None:
            raise PodcastContentNotFoundError("Brief not found")
        raise PodcastContentNotFoundError("Podcast episode not found")
    field_map = {
        "manifest": "manifest_json",
        "provider": "provider",
        "model": "model",
        "voice": "voice",
        "audio_path": "audio_path",
        "audio_content_hash": "audio_content_hash",
        "duration_ms": "duration_ms",
        "size_bytes": "size_bytes",
        "task_id": "generation_task_id",
        "error": "error",
    }
    values: dict[str, Any] = {"status": payload.status}
    for request_name, model_name in field_map.items():
        if request_name in payload.model_fields_set:
            value = getattr(payload, request_name)
            if request_name == "audio_path":
                value = _validated_audio_path(value, require_file=payload.status == "ready")
            elif request_name == "audio_content_hash":
                value = _normalized_content_hash(value)
            values[model_name] = value
    if payload.status == "ready":
        combined = {key: values.get(key, getattr(item, key)) for key in (
            "audio_path", "audio_content_hash", "duration_ms", "size_bytes"
        )}
        if (
            not combined["audio_path"]
            or not combined["audio_content_hash"]
            or combined["duration_ms"] is None
            or combined["size_bytes"] is None
        ):
            raise PodcastContentValidationError(
                "ready episode requires audio_path, audio_content_hash, duration_ms and size_bytes"
            )
    if all(getattr(item, key) == value for key, value in values.items()):
        return item, False
    if item.revision != payload.expected_revision:
        raise PodcastContentRevisionError(payload.expected_revision, item.revision)
    for key, value in values.items():
        setattr(item, key, value)
    item.revision += 1
    if item.status == "ready" and item.published_at is None:
        item.published_at = datetime.now(UTC)
    elif item.status != "ready":
        item.published_at = None
    session.flush()
    return item, True
