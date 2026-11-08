from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Brief, BriefItem, Memory, Paper, PaperNode, PaperTag, ResearchNode, Tag
from .common import BROAD_DISCOVERY_DOMAINS, get_setting, model_dict

INTEREST_CONTEXT_SCHEMA_VERSION = 3
DERIVED_INTEREST_PROFILE_ID = "derived-interest-profile-v1"
READING_PROVENANCE_KEY = "reading_provenance"
FEEDBACK_PROVENANCE_KEY = "feedback_provenance"
IGNORE_PROVENANCE_KEY = "ignore_provenance"


def paper_user_score(paper: Paper) -> float | None:
    return paper.user_score


def feedback_payload_hash(
    *,
    user_score: float | None,
    post_read_summary: str | None,
    create_node: bool,
    tag_names: list[str],
    node_selections: list[dict],
    personal_comment: str | None,
    source_brief_item_id: str | None,
) -> str:
    return _digest(
        {
            "user_score": user_score,
            "post_read_summary": post_read_summary,
            "create_node": create_node,
            "tag_names": sorted(set(tag_names)),
            "node_selections": node_selections,
            "personal_comment": personal_comment,
            "source_brief_item_id": source_brief_item_id,
        }
    )


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _brief_item_payload(item: BriefItem, brief: Brief) -> dict[str, Any]:
    return {
        "kind": "manual" if item.tier == "manual" else "daily_brief",
        "brief_id": brief.id,
        "brief_item_id": item.id,
        "brief_date": brief.brief_date.isoformat(),
        "rank": item.rank,
        "tier": item.tier,
    }


def _published_attributions(
    session: Session,
) -> tuple[dict[str, tuple[BriefItem, Brief]], dict[str, list[tuple[BriefItem, Brief]]]]:
    rows = session.execute(
        select(BriefItem, Brief)
        .join(Brief, Brief.id == BriefItem.brief_id)
        .where(or_(Brief.status == "published", BriefItem.tier == "manual"))
        .order_by(Brief.brief_date.desc(), BriefItem.created_at.desc())
    ).all()
    by_item = {item.id: (item, brief) for item, brief in rows}
    by_paper: dict[str, list[tuple[BriefItem, Brief]]] = defaultdict(list)
    for item, brief in rows:
        # An explicit manual source is valid before publication, but a draft row
        # must never become an implicit legacy attribution.
        if brief.status == "published":
            by_paper[item.paper_id].append((item, brief))
    return by_item, by_paper


def resolve_feedback_attribution(
    paper: Paper,
    *,
    by_item: dict[str, tuple[BriefItem, Brief]],
    by_paper: dict[str, list[tuple[BriefItem, Brief]]],
) -> dict[str, Any]:
    metadata = paper.metadata_json or {}
    feedback_provenance = metadata.get(FEEDBACK_PROVENANCE_KEY)
    reading_provenance = metadata.get(READING_PROVENANCE_KEY)
    explicit_item_id = None
    ignore_provenance = metadata.get(IGNORE_PROVENANCE_KEY)
    provenance_candidates = (
        (feedback_provenance, reading_provenance)
        if paper.status == "completed"
        else (ignore_provenance, reading_provenance)
    )
    for value in provenance_candidates:
        if isinstance(value, dict) and isinstance(value.get("brief_item_id"), str):
            explicit_item_id = value["brief_item_id"]
            break
    if explicit_item_id and explicit_item_id in by_item:
        item, brief = by_item[explicit_item_id]
        if item.paper_id == paper.id:
            return {**_brief_item_payload(item, brief), "resolution": "explicit"}

    candidates = by_paper.get(paper.id, [])
    non_manual = [(item, brief) for item, brief in candidates if item.tier != "manual"]
    if len(non_manual) == 1:
        item, brief = non_manual[0]
        return {**_brief_item_payload(item, brief), "resolution": "unique_legacy_match"}
    manual = [(item, brief) for item, brief in candidates if item.tier == "manual"]
    if not non_manual and len(manual) == 1:
        item, brief = manual[0]
        return {**_brief_item_payload(item, brief), "resolution": "unique_manual_match"}
    return {
        "kind": "unattributed",
        "brief_id": None,
        "brief_item_id": None,
        "brief_date": None,
        "rank": None,
        "tier": None,
        "resolution": "ambiguous" if candidates else "none",
    }


def validate_brief_item_source(session: Session, paper_id: str, brief_item_id: str) -> BriefItem:
    item = session.get(BriefItem, brief_item_id)
    if item is None or item.paper_id != paper_id:
        raise ValueError("Brief item does not belong to this paper")
    brief = session.get(Brief, item.brief_id)
    if brief is None or (brief.status != "published" and item.tier != "manual"):
        raise ValueError("Brief item is not a published recommendation")
    return item


