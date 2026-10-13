from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper, ResearchGroup, ResearchGroupPaper
from .common import RESEARCH_GROUP_PROFILE_KEY, normalize_title


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _group_key(value: str | None) -> str:
    return re.sub(r"[^\w]+", "", normalize_title(value or ""), flags=re.UNICODE)


def _profile_from_paper(paper: Paper) -> dict[str, Any]:
    profile = (paper.metadata_json or {}).get(RESEARCH_GROUP_PROFILE_KEY)
    return dict(profile) if isinstance(profile, dict) else {}


def upsert_group_from_paper(session: Session, paper: Paper) -> ResearchGroup | None:
    """Merge an Agent's structured group evidence into the durable group index."""
    profile = _profile_from_paper(paper)
    name = _clean(profile.get("name")) or _clean(paper.primary_affiliation)
    institution = _clean(profile.get("institution")) or _clean(paper.primary_affiliation)
    if not name or not institution:
        return None
    normalized_name = _group_key(name)
    normalized_institution = _group_key(institution)
    group = session.scalar(
        select(ResearchGroup).where(
            ResearchGroup.normalized_name == normalized_name,
            ResearchGroup.normalized_institution == normalized_institution,
        )
    )
    if group is None:
        group = ResearchGroup(
            name=name,
            normalized_name=normalized_name,
            institution=institution,
            normalized_institution=normalized_institution,
        )
        session.add(group)
        session.flush()
    elif group.verification_status != "verified":
        group.name = name
        group.institution = institution

    aliases = [str(item).strip() for item in profile.get("aliases", []) if str(item).strip()]
    group.aliases = sorted({*(group.aliases or []), *aliases}, key=str.casefold)
    focus = [str(item).strip() for item in profile.get("research_focus", []) if str(item).strip()]
    group.research_focus = sorted({*(group.research_focus or []), *focus}, key=str.casefold)
    contributions = [item for item in profile.get("representative_contributions", []) if isinstance(item, dict)]
    existing_contributions = [item for item in (group.representative_contributions or []) if isinstance(item, dict)]
    seen_contributions = {
        (str(item.get("topic") or "").strip(), str(item.get("summary") or "").strip())
        for item in existing_contributions
    }
    for contribution in contributions:
        key = (str(contribution.get("topic") or "").strip(), str(contribution.get("summary") or "").strip())
        if key not in seen_contributions and any(key):
            existing_contributions.append(
                {
                    "topic": key[0],
                    "summary": key[1],
                    "evidence": str(contribution.get("evidence") or "").strip(),
                }
            )
            seen_contributions.add(key)
    group.representative_contributions = existing_contributions[-20:]
    homepage = _clean(profile.get("homepage_url"))
    if homepage and not group.homepage_url:
        group.homepage_url = homepage
    summary = _clean(profile.get("summary")) or _clean(
        (paper.metadata_json or {}).get("research_group_summary")
    )
    if summary and group.verification_status != "verified":
        group.representative_summary = summary
    confidence = _clean(profile.get("confidence"))
    if confidence in {"high", "medium", "low"}:
        group.confidence = confidence
    group.evidence_json = {
        **(group.evidence_json or {}),
        "last_paper_id": paper.id,
        "last_note": _clean(profile.get("evidence")),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    contribution = _clean(profile.get("paper_contribution")) or _clean(paper.agent_comment)
    link = session.scalar(
        select(ResearchGroupPaper).where(
            ResearchGroupPaper.group_id == group.id,
            ResearchGroupPaper.paper_id == paper.id,
        )
    )
    if link is None:
        link = ResearchGroupPaper(group_id=group.id, paper_id=paper.id)
        session.add(link)
    link.contribution_summary = contribution
    link.evidence_json = {"source": "agent", "note": _clean(profile.get("evidence"))}
    representative = bool(profile.get("is_representative"))
    if representative:
        link.is_representative = True
    return group


def serialize_group(session: Session, group: ResearchGroup, *, detail: bool = False) -> dict[str, Any]:
    links = list(
        session.scalars(
            select(ResearchGroupPaper)
            .where(ResearchGroupPaper.group_id == group.id)
            .order_by(ResearchGroupPaper.is_representative.desc(), ResearchGroupPaper.created_at.desc())
        )
    )
    payload = {
        "id": group.id,
        "name": group.name,
        "institution": group.institution,
        "homepage_url": group.homepage_url,
        "aliases": group.aliases or [],
        "research_focus": group.research_focus or [],
        "representative_contributions": group.representative_contributions or [],
        "representative_summary": group.representative_summary,
        "confidence": group.confidence,
        "verification_status": group.verification_status,
        "paper_count": len(links),
        "representative_paper_count": sum(1 for link in links if link.is_representative),
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
    }
    if detail:
        papers = {paper.id: paper for paper in session.scalars(select(Paper).where(Paper.id.in_([link.paper_id for link in links])))}
        payload["papers"] = [
            {
                "id": link.paper_id,
                "title": papers.get(link.paper_id).title if papers.get(link.paper_id) else None,
                "published_at": papers.get(link.paper_id).published_at.isoformat() if papers.get(link.paper_id) and papers.get(link.paper_id).published_at else None,
                "contribution_summary": link.contribution_summary,
                "is_representative": link.is_representative,
                "role": link.role,
            }
            for link in links
        ]
        payload["evidence"] = group.evidence_json or {}
    return payload


def group_for_paper(session: Session, paper_id: str) -> ResearchGroup | None:
    return session.scalar(
        select(ResearchGroup)
        .join(ResearchGroupPaper, ResearchGroupPaper.group_id == ResearchGroup.id)
        .where(ResearchGroupPaper.paper_id == paper_id)
        .order_by(ResearchGroupPaper.is_representative.desc())
    )
