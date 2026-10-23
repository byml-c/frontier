from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Memory
from ..schemas import InterestProfileUpdate
from ..services.common import log_operation, model_dict
from ..services.interest_evidence import (
    DERIVED_INTEREST_PROFILE_ID,
    active_feedback_evidence_keys,
    build_interest_context,
)
from ..services.interest_ranker import (
    load_model_run,
    load_ranker,
    rescore_candidate_papers,
    train_daily_ranker,
)

router = APIRouter(prefix="/api/interests", tags=["interests"])


@router.get("/context")
def get_interest_context(
    session: Annotated[Session, Depends(get_session)],
    recent_limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    return build_interest_context(session, recent_limit=recent_limit)


@router.get("/model")
def get_interest_model(session: Annotated[Session, Depends(get_session)]) -> dict:
    run = load_model_run(session)
    if run is None:
        return {"status": "missing", "model": None}
    return {
        "status": run.status,
        "model": {
            **model_dict(run),
            "artifact_exists": Path(run.artifact_path).exists(),
        },
    }


@router.post("/model/rescore")
def rescore_interest_model(session: Annotated[Session, Depends(get_session)]) -> dict:
    model_run = load_model_run(session)
    ranker = load_ranker(model_run)
    if ranker is None:
        raise HTTPException(status_code=409, detail="No active interest model artifact")
    result = rescore_candidate_papers(session, ranker)
    log_operation(
        session,
        actor="system",
        action="interest_model.rescored_candidates",
        detail=result,
    )
    session.commit()
    return result


@router.post("/model/train")
def train_interest_model(session: Annotated[Session, Depends(get_session)]) -> dict:
    ranker, run, metrics = train_daily_ranker(session)
    log_operation(
        session,
        actor="system",
        action="interest_model.trained",
        entity_type="interest_model",
        entity_id=run.id,
        detail={"version": run.version, **metrics},
    )
    session.commit()
    return {"version": ranker.version, "model_run_id": run.id, "metrics": metrics}


@router.put("/profile")
def update_interest_profile(
    payload: InterestProfileUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    context = build_interest_context(session, recent_limit=100)
    if payload.feedback_digest != context["feedback_digest"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Interest evidence changed; rebuild the profile from fresh context",
                "current_feedback_digest": context["feedback_digest"],
            },
        )
    available_evidence = active_feedback_evidence_keys(session)
    referenced_evidence = {
        key
        for preference in payload.semantic_preferences
        for key in preference.evidence_keys
    }
    missing = sorted(referenced_evidence - available_evidence)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Semantic preferences reference unavailable feedback evidence",
                "evidence_keys": missing,
            },
        )
    semantic_preferences = [
        preference.model_dump() for preference in payload.semantic_preferences
    ]
    metadata = {
        "schema_version": 1,
        "feedback_digest": payload.feedback_digest,
        "evidence_keys": sorted(referenced_evidence),
        "semantic_preferences": semantic_preferences,
    }
    item = session.get(Memory, DERIVED_INTEREST_PROFILE_ID)
    changed = item is None
    if item is None:
        item = Memory(
            id=DERIVED_INTEREST_PROFILE_ID,
            kind="interest_profile",
            title="反馈闭环兴趣画像",
            content=payload.content.strip(),
            source="feedback_profile",
            active=True,
            metadata_json=metadata,
        )
        session.add(item)
    elif item.content != payload.content.strip() or item.metadata_json != metadata or not item.active:
        item.kind = "interest_profile"
        item.title = "反馈闭环兴趣画像"
        item.content = payload.content.strip()
        item.source = "feedback_profile"
        item.active = True
        item.metadata_json = metadata
        changed = True
    if changed:
        log_operation(
            session,
            actor="agent",
            action="interest_profile.rebuilt",
            entity_type="memory",
            entity_id=item.id,
            detail={
                "feedback_digest": payload.feedback_digest,
                "evidence_count": len(referenced_evidence),
                "preference_count": len(semantic_preferences),
            },
        )
    session.commit()
    return {**model_dict(item), "changed": changed}
