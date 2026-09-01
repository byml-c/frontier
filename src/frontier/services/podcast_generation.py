from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import (
    Brief,
    BriefItem,
    Paper,
    PaperNarration,
    PodcastEpisode,
    TaskRun,
    utcnow,
)
from .common import log_operation
from .podcast_audio import PodcastAudioError, render_podcast_audio
from .podcast_content import brief_podcast_source_digest, paper_narration_source_digest
from .speech_provider import SpeechProviderError
from .task_control import ACTIVE_TASK_STATUSES, TaskClaim, task_claim_transaction

PODCAST_TASK_KIND = "podcast_episode_render"
PODCAST_LANGUAGE = "zh-CN"


class PodcastGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NarrationDraft:
    title: str
    script_markdown: str
    speech_text: str
    sections: list[dict[str, str]]
    citations: list[dict[str, str]]


def _digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _spoken(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", text)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_>#|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit("。", 1)[0].strip()
    if len(shortened) < limit // 2:
        shortened = text[:limit].rstrip("，,；;：: ")
    return f"{shortened}。"


def compose_paper_narration(paper: Paper, *, rank: int) -> NarrationDraft:
    """Create a source-grounded single-speaker script from durable analysis fields."""
    title = _spoken(paper.title, limit=240) or "未命名论文"
    author = _spoken(paper.first_author, limit=80)
    section_values = [
        ("研究概览", paper.summary or paper.abstract, 260),
        ("研究问题", paper.problem, 220),
        ("核心创新", paper.innovation, 240),
        ("实验与证据", paper.experiment, 240),
        ("局限与边界", paper.limitations, 220),
        ("推荐理由", paper.recommendation_reason, 180),
    ]
    sections = [
        {"heading": heading, "text": text}
        for heading, raw, limit in section_values
        if (text := _spoken(raw, limit=limit))
    ]
    if not sections:
        sections = [{"heading": "研究概览", "text": "当前简报没有提供更多分析内容。"}]

    heading = f"第 {rank} 篇：{title}"
    byline = f"第一作者是{author}。" if author else ""
    speech_parts = [f"第{rank}篇，我们来看《{title}》。", byline]
    speech_parts.extend(f"{item['heading']}。{item['text']}" for item in sections)
    speech_text = "\n\n".join(part for part in speech_parts if part)

    markdown_parts = [f"## {heading}"]
    if author:
        markdown_parts.append(f"第一作者：{author}")
    markdown_parts.extend(
        f"### {item['heading']}\n\n{item['text']}" for item in sections
    )
    citations = (
        [{"label": "论文来源", "url": paper.source_url}]
        if paper.source_url
        else []
    )
    return NarrationDraft(
        title=title,
        script_markdown="\n\n".join(markdown_parts),
        speech_text=speech_text,
        sections=sections,
        citations=citations,
    )


def _active_task(session: Session, brief_id: str) -> TaskRun | None:
    return session.scalar(
        select(TaskRun)
        .where(
            TaskRun.kind == PODCAST_TASK_KIND,
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
            TaskRun.payload_json["brief_id"].as_string() == brief_id,
        )
        .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
        .limit(1)
    )


def get_or_create_podcast_task(
    session: Session, brief_id: str, *, language: str = PODCAST_LANGUAGE
) -> TaskClaim:
    """Atomically create one active render task for a published brief."""
    with task_claim_transaction(session):
        brief = session.get(Brief, brief_id)
        if brief is None:
            raise PodcastGenerationError("简报不存在")
        if brief.status != "published":
            raise PodcastGenerationError("只能为已发布的简报生成播客")
        active = _active_task(session, brief_id)
        if active is not None:
            return TaskClaim(task=active, created=False)

        task = TaskRun(
            kind=PODCAST_TASK_KIND,
            payload_json={"brief_id": brief_id, "language": language},
        )
        session.add(task)
        session.flush()
        episode = session.scalar(
            select(PodcastEpisode).where(
                PodcastEpisode.brief_id == brief_id,
                PodcastEpisode.language == language,
            )
        )
        source_digest = brief_podcast_source_digest(
            session, brief, language=language
        )
        if episode is None:
            episode = PodcastEpisode(
                brief_id=brief_id,
                language=language,
                status="generating",
                revision=1,
                title=brief.title,
                source_digest=source_digest,
                generation_task_id=task.id,
            )
            session.add(episode)
        else:
            episode.status = "generating"
            episode.revision += 1
            episode.source_digest = source_digest
            episode.generation_task_id = task.id
            episode.error = None
            episode.published_at = None
        session.flush()
        log_operation(
            session,
            actor="system",
            action="podcast_episode.queued",
            entity_type="podcast_episode",
            entity_id=episode.id,
            task_id=task.id,
            detail={"brief_id": brief_id, "language": language},
        )
        return TaskClaim(task=task, created=True)


def queue_podcast_for_brief(brief_id: str) -> None:
    """Best-effort post-publish hook; podcast failure never rolls back a brief."""
    from .task_dispatcher import submit_task

    if not (settings.aliyun_tts_api_key or "").strip():
        return
    try:
        with SessionLocal() as session:
            claim = get_or_create_podcast_task(session, brief_id)
            task_id = claim.task.id if claim.task is not None else None
        if task_id:
            submit_task(task_id)
    except (PodcastGenerationError, SQLAlchemyError):
        # The explicit generate endpoint remains available. Publishing the daily
        # research record is the primary transaction and must stay independent.
        return


def _prepare_scripts(session: Session, task: TaskRun) -> tuple[PodcastEpisode, int]:
    payload = task.payload_json or {}
    brief_id = payload.get("brief_id")
    language = payload.get("language") or PODCAST_LANGUAGE
    if not isinstance(brief_id, str):
        raise PodcastGenerationError("播客任务缺少 brief_id")
    brief = session.get(Brief, brief_id)
    if brief is None or brief.status != "published":
        raise PodcastGenerationError("播客对应的已发布简报不存在")
    episode = session.scalar(
        select(PodcastEpisode).where(
            PodcastEpisode.brief_id == brief_id,
            PodcastEpisode.language == language,
        )
    )
    if episode is None or episode.generation_task_id != task.id:
        raise PodcastGenerationError("播客任务已被更新的生成请求取代")

    rows = session.execute(
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
        .order_by(BriefItem.rank, BriefItem.id)
    ).all()
    if not rows:
        raise PodcastGenerationError("简报中没有可生成文字稿的论文")

    narration_manifest: list[dict[str, Any]] = []
    markdown_parts = [f"# {brief.title}"]
    speech_parts = [
        f"欢迎收听{brief.brief_date.isoformat()}的每日论文播客。今天共有{len(rows)}篇论文。"
    ]
    introduction = _spoken(brief.introduction, limit=320)
    if introduction:
        markdown_parts.append(introduction)
        speech_parts.append(introduction)

    for position, (_brief_item, paper) in enumerate(rows, start=1):
        draft = compose_paper_narration(paper, rank=position)
        source_digest = paper_narration_source_digest(paper)
        narration = session.scalar(
            select(PaperNarration).where(
                PaperNarration.paper_id == paper.id,
                PaperNarration.language == language,
            )
        )
        content_hash = _digest(
            f"{draft.script_markdown}\n---speech---\n{draft.speech_text}"
        )
        if narration is None:
            narration = PaperNarration(
                paper_id=paper.id,
                language=language,
                revision=1,
                source_digest=source_digest,
            )
            session.add(narration)
        elif (
            narration.content_hash != content_hash
            or narration.source_digest != source_digest
            or narration.status != "ready"
        ):
            narration.revision += 1
        narration.status = "ready"
        narration.script_markdown = draft.script_markdown
        narration.speech_text = draft.speech_text
        narration.sections_json = draft.sections
        narration.citations_json = draft.citations
        narration.pronunciations_json = []
        narration.source_digest = source_digest
        narration.content_hash = content_hash
        narration.generation_task_id = task.id
        narration.error = None
        session.flush()
        narration_manifest.append(
            {
                "paper_id": paper.id,
                "narration_id": narration.id,
                "revision": narration.revision,
                "source_digest": source_digest,
            }
        )
        markdown_parts.append(draft.script_markdown)
        speech_parts.append(draft.speech_text)

    closing = "以上就是今天的论文简报，感谢收听。"
    markdown_parts.append(f"## 结语\n\n{closing}")
    speech_parts.append(closing)
    episode.script_text = "\n\n".join(markdown_parts)
    episode.speech_text = "\n\n".join(speech_parts)
    episode.status = "rendering"
    episode.source_digest = brief_podcast_source_digest(
        session, brief, language=language
    )
    episode.content_hash = _digest(
        f"{episode.script_text}\n---speech---\n{episode.speech_text}"
    )
    episode.manifest_json = {
        "version": 1,
        "language": language,
        "narrations": narration_manifest,
    }
    episode.provider = "aliyun"
    episode.model = settings.aliyun_tts_model
    episode.voice = settings.aliyun_tts_voice
    episode.error = None
    episode.revision += 1
    session.flush()
    return episode, episode.revision


def _safe_failure(exc: Exception) -> str:
    if isinstance(exc, (PodcastGenerationError, PodcastAudioError, SpeechProviderError)):
        return str(exc)
    return f"播客生成失败（{type(exc).__name__}）"


def mark_podcast_task_failed(session: Session, task: TaskRun, error: str) -> None:
    episode = session.scalar(
        select(PodcastEpisode).where(PodcastEpisode.generation_task_id == task.id)
    )
    if episode is not None and episode.status != "ready":
        episode.status = "failed"
        episode.error = error
        episode.revision += 1
        episode.published_at = None


def run_podcast_render_task(task_id: str) -> None:
    """Render one episode without consuming a Codex Agent execution slot."""
    try:
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task is None or task.status != "queued":
                return
            task.status = "running"
            task.started_at = utcnow()
            episode, expected_revision = _prepare_scripts(session, task)
            speech_text = episode.speech_text or ""
            target = (
                settings.audio_dir
                / "podcasts"
                / episode.id
                / f"{task.id}.wav"
            )
            episode_id = episode.id
            session.commit()

        rendered = render_podcast_audio(speech_text, target)

        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            episode = session.get(PodcastEpisode, episode_id)
            if (
                task is None
                or task.status != "running"
                or episode is None
                or episode.generation_task_id != task_id
                or episode.revision != expected_revision
            ):
                return
            episode.status = "ready"
            episode.provider = rendered.provider
            episode.model = rendered.model
            episode.voice = rendered.voice
            episode.audio_path = str(rendered.path.resolve())
            episode.audio_content_hash = f"sha256:{rendered.content_hash}"
            episode.duration_ms = round(rendered.duration_seconds * 1000)
            episode.size_bytes = rendered.size_bytes
            episode.manifest_json = {
                **(episode.manifest_json or {}),
                "audio": rendered.manifest,
            }
            episode.error = None
            episode.published_at = utcnow()
            episode.revision += 1
            task.status = "completed"
            task.result_json = {
                "episode_id": episode.id,
                "brief_id": episode.brief_id,
                "audio_url": f"/api/podcasts/{episode.id}/audio",
                "duration_ms": episode.duration_ms,
            }
            task.error = None
            task.finished_at = utcnow()
            log_operation(
                session,
                actor="system",
                action="podcast_episode.ready",
                entity_type="podcast_episode",
                entity_id=episode.id,
                task_id=task.id,
                detail={"duration_ms": episode.duration_ms, "size_bytes": episode.size_bytes},
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - durable failure state is required
        error = _safe_failure(exc)
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task is not None and task.status != "cancelled":
                task.status = "failed"
                task.error = error
                task.finished_at = utcnow()
                mark_podcast_task_failed(session, task, error)
                log_operation(
                    session,
                    actor="system",
                    action="podcast_episode.failed",
                    entity_type="task",
                    entity_id=task.id,
                    task_id=task.id,
                    detail={"error": error},
                )
                session.commit()


def podcast_audio_path(episode: PodcastEpisode) -> Path:
    if episode.status != "ready" or not episode.audio_path:
        raise PodcastGenerationError("播客音频尚未就绪")
    audio_root = settings.audio_dir.resolve()
    path = Path(episode.audio_path).resolve()
    if audio_root not in path.parents or path.suffix.casefold() != ".wav":
        raise PodcastGenerationError("播客音频路径无效")
    if not path.is_file():
        raise PodcastGenerationError("播客音频文件不存在")
    return path


__all__ = [
    "PODCAST_TASK_KIND",
    "PodcastGenerationError",
    "compose_paper_narration",
    "get_or_create_podcast_task",
    "mark_podcast_task_failed",
    "podcast_audio_path",
    "queue_podcast_for_brief",
    "run_podcast_render_task",
]
