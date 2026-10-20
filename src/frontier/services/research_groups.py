from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Paper, PaperTag, ResearchGroup, ResearchGroupPaper, Tag
from .common import RESEARCH_GROUP_PROFILE_KEY, normalize_title
from .paper_metadata import METADATA_ENRICHMENT_KEY


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _group_key(value: str | None) -> str:
    return re.sub(r"[^\w]+", "", normalize_title(value or ""), flags=re.UNICODE)


def _profile_from_paper(paper: Paper) -> dict[str, Any]:
    profile = (paper.metadata_json or {}).get(RESEARCH_GROUP_PROFILE_KEY)
    return dict(profile) if isinstance(profile, dict) else {}


def ensure_group_tag(session: Session, group: ResearchGroup) -> Tag:
    """Return the durable research-group tag and create it once when needed."""
    display_name = group.name.strip()
    tag = session.scalar(select(Tag).where(Tag.research_group_id == group.id))
    if tag is not None:
        return tag
    if tag is None:
        tag = session.scalar(
            select(Tag).where(func.lower(Tag.name) == display_name.lower())
        )
        if tag is not None and tag.research_group_id not in {None, group.id}:
            tag = None
            display_name = f"{group.name.strip()}（{group.institution.strip()}）"
    if tag is None:
        tag = Tag(name=display_name, research_group_id=group.id)
        session.add(tag)
        session.flush()
    elif tag.research_group_id is None:
        tag.research_group_id = group.id
        tag.name = display_name
    return tag


def _group_tag(session: Session, group_id: str) -> Tag | None:
    return session.scalar(select(Tag).where(Tag.research_group_id == group_id))


def _remove_group_tag_from_paper(session: Session, paper_id: str, group_id: str) -> None:
    tag = _group_tag(session, group_id)
    if tag is not None:
        session.query(PaperTag).filter(
            PaperTag.paper_id == paper_id, PaperTag.tag_id == tag.id
        ).delete()


def assign_research_group(
    session: Session,
    paper: Paper,
    group: ResearchGroup,
    *,
    role: str = "author_affiliation",
    contribution_summary: str | None = None,
    evidence: dict[str, Any] | None = None,
    is_representative: bool = False,
) -> None:
    """Bind the paper to one canonical group; keep ResearchGroupPaper as a view."""
    if paper.research_group_id and paper.research_group_id != group.id:
        _remove_group_tag_from_paper(session, paper.id, paper.research_group_id)
    paper.research_group_id = group.id
    state = (paper.metadata_json or {}).get(METADATA_ENRICHMENT_KEY)
    if isinstance(state, dict) and state.get("status") in {"queued", "running"}:
        paper.metadata_json = {
            **(paper.metadata_json or {}),
            METADATA_ENRICHMENT_KEY: {
                **state,
                "status": "completed",
                "updated_at": datetime.now(UTC).isoformat(),
            },
        }
    session.query(ResearchGroupPaper).filter(
        ResearchGroupPaper.paper_id == paper.id
    ).delete()
    session.add(
        ResearchGroupPaper(
            group_id=group.id,
            paper_id=paper.id,
            role=role,
            contribution_summary=contribution_summary,
            evidence_json=evidence or {"source": "agent"},
            is_representative=is_representative,
        )
    )
    tag = ensure_group_tag(session, group)
    exists = session.scalar(
        select(PaperTag.id).where(
            PaperTag.paper_id == paper.id,
            PaperTag.tag_id == tag.id,
        )
    )
    if exists is None:
        session.add(PaperTag(paper_id=paper.id, tag_id=tag.id))
        session.flush()


def clear_research_group(session: Session, paper: Paper) -> None:
    if paper.research_group_id:
        _remove_group_tag_from_paper(session, paper.id, paper.research_group_id)
    paper.research_group_id = None
    paper.metadata_json = {
        key: value
        for key, value in (paper.metadata_json or {}).items()
        if key != RESEARCH_GROUP_PROFILE_KEY
    }
    session.query(ResearchGroupPaper).filter(
        ResearchGroupPaper.paper_id == paper.id
    ).delete()


