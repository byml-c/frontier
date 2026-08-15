from __future__ import annotations

from collections import deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import (
    NodeProgress,
    Paper,
    PaperNode,
    ResearchEdge,
    ResearchIdea,
    ResearchNode,
)
from ..schemas import (
    EdgeCreate,
    IdeaCreate,
    IdeaUpdate,
    NodeCreate,
    NodeUpdate,
    PaperNodeCreate,
    ProgressCreate,
)
from ..services.common import log_operation, model_dict

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _node_or_404(session: Session, node_id: str) -> ResearchNode:
    node = session.get(ResearchNode, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Research node not found")
    return node


def _node_summary(session: Session, node: ResearchNode) -> dict:
    payload = model_dict(node)
    payload["paper_count"] = len(
        session.scalars(
            select(PaperNode.id)
            .join(Paper, Paper.id == PaperNode.paper_id)
            .where(PaperNode.node_id == node.id, Paper.status == "completed")
        ).all()
    )
    return payload


@router.get("/nodes")
def list_nodes(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    domain: str | None = None,
    active: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    statement = select(ResearchNode).where(ResearchNode.is_active == active)
    if q:
        token = f"%{q}%"
        statement = statement.where(
            or_(
                ResearchNode.name.ilike(token),
                ResearchNode.core_question.ilike(token),
                ResearchNode.summary.ilike(token),
            )
        )
    if domain:
        statement = statement.where(ResearchNode.domain == domain)
    nodes = session.scalars(statement.order_by(ResearchNode.name).limit(limit)).all()
    return [_node_summary(session, node) for node in nodes]


@router.post("/nodes")
def create_node(payload: NodeCreate, session: Annotated[Session, Depends(get_session)]) -> dict:
    name = payload.name.strip()
    core_question = payload.core_question.strip()
    if not name or not core_question:
        raise HTTPException(status_code=422, detail="Node name and core question are required")
    existing = session.scalar(
        select(ResearchNode).where(func.lower(ResearchNode.name) == name.lower())
    )
    if existing:
        return _node_summary(session, existing)
    node = ResearchNode(
        **{
            **payload.model_dump(),
            "name": name,
            "core_question": core_question,
        }
    )
    session.add(node)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="graph.node_created",
        entity_type="research_node",
        entity_id=node.id,
    )
    session.commit()
    return _node_summary(session, node)


@router.get("/nodes/{node_id}")
def get_node(node_id: str, session: Annotated[Session, Depends(get_session)]) -> dict:
    node = _node_or_404(session, node_id)
    payload = _node_summary(session, node)
    parent_rows = session.execute(
        select(ResearchNode.id, ResearchNode.name)
        .join(ResearchEdge, ResearchEdge.source_id == ResearchNode.id)
        .where(ResearchEdge.target_id == node_id)
    ).all()
    child_rows = session.execute(
        select(ResearchNode.id, ResearchNode.name)
        .join(ResearchEdge, ResearchEdge.target_id == ResearchNode.id)
        .where(ResearchEdge.source_id == node_id)
    ).all()
    paper_rows = session.execute(
        select(Paper, PaperNode.reason, PaperNode.is_key)
        .join(PaperNode, PaperNode.paper_id == Paper.id)
        .where(PaperNode.node_id == node_id, Paper.status == "completed")
        .order_by(PaperNode.is_key.desc(), Paper.published_at.desc())
    ).all()
    progress = session.scalars(
        select(NodeProgress)
        .where(NodeProgress.node_id == node_id)
        .order_by(NodeProgress.happened_at.desc(), NodeProgress.created_at.desc())
    ).all()
    ideas = session.scalars(
        select(ResearchIdea)
        .where(ResearchIdea.node_id == node_id)
        .order_by(ResearchIdea.created_at.desc())
    ).all()
    payload.update(
        {
            "parents": [dict(row._mapping) for row in parent_rows],
            "children": [dict(row._mapping) for row in child_rows],
            "papers": [
                {
                    "id": paper.id,
                    "title": paper.title,
                    "first_author": paper.first_author,
                    "published_at": paper.published_at.isoformat() if paper.published_at else None,
                    "status": paper.status,
                    "is_quality": paper.is_quality,
                    "reason": reason,
                    "is_key": is_key,
                }
                for paper, reason, is_key in paper_rows
            ],
            "progress": [model_dict(item) for item in progress],
            "ideas": [model_dict(item) for item in ideas],
        }
    )
    return payload


@router.patch("/nodes/{node_id}")
def update_node(
    node_id: str,
    payload: NodeUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    node = _node_or_404(session, node_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(node, key, value)
    log_operation(
        session,
        actor="agent",
        action="graph.node_updated",
        entity_type="research_node",
        entity_id=node.id,
        detail={"fields": sorted(changes)},
    )
    session.commit()
    return _node_summary(session, node)


@router.post("/edges")
def create_edge(payload: EdgeCreate, session: Annotated[Session, Depends(get_session)]) -> dict:
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=400, detail="A node cannot link to itself")
    _node_or_404(session, payload.source_id)
    _node_or_404(session, payload.target_id)
    existing = session.scalar(
        select(ResearchEdge).where(
            ResearchEdge.source_id == payload.source_id,
            ResearchEdge.target_id == payload.target_id,
        )
    )
    if existing:
        return model_dict(existing)
    edge = ResearchEdge(**payload.model_dump())
    session.add(edge)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="graph.edge_created",
        entity_type="research_edge",
        entity_id=edge.id,
    )
    session.commit()
    return model_dict(edge)


@router.post("/paper-links")
def attach_paper(
    payload: PaperNodeCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    paper = session.get(Paper, payload.paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="Paper must complete user-confirmed reading before graph attachment",
        )
    _node_or_404(session, payload.node_id)
    existing = session.scalar(
        select(PaperNode).where(
            PaperNode.paper_id == payload.paper_id,
            PaperNode.node_id == payload.node_id,
        )
    )
    if existing:
        existing.reason = payload.reason or existing.reason
        existing.is_key = payload.is_key
        session.commit()
        return model_dict(existing)
    item = PaperNode(**payload.model_dump())
    session.add(item)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="graph.paper_attached",
        entity_type="paper_node",
        entity_id=item.id,
        detail={"paper_id": payload.paper_id, "node_id": payload.node_id},
    )
    session.commit()
    return model_dict(item)


@router.post("/nodes/{node_id}/progress")
def add_progress(
    node_id: str,
    payload: ProgressCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _node_or_404(session, node_id)
    if payload.paper_id:
        paper = session.get(Paper, payload.paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        if paper.status != "completed":
            raise HTTPException(
                status_code=409,
                detail="Paper must complete user-confirmed reading before updating node progress",
            )
        existing = session.scalar(
            select(NodeProgress).where(
                NodeProgress.node_id == node_id,
                NodeProgress.paper_id == payload.paper_id,
            )
        )
        if existing:
            changed = False
            for field in ("title", "content", "happened_at"):
                value = getattr(payload, field)
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if changed:
                log_operation(
                    session,
                    actor="agent",
                    action="graph.progress_updated",
                    entity_type="node_progress",
                    entity_id=existing.id,
                    detail={"paper_id": payload.paper_id, "node_id": node_id},
                )
                session.commit()
            return model_dict(existing)
    item = NodeProgress(node_id=node_id, **payload.model_dump())
    session.add(item)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="graph.progress_added",
        entity_type="node_progress",
        entity_id=item.id,
    )
    session.commit()
    return model_dict(item)


@router.post("/nodes/{node_id}/ideas")
def add_idea(
    node_id: str,
    payload: IdeaCreate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _node_or_404(session, node_id)
    item = ResearchIdea(node_id=node_id, **payload.model_dump())
    session.add(item)
    session.flush()
    log_operation(
        session,
        actor="agent",
        action="graph.idea_added",
        entity_type="research_idea",
        entity_id=item.id,
    )
    session.commit()
    return model_dict(item)


@router.patch("/ideas/{idea_id}")
def update_idea(
    idea_id: str,
    payload: IdeaUpdate,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    idea = session.get(ResearchIdea, idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(idea, key, value)
    session.commit()
    return model_dict(idea)


@router.get("/subgraph")
def get_subgraph(
    session: Annotated[Session, Depends(get_session)],
    focus_id: str | None = None,
    limit: int = Query(default=80, ge=1, le=300),
) -> dict:
    all_edges = session.scalars(select(ResearchEdge)).all()
    active_ids = set(
        session.scalars(
            select(ResearchNode.id).where(ResearchNode.is_active.is_(True))
        ).all()
    )
    if focus_id:
        _node_or_404(session, focus_id)
        adjacency: dict[str, set[str]] = {}
        for edge in all_edges:
            if edge.source_id not in active_ids or edge.target_id not in active_ids:
                continue
            adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
            adjacency.setdefault(edge.target_id, set()).add(edge.source_id)
        selected = {focus_id}
        queue: deque[str] = deque([focus_id])
        while queue and len(selected) < limit:
            current = queue.popleft()
            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor not in selected:
                    selected.add(neighbor)
                    queue.append(neighbor)
                    if len(selected) >= limit:
                        break
    else:
        selected = set(
            session.scalars(
                select(ResearchNode.id)
                .where(ResearchNode.is_active.is_(True))
                .order_by(ResearchNode.updated_at.desc())
                .limit(limit)
            ).all()
        )
    nodes = session.scalars(select(ResearchNode).where(ResearchNode.id.in_(selected))).all()
    edges = [
        edge for edge in all_edges if edge.source_id in selected and edge.target_id in selected
    ]
    boundary_connections: dict[str, set[str]] = {}
    for edge in all_edges:
        if edge.source_id in selected and edge.target_id in active_ids - selected:
            boundary_connections.setdefault(edge.target_id, set()).add(edge.source_id)
        elif edge.target_id in selected and edge.source_id in active_ids - selected:
            boundary_connections.setdefault(edge.source_id, set()).add(edge.target_id)
    boundary_limit = min(24, max(6, limit // 3))
    boundary_ids = sorted(boundary_connections)[:boundary_limit]
    boundary_nodes = session.scalars(
        select(ResearchNode).where(ResearchNode.id.in_(boundary_ids))
    ).all()
    return {
        "focus_id": focus_id,
        "nodes": [_node_summary(session, node) for node in nodes],
        "links": [model_dict(edge) for edge in edges],
        "boundary_nodes": [
            {
                **_node_summary(session, node),
                "connects_to": sorted(boundary_connections[node.id]),
            }
            for node in boundary_nodes
        ],
        "has_more": len(selected) < len(active_ids),
    }
