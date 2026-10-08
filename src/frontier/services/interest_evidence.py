from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    AppSetting,
    Brief,
    BriefItem,
    Memory,
    Paper,
    PaperNode,
    PaperTag,
    ResearchNode,
    Tag,
)
from .common import model_dict
from .completion_summary import completion_summary_structured

INTEREST_CONTEXT_SCHEMA_VERSION = 2
DERIVED_INTEREST_PROFILE_ID = "derived-interest-profile-v1"
READING_PROVENANCE_KEY = "reading_provenance"
FEEDBACK_PROVENANCE_KEY = "feedback_provenance"
IGNORE_PROVENANCE_KEY = "ignore_provenance"
INTEREST_LEVELS = {"ignored", "neutral", "interested"}
QUALITY_LEVELS = {"poor", "normal", "excellent"}


def paper_interest_level(paper: Paper) -> str | None:
    """Return the canonical three-state interest judgment with legacy fallback."""
    if paper.status == "ignored":
        return "ignored"
    if paper.status != "completed":
        return None
    provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY)
    if isinstance(provenance, dict) and provenance.get("active", True):
        level = provenance.get("interest_level")
        if level in INTEREST_LEVELS:
            return str(level)
    return "interested" if paper.is_interested else "neutral"


def paper_quality_level(paper: Paper) -> str | None:
    """Return the canonical three-state quality judgment with legacy fallback."""
    if paper.status != "completed":
        return None
    provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY)
    if isinstance(provenance, dict) and provenance.get("active", True):
        level = provenance.get("quality_level")
        if level in QUALITY_LEVELS:
            return str(level)
    # A legacy false meant only "not excellent". Map it conservatively to normal,
    # never to poor, because the old UI could not express that distinction.
    return "excellent" if paper.is_quality else "normal"


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


def feedback_payload_hash(
    *,
    interest_level: str,
    quality_level: str,
    create_node: bool,
    tag_names: list[str],
    node_selections: list[dict[str, Any]],
    user_attitude: str | None,
    personal_comment: str | None,
    source_brief_item_id: str | None,
) -> str:
    """Hash the user-confirmed semantics, not request syntax or generated suggestions."""
    return _digest(
        {
            "interest_level": interest_level,
            "quality_level": quality_level,
            "create_node": create_node,
            "tag_names": sorted({name.strip().casefold() for name in tag_names if name.strip()}),
            "node_selections": node_selections,
            "user_attitude": (user_attitude or "").strip(),
            "personal_comment": (personal_comment or "").strip(),
            "source_brief_item_id": source_brief_item_id,
        }
    )


def feedback_payload_hash_v3(
    *,
    interest_level: str,
    quality: bool,
    create_node: bool,
    tag_names: list[str],
    node_selections: list[dict[str, Any]],
    user_attitude: str | None,
    personal_comment: str | None,
    source_brief_item_id: str | None,
) -> str:
    """Compatibility hash for feedback written before three-state quality."""
    return _digest(
        {
            "interest_level": interest_level,
            "quality": quality,
            "create_node": create_node,
            "tag_names": sorted({name.strip().casefold() for name in tag_names if name.strip()}),
            "node_selections": node_selections,
            "user_attitude": (user_attitude or "").strip(),
            "personal_comment": (personal_comment or "").strip(),
            "source_brief_item_id": source_brief_item_id,
        }
    )


def feedback_payload_hash_v2(
    *,
    interested: bool,
    quality: bool,
    create_node: bool,
    tag_names: list[str],
    user_attitude: str | None,
    personal_comment: str | None,
    source_brief_item_id: str | None,
) -> str:
    """Compatibility hash for feedback written before tri-state/node selections."""
    return _digest(
        {
            "interested": interested,
            "quality": quality,
            "create_node": create_node,
            "tag_names": sorted({name.strip().casefold() for name in tag_names if name.strip()}),
            "user_attitude": (user_attitude or "").strip(),
            "personal_comment": (personal_comment or "").strip(),
            "source_brief_item_id": source_brief_item_id,
        }
    )


