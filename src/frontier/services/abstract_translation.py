from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Brief, BriefItem, Paper, TaskRun, TranslationCache

TARGET_LANGUAGE = "简体中文"
PAPER_ABSTRACT_TRANSLATION_KIND = "paper_abstract_translation"
ABSTRACT_TRANSLATION_STATE_KEY = "abstract_translation"


def abstract_selection_key(
    paper_id: str, abstract: str, target_language: str = TARGET_LANGUAGE
) -> str:
    payload = {
        "paper_id": paper_id,
        "text": " ".join(abstract.split()),
        "target_language": target_language,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def abstract_translation(
    session: Session, paper: Paper, target_language: str = TARGET_LANGUAGE
) -> TranslationCache | None:
    if not paper.abstract:
        return None
    return session.scalar(
        select(TranslationCache).where(
            TranslationCache.selection_key
            == abstract_selection_key(paper.id, paper.abstract, target_language)
        )
    )


def queue_paper_abstract_translation(
    session: Session, paper: Paper, *, force: bool = False
) -> TaskRun | None:
    """Queue a translation for one paper, independent of brief membership.

    Manual additions can be generated before a brief is published.  The historical
    batch endpoint only knows about brief items, so keep a small paper-scoped task
    for this workflow and reuse the same content-addressed translation cache.
    """
    if not paper.abstract:
        return None
    if not force and abstract_translation(session, paper):
        return None
    metadata = dict(paper.metadata_json or {})
    state = metadata.get(ABSTRACT_TRANSLATION_STATE_KEY)
    if isinstance(state, dict) and state.get("status") in {"queued", "processing"}:
        task_id = state.get("task_id")
        active = session.get(TaskRun, task_id) if isinstance(task_id, str) else None
        if active and active.status in {"queued", "running"}:
            return active
    task = TaskRun(
        kind=PAPER_ABSTRACT_TRANSLATION_KIND,
        payload_json={
            "paper_id": paper.id,
            "text": paper.abstract,
            "target_language": TARGET_LANGUAGE,
            "selection_key": abstract_selection_key(paper.id, paper.abstract),
        },
    )
    session.add(task)
    session.flush()
    paper.metadata_json = {
        **metadata,
        ABSTRACT_TRANSLATION_STATE_KEY: {
            "status": "queued",
            "task_id": task.id,
            "error": None,
        },
    }
    session.commit()
    return task


def queue_brief_abstract_translations(
    session: Session, brief_id: str, *, paper_id: str | None = None, force: bool = False
) -> TaskRun | None:
    statement = (
        select(BriefItem, Paper)
        .join(Paper, Paper.id == BriefItem.paper_id)
        .where(BriefItem.brief_id == brief_id)
        .order_by(BriefItem.rank)
    )
    if paper_id:
        statement = statement.where(Paper.id == paper_id)
    items: list[dict[str, Any]] = []
    for _item, paper in session.execute(statement).all():
        if not paper.abstract or (not force and abstract_translation(session, paper)):
            continue
        items.append(
            {
                "paper_id": paper.id,
                "text": paper.abstract,
                "target_language": TARGET_LANGUAGE,
                "selection_key": abstract_selection_key(paper.id, paper.abstract),
            }
        )
    if not items:
        return None
    task = TaskRun(
        kind="abstract_translation_batch", payload_json={"brief_id": brief_id, "items": items}
    )
    session.add(task)
    session.flush()
    brief = session.get(Brief, brief_id)
    if brief:
        brief.snapshot_json = {
            **(brief.snapshot_json or {}),
            "abstract_translation_status": "queued",
            "abstract_translation_task_id": task.id,
        }
    session.commit()
    return task