def upsert_group_from_paper(session: Session, paper: Paper) -> ResearchGroup | None:
    """Merge an Agent's structured group evidence into the durable group index."""
    if paper.research_group_id:
        existing = session.get(ResearchGroup, paper.research_group_id)
        if existing is not None:
            tag = ensure_group_tag(session, existing)
            exists = session.scalar(
                select(PaperTag.id).where(
                    PaperTag.paper_id == paper.id,
                    PaperTag.tag_id == tag.id,
                )
            )
            if exists is None:
                session.add(PaperTag(paper_id=paper.id, tag_id=tag.id))
            return existing
    profile = _profile_from_paper(paper)
    # Affiliation alone is not enough to create a durable group. The ingestion
    # metadata Agent must explicitly confirm a lab/group identity or bind one.
    name = _clean(profile.get("name"))
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

    assign_research_group(
        session,
        paper,
        group,
        contribution_summary=_clean(profile.get("paper_contribution"))
        or _clean(paper.agent_comment),
        evidence={"source": "agent", "note": _clean(profile.get("evidence"))},
        is_representative=bool(profile.get("is_representative")),
    )
    return group


def serialize_group(session: Session, group: ResearchGroup, *, detail: bool = False) -> dict[str, Any]:
    papers = list(
        session.scalars(
            select(Paper)
            .where(Paper.research_group_id == group.id)
            .order_by(Paper.published_at.desc(), Paper.created_at.desc())
        )
    )
    links = list(
        session.scalars(
            select(ResearchGroupPaper).where(ResearchGroupPaper.group_id == group.id)
        )
    )
    if not papers:
        # Legacy rows from before Paper.research_group_id became canonical.
        links = list(
            session.scalars(
                select(ResearchGroupPaper)
                .where(ResearchGroupPaper.group_id == group.id)
                .order_by(
                    ResearchGroupPaper.is_representative.desc(),
                    ResearchGroupPaper.created_at.desc(),
                )
            )
        )
        papers = list(
            session.scalars(
                select(Paper).where(Paper.id.in_([link.paper_id for link in links]))
            )
        )
    links_by_paper = {link.paper_id: link for link in links}
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
        "paper_count": len(papers),
        "representative_paper_count": sum(1 for link in links if link.is_representative),
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
    }
    if detail:
        payload["papers"] = [
            {
                "id": paper.id,
                "title": paper.title,
                "published_at": paper.published_at.isoformat() if paper.published_at else None,
                "contribution_summary": (
                    links_by_paper[paper.id].contribution_summary
                    if paper.id in links_by_paper
                    else None
                ),
                "is_representative": (
                    links_by_paper[paper.id].is_representative
                    if paper.id in links_by_paper
                    else False
                ),
                "role": (
                    links_by_paper[paper.id].role
                    if paper.id in links_by_paper
                    else "author_affiliation"
                ),
            }
            for paper in papers
        ]
        payload["evidence"] = group.evidence_json or {}
    return payload


def group_for_paper(session: Session, paper_id: str) -> ResearchGroup | None:
    paper = session.get(Paper, paper_id)
    if paper is not None and paper.research_group_id:
        group = session.get(ResearchGroup, paper.research_group_id)
        if group is not None:
            return group
    return session.scalar(
        select(ResearchGroup)
        .join(ResearchGroupPaper, ResearchGroupPaper.group_id == ResearchGroup.id)
        .where(ResearchGroupPaper.paper_id == paper_id)
        .order_by(ResearchGroupPaper.is_representative.desc())
    )


def migrate_research_group_links(session: Session) -> int:
    """Make Paper.research_group_id canonical and project groups into tags."""
    updated = 0
    for group in session.scalars(select(ResearchGroup)).all():
        ensure_group_tag(session, group)
    links = list(session.scalars(select(ResearchGroupPaper)).all())
    for link in links:
        paper = session.get(Paper, link.paper_id)
        group = session.get(ResearchGroup, link.group_id)
        if paper is None or group is None:
            continue
        if paper.research_group_id is None:
            paper.research_group_id = group.id
            updated += 1
        if paper.research_group_id != group.id:
            continue
        tag = ensure_group_tag(session, group)
        exists = session.scalar(
            select(PaperTag.id).where(
                PaperTag.paper_id == paper.id,
                PaperTag.tag_id == tag.id,
            )
        )
        if exists is None:
            session.add(PaperTag(paper_id=paper.id, tag_id=tag.id))
    session.flush()
    return updated