def _topic_rows(
    session: Session, paper_ids: list[str]
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    tags_by_paper: dict[str, list[dict[str, str]]] = defaultdict(list)
    nodes_by_paper: dict[str, list[dict[str, str]]] = defaultdict(list)
    if not paper_ids:
        return tags_by_paper, nodes_by_paper
    for paper_id, tag_id, name in session.execute(
        select(PaperTag.paper_id, Tag.id, Tag.name)
        .join(Tag, Tag.id == PaperTag.tag_id)
        .where(PaperTag.paper_id.in_(paper_ids))
        .order_by(Tag.name)
    ):
        tags_by_paper[paper_id].append({"id": tag_id, "name": name})
    for paper_id, node_id, name in session.execute(
        select(PaperNode.paper_id, ResearchNode.id, ResearchNode.name)
        .join(ResearchNode, ResearchNode.id == PaperNode.node_id)
        .where(PaperNode.paper_id.in_(paper_ids))
        .order_by(ResearchNode.name)
    ):
        nodes_by_paper[paper_id].append({"id": node_id, "name": name})
    return tags_by_paper, nodes_by_paper


def active_feedback_evidence_keys(session: Session) -> set[str]:
    return {
        f"paper:{p.id}:evaluation:{p.updated_at.isoformat()}"
        for p in session.scalars(select(Paper))
        if p.user_score is not None or p.user_note
    }


def build_interest_context(session: Session, *, recent_limit: int = 20) -> dict[str, Any]:
    papers = list(session.scalars(select(Paper).order_by(Paper.updated_at.desc())).all())
    rated = [p for p in papers if p.user_score is not None]
    feedback = [p for p in papers if p.user_score is not None or p.user_note]
    tags, nodes = _topic_rows(session, [p.id for p in feedback])
    by_item, by_paper = _published_attributions(session)
    topics: dict[str, dict] = {}
    signals = []
    errors = []
    for p in feedback:
        key = f"paper:{p.id}:evaluation:{p.updated_at.isoformat()}"
        for tag in tags.get(p.id, []) if p.user_score is not None else []:
            row = topics.setdefault(
                tag["id"], {"name": tag["name"], "evidence_count": 0, "score_sum": 0.0}
            )
            row["evidence_count"] += 1
            row["score_sum"] += p.user_score
        if p.user_score is not None and p.agent_score is not None:
            errors.append((p.user_score - p.agent_score) ** 2)
        signals.append(
            {
                "paper_id": p.id,
                "title": p.title,
                "status": p.status,
                "evidence_key": key,
                "confirmed_at": model_dict(p)["updated_at"],
                "labels": {"user_score": p.user_score},
                "agent_score": p.agent_score,
                "model_score": p.model_score,
                "pre_read_summary": _bounded_text(p.pre_read_summary, 500),
                "post_read_summary": _bounded_text(p.post_read_summary, 1200),
                "user_note": p.user_note,
                "tags": tags.get(p.id, []),
                "nodes": nodes.get(p.id, []),
                "attribution": resolve_feedback_attribution(p, by_item=by_item, by_paper=by_paper),
            }
        )
    for row in topics.values():
        row["mean_score"] = round(row.pop("score_sum") / row["evidence_count"], 4)
    digest = _digest(signals)
    derived = session.get(Memory, DERIVED_INTEREST_PROFILE_ID)
    profile = None
    if derived and derived.active:
        stale = (derived.metadata_json or {}).get("feedback_digest") != digest
        profile = {
            "id": derived.id,
            "content": None if stale else derived.content,
            "metadata": derived.metadata_json or {},
            "stale": stale,
            "updated_at": model_dict(derived)["updated_at"],
        }
    memories = [
        {
            "id": m.id,
            "title": m.title,
            "content": m.content,
            "kind": m.kind,
            "updated_at": model_dict(m)["updated_at"],
        }
        for m in session.scalars(
            select(Memory).where(Memory.active.is_(True), Memory.id != DERIVED_INTEREST_PROFILE_ID)
        ).all()
    ]
    graph = [
        {"id": n.id, "name": n.name, "core_question": n.core_question, "domain": n.domain}
        for n in session.scalars(select(ResearchNode).where(ResearchNode.is_active.is_(True))).all()
    ]
    layers = {
        "explicit_priors": [m for m in memories if m["kind"] == "interest_profile"],
        "semantic_profile": profile,
        "workflow_lessons": [m for m in memories if m["kind"] == "agent_experience"],
        "seed_interests": get_setting(session, "initial_interest_domains", []),
        "discovery_domains": get_setting(session, "discovery_domains", BROAD_DISCOVERY_DOMAINS),
        "confirmed_topic_profile": {
            "rated_paper_count": len(rated),
            "topic_count": len(topics),
            "topics": list(topics.values())[:100],
        },
        "research_graph": graph,
    }
    return {
        "schema_version": INTEREST_CONTEXT_SCHEMA_VERSION,
        "source_digest": _digest({"feedback": digest, "layers": layers}),
        "feedback_digest": digest,
        "feedback_revision": signals[0]["confirmed_at"] if signals else None,
        "layers": layers,
        "recent_feedback": signals[:recent_limit],
        "calibration": {
            "score_sample_count": len(errors),
            "score_mse": sum(errors) / len(errors) if errors else None,
        },
    }