def _brief_item_payload(item: BriefItem, brief: Brief) -> dict[str, Any]:
    snapshot = item.snapshot_json or {}
    return {
        "kind": "manual" if item.tier == "manual" else "daily_brief",
        "brief_id": brief.id,
        "brief_item_id": item.id,
        "brief_date": brief.brief_date.isoformat(),
        "rank": item.rank,
        "tier": item.tier,
        "prediction": {
            "relevance_score": snapshot.get("relevance_score"),
            "research_score": snapshot.get("research_score"),
        },
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
        "prediction": {"relevance_score": None, "research_score": None},
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


def _score(actual: bool | float, predicted: Any) -> float | None:
    if isinstance(predicted, bool) or not isinstance(predicted, (int, float)):
        return None
    probability = min(1.0, max(0.0, float(predicted)))
    return (probability - float(actual)) ** 2


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


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
    """Return durable evidence identities without exposing an unbounded context payload."""
    keys: set[str] = set()
    for paper in session.scalars(
        select(Paper).where(
            Paper.status.in_(("candidate", "reading", "feedback_pending", "completed", "ignored"))
        )
    ):
        screening = paper.screening_feedback or {}
        if isinstance(screening, dict) and (
            screening.get("interest_level")
            or screening.get("quality_level")
            or screening.get("comment")
        ):
            keys.add(str(screening.get("evidence_key") or f"paper:{paper.id}:screening:legacy"))
        if paper.status not in {"completed", "ignored"}:
            continue
        metadata = paper.metadata_json or {}
        provenance_key = (
            FEEDBACK_PROVENANCE_KEY if paper.status == "completed" else IGNORE_PROVENANCE_KEY
        )
        provenance = metadata.get(provenance_key)
        if isinstance(provenance, dict):
            if not provenance.get("active", True):
                continue
            evidence_key = provenance.get("evidence_key")
            if isinstance(evidence_key, str):
                keys.add(evidence_key)
                continue
        suffix = "feedback" if paper.status == "completed" else "ignore"
        keys.add(f"paper:{paper.id}:{suffix}:legacy")
    return keys


def build_interest_context(session: Session, *, recent_limit: int = 20) -> dict[str, Any]:
    """Build a bounded, rebuildable handoff from confirmed paper state to the daily Agent."""
    completed = list(
        session.scalars(
            select(Paper)
            .where(Paper.status == "completed")
            .order_by(Paper.reading_completed_at.desc(), Paper.updated_at.desc())
        )
    )
    ignored = list(
        session.scalars(
            select(Paper).where(Paper.status == "ignored").order_by(Paper.updated_at.desc())
        )
    )
    all_feedback = [*completed, *ignored]
    screening_papers = [
        paper
        for paper in session.scalars(select(Paper).order_by(Paper.updated_at.desc()))
        if isinstance(paper.screening_feedback, dict)
        and any(
            (paper.screening_feedback or {}).get(key)
            for key in ("interest_level", "quality_level", "comment")
        )
    ]
    paper_ids = [paper.id for paper in all_feedback]
    tags_by_paper, nodes_by_paper = _topic_rows(session, paper_ids)
    by_item, by_paper = _published_attributions(session)

    topic_stats: dict[str, dict[str, Any]] = {}
    unclassified_completed = 0
    for paper in completed:
        interest_level = paper_interest_level(paper)
        # User-confirmed tags are the primary explicit topic signal. Graph nodes are
        # reported separately because Agent-authored graph structure must not train itself.
        tags = tags_by_paper.get(paper.id, [])
        if not tags:
            unclassified_completed += 1
        for tag in tags:
            key = tag["name"].strip().casefold()
            stat = topic_stats.setdefault(
                key,
                {
                    "name": tag["name"],
                    "positive_interest": 0,
                    "neutral_interest": 0,
                    "negative_interest": 0,
                    "positive_quality": 0,
                    "neutral_quality": 0,
                    "negative_quality": 0,
                    "evidence_count": 0,
                },
            )
            stat["evidence_count"] += 1
            interest_bucket = {
                "interested": "positive_interest",
                "neutral": "neutral_interest",
                "ignored": "negative_interest",
            }[interest_level or "neutral"]
            stat[interest_bucket] += 1
            quality_bucket = {
                "excellent": "positive_quality",
                "normal": "neutral_quality",
                "poor": "negative_quality",
            }[paper_quality_level(paper) or "normal"]
            stat[quality_bucket] += 1
    topics = []
    for stat in topic_stats.values():
        support = stat["evidence_count"]
        stat["interest_affinity"] = round(
            (stat["positive_interest"] - stat["negative_interest"]) / (support + 2), 4
        )
        stat["quality_affinity"] = round(
            (stat["positive_quality"] - stat["negative_quality"]) / (support + 2), 4
        )
        topics.append(stat)
    topics.sort(
        key=lambda item: (
            -item["evidence_count"],
            -item["interest_affinity"],
            item["name"].casefold(),
        )
    )
    topic_count = len(topics)
    topics = topics[:100]

    attributed_feedback = [
        (
            paper,
            resolve_feedback_attribution(paper, by_item=by_item, by_paper=by_paper),
        )
        for paper in all_feedback
    ]
    daily_feedback = [
        (paper, attribution)
        for paper, attribution in attributed_feedback
        if attribution["kind"] == "daily_brief"
    ]
    completed_recommended = [
        (paper, attribution) for paper, attribution in daily_feedback if paper.status == "completed"
    ]
    ignored_recommended = [
        (paper, attribution) for paper, attribution in daily_feedback if paper.status == "ignored"
    ]
    interest_hits = sum(
        paper_interest_level(paper) == "interested" for paper, _ in completed_recommended
    )
    quality_hits = sum(
        paper_quality_level(paper) == "excellent" for paper, _ in completed_recommended
    )
    joint_hits = sum(
        paper_interest_level(paper) == "interested" and paper_quality_level(paper) == "excellent"
        for paper, _ in completed_recommended
    )

    relevance_errors: list[float] = []
    research_errors: list[float] = []
    for paper, attribution in completed_recommended:
        prediction = attribution["prediction"]
        interest_target = {
            "ignored": 0.0,
            "neutral": 0.5,
            "interested": 1.0,
        }[paper_interest_level(paper) or "neutral"]
        relevance_error = _score(interest_target, prediction.get("relevance_score"))
        quality_target = {
            "poor": 0.0,
            "normal": 0.5,
            "excellent": 1.0,
        }[paper_quality_level(paper) or "normal"]
        research_error = _score(quality_target, prediction.get("research_score"))
        if relevance_error is not None:
            relevance_errors.append(relevance_error)
        if research_error is not None:
            research_errors.append(research_error)

    calibration = {
        "sample_policy": "confirmed_or_explicitly_ignored_only",
        "evaluated_daily_recommendations": len(daily_feedback),
        "completed_daily_recommendations": len(completed_recommended),
        "ignored_daily_recommendations": len(ignored_recommended),
        "interest_hits": interest_hits,
        "quality_hits": quality_hits,
        "joint_hits": joint_hits,
        "interest_hit_rate": _rate(interest_hits, len(daily_feedback)),
        # Ignore is explicit weak negative interest but does not label scientific quality.
        "quality_hit_rate": _rate(quality_hits, len(completed_recommended)),
        "joint_hit_rate": _rate(joint_hits, len(completed_recommended)),
        "confirmed_interest_precision": _rate(interest_hits, len(completed_recommended)),
        "confirmed_quality_precision": _rate(quality_hits, len(completed_recommended)),
        "relevance_brier_score": (
            round(sum(relevance_errors) / len(relevance_errors), 4) if relevance_errors else None
        ),
        "research_brier_score": (
            round(sum(research_errors) / len(research_errors), 4) if research_errors else None
        ),
        "relevance_prediction_sample_count": len(relevance_errors),
        "research_prediction_sample_count": len(research_errors),
    }

    signals: list[dict[str, Any]] = []
    for paper in all_feedback:
        structured = completion_summary_structured(paper, include_user_attitude=True)
        metadata = paper.metadata_json or {}
        provenance = metadata.get(FEEDBACK_PROVENANCE_KEY)
        attribution = resolve_feedback_attribution(paper, by_item=by_item, by_paper=by_paper)
        is_completed = paper.status == "completed"
        interest_level = paper_interest_level(paper)
        user_attitude = (structured or {}).get("user_attitude") if is_completed else None
        attitude_status = (
            "user_confirmed"
            if isinstance(provenance, dict) and provenance.get("active", True)
            else "legacy_unverified"
        )
        if attitude_status == "legacy_unverified" and isinstance(user_attitude, str):
            placeholder_markers = (
                "没有可确认的用户态度",
                "没有可确认的用户",
                "尚未明确表态",
                "无法可靠判断用户",
            )
            if any(marker in user_attitude for marker in placeholder_markers):
                user_attitude = None
        if is_completed:
            evidence_key = (
                provenance.get("evidence_key")
                if isinstance(provenance, dict)
                else f"paper:{paper.id}:feedback:legacy"
            )
        else:
            ignore_provenance = metadata.get(IGNORE_PROVENANCE_KEY)
            evidence_key = (
                ignore_provenance.get("evidence_key")
                if isinstance(ignore_provenance, dict)
                else f"paper:{paper.id}:ignore:legacy"
            )
        signals.append(
            {
                "evidence_key": evidence_key,
                "paper_id": paper.id,
                "title": paper.title,
                "summary": _bounded_text(paper.summary, 500),
                "source_updated_at": model_dict(paper)["updated_at"],
                "outcome": "confirmed" if is_completed else "ignored",
                "confirmed_at": (
                    provenance.get("confirmed_at")
                    if isinstance(provenance, dict)
                    else (
                        model_dict(paper).get("reading_completed_at")
                        if is_completed
                        else model_dict(paper).get("updated_at")
                    )
                ),
                "labels": {
                    "interest": (
                        {
                            "interested": "positive",
                            "neutral": "neutral",
                            "ignored": "negative",
                        }.get(interest_level, "neutral")
                        if is_completed
                        else "negative_weak"
                    ),
                    "quality": (
                        {
                            "excellent": "positive",
                            "normal": "neutral",
                            "poor": "negative",
                        }[paper_quality_level(paper) or "normal"]
                    )
                    if is_completed
                    else "unknown",
                },
                "tags": tags_by_paper.get(paper.id, []) if is_completed else [],
                "graph_nodes": nodes_by_paper.get(paper.id, []),
                "user_attitude": _bounded_text(
                    user_attitude if is_completed else paper.ignored_reason,
                    1_000,
                ),
                "attitude_status": attitude_status if is_completed else "explicit_ignore_reason",
                "personal_comment": _bounded_text(
                    metadata.get("personal_comment") if is_completed else None,
                    1_000,
                ),
                "attribution": attribution,
            }
        )
    signals.sort(
        key=lambda item: item.get("confirmed_at") or "",
        reverse=True,
    )
    screening_signals = []
    for paper in screening_papers:
        screening = paper.screening_feedback or {}
        screening_signals.append(
            {
                "evidence_key": screening.get("evidence_key")
                or f"paper:{paper.id}:screening:legacy",
                "paper_id": paper.id,
                "title": paper.title,
                "summary": _bounded_text(paper.summary or paper.abstract, 500),
                "outcome": "screening",
                "confirmed_at": screening.get("updated_at") or model_dict(paper).get("updated_at"),
                "labels": {
                    "interest": screening.get("interest_level") or "unknown",
                    "quality": screening.get("quality_level") or "unknown",
                },
                "agent_scores": {
                    "topic_relevance": paper.topic_relevance_score,
                    "author_influence": paper.author_influence_score,
                    "quality": paper.research_score,
                },
                "tags": [],
                "graph_nodes": [],
                "user_attitude": _bounded_text(screening.get("comment"), 1_000),
                "personal_comment": _bounded_text(screening.get("comment"), 1_000),
                "attribution": {"kind": "screening", "resolution": "explicit"},
            }
        )

    setting = session.get(AppSetting, "initial_interest_domains")
    seed_interests = setting.value_json if setting and isinstance(setting.value_json, list) else []
    derived_memory = session.get(Memory, DERIVED_INTEREST_PROFILE_ID)
    interest_memory_rows = list(
        session.scalars(
            select(Memory)
            .where(
                Memory.kind == "interest_profile",
                Memory.active.is_(True),
                Memory.id != DERIVED_INTEREST_PROFILE_ID,
            )
            .order_by(Memory.updated_at.desc())
            .limit(20)
        )
    )
    memories = [
        {
            "id": item.id,
            "title": item.title,
            "content": _bounded_text(item.content, 2_000),
            "source": item.source,
            "updated_at": model_dict(item)["updated_at"],
        }
        for item in interest_memory_rows
    ]
    semantic_profile = (
        {
            "id": derived_memory.id,
            "content": _bounded_text(derived_memory.content, 4_000),
            "source": derived_memory.source,
            "metadata": derived_memory.metadata_json or {},
            "updated_at": model_dict(derived_memory)["updated_at"],
        }
        if derived_memory and derived_memory.active
        else None
    )
    workflow_memories = [
        {
            "id": item.id,
            "title": item.title,
            "content": _bounded_text(item.content, 2_000),
            "source": item.source,
            "updated_at": model_dict(item)["updated_at"],
        }
        for item in session.scalars(
            select(Memory)
            .where(Memory.kind == "agent_experience", Memory.active.is_(True))
            .order_by(Memory.updated_at.desc())
            .limit(20)
        )
    ]
    graph_nodes = [
        {
            "id": node.id,
            "name": node.name,
            "core_question": node.core_question,
            "domain": node.domain,
        }
        for node in session.scalars(
            select(ResearchNode)
            .where(ResearchNode.is_active.is_(True))
            .order_by(ResearchNode.name)
            .limit(100)
        )
    ]
    latest_revision = (
        max(
            (item.get("confirmed_at") or "" for item in signals),
            default="",
        )
        or None
    )
    feedback_source_payload = [
        {
            "evidence_key": item["evidence_key"],
            "labels": item["labels"],
            "tags": item["tags"],
            "attitude": item["user_attitude"],
            "personal_comment": item["personal_comment"],
            "attribution": item["attribution"],
        }
        for item in [*signals, *screening_signals]
    ]
    feedback_digest = _digest(feedback_source_payload)
    if semantic_profile:
        stale = semantic_profile["metadata"].get("feedback_digest") != feedback_digest
        semantic_profile["stale"] = stale
        if stale:
            previous_digest = semantic_profile["metadata"].get("feedback_digest")
            semantic_profile["content"] = None
            semantic_profile["metadata"] = {
                "schema_version": semantic_profile["metadata"].get("schema_version"),
                "feedback_digest": previous_digest,
            }
    source_payload = {
        "schema_version": INTEREST_CONTEXT_SCHEMA_VERSION,
        "feedback_digest": feedback_digest,
        "semantic_profile": (
            {
                "id": semantic_profile["id"],
                "updated_at": semantic_profile["updated_at"],
            }
            if semantic_profile
            else None
        ),
        "memories": [{"id": item["id"], "updated_at": item["updated_at"]} for item in memories],
        "workflow_memories": [
            {"id": item["id"], "updated_at": item["updated_at"]} for item in workflow_memories
        ],
        "seed_interests": seed_interests,
        "graph_nodes": graph_nodes,
    }
    return {
        "schema_version": INTEREST_CONTEXT_SCHEMA_VERSION,
        "source_digest": _digest(source_payload),
        "feedback_digest": feedback_digest,
        "feedback_revision": latest_revision,
        "layers": {
            "explicit_priors": memories,
            "semantic_profile": semantic_profile,
            "workflow_lessons": workflow_memories,
            "seed_interests": seed_interests,
            "confirmed_topic_profile": {
                "formula": "(positive - negative) / (support + 2)",
                "confirmed_paper_count": len(completed),
                "unclassified_confirmed_count": unclassified_completed,
                "topic_count": topic_count,
                "topics_truncated": topic_count > len(topics),
                "topics": topics,
            },
            "research_graph": graph_nodes,
        },
        "calibration": calibration,
        "recent_feedback": signals[:recent_limit],
        "screening_feedback": screening_signals[:recent_limit],
        "screening_evidence_count": len(screening_signals),
    }


def interest_context_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    profile = context["layers"]["confirmed_topic_profile"]
    return {
        "schema_version": context["schema_version"],
        "source_digest": context["source_digest"],
        "feedback_digest": context["feedback_digest"],
        "feedback_revision": context["feedback_revision"],
        "calibration": context["calibration"],
        "semantic_profile": context["layers"]["semantic_profile"],
        "screening_evidence_count": context.get("screening_evidence_count", 0),
        "confirmed_topic_profile": {
            **{key: value for key, value in profile.items() if key != "topics"},
            "topics": profile["topics"][:20],
        },
    }
