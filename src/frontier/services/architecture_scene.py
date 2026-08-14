from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..architecture_schemas import ArchitectureScene
from ..config import settings
from ..models import ArchitectureAsset, ArchitectureDocument, Paper, TaskRun

ARCHITECTURE_SCHEMA_VERSION = 1
USER_LOCK_LEASE_SECONDS = 300
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_ARCHITECTURE_ASSET_BYTES = 10 * 1024 * 1024


class ArchitectureError(RuntimeError):
    pass


class ArchitectureLockedError(ArchitectureError):
    def __init__(self, owner_type: str) -> None:
        self.owner_type = owner_type
        super().__init__(f"Architecture is locked by {owner_type}")


class ArchitectureRevisionError(ArchitectureError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Architecture revision changed from {expected} to {actual}")


class ArchitectureValidationError(ArchitectureError):
    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        self.diagnostics = diagnostics
        super().__init__("Architecture scene validation failed")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _document(session: Session, paper_id: str, *, create: bool = False) -> ArchitectureDocument | None:
    item = session.get(ArchitectureDocument, paper_id)
    if item is None and create:
        if session.get(Paper, paper_id) is None:
            raise ArchitectureError("Paper not found")
        item = ArchitectureDocument(paper_id=paper_id)
        session.add(item)
        session.flush()
    return item


def _task_lock_is_live(session: Session, owner_id: str | None) -> bool:
    if not owner_id:
        return False
    task = session.get(TaskRun, owner_id)
    return bool(task and task.status in {"queued", "running"})


def _paper_pdf_version(paper: Paper) -> str | None:
    if not paper.pdf_path:
        return None
    path = Path(paper.pdf_path)
    if not path.is_file():
        return None
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def refresh_lock(session: Session, item: ArchitectureDocument) -> bool:
    if not item.lock_owner_type:
        return False
    if item.lock_owner_type == "agent":
        active = _task_lock_is_live(session, item.lock_owner_id)
    else:
        expires = _aware(item.lock_expires_at)
        active = bool(expires and expires > datetime.now(UTC))
    if active:
        return True
    item.lock_owner_type = None
    item.lock_owner_id = None
    item.lock_expires_at = None
    return False


def architecture_document_payload(session: Session, paper_id: str) -> dict[str, Any]:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise ArchitectureError("Paper not found")
    item = _document(session, paper_id)
    if item is None:
        return {
            "paperId": paper_id,
            "status": "missing",
            "revision": 0,
            "schemaVersion": ARCHITECTURE_SCHEMA_VERSION,
            "scene": None,
            "taskId": None,
            "error": None,
            "lock": None,
            "sourcePdfVersion": None,
            "currentPdfVersion": _paper_pdf_version(paper),
            "stale": False,
        }
    lock_active = refresh_lock(session, item)
    if session.is_modified(item):
        session.commit()
    current_pdf_version = _paper_pdf_version(paper)
    generation_task = (
        session.get(TaskRun, item.generation_task_id) if item.generation_task_id else None
    )
    progress = None
    if generation_task and isinstance(generation_task.result_json, dict):
        candidate = generation_task.result_json.get("architectureProgress")
        if isinstance(candidate, dict):
            progress = candidate
    return {
        "paperId": item.paper_id,
        "status": item.status,
        "revision": item.revision,
        "schemaVersion": item.schema_version,
        "scene": item.scene_json or None,
        "taskId": item.generation_task_id,
        "progress": progress,
        "error": item.error,
        "lock": (
            {
                "ownerType": item.lock_owner_type,
                "ownerId": item.lock_owner_id,
                "expiresAt": (
                    _aware(item.lock_expires_at).isoformat() if item.lock_expires_at else None
                ),
            }
            if lock_active
            else None
        ),
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
        "sourcePdfVersion": item.source_pdf_version,
        "currentPdfVersion": current_pdf_version,
        "stale": bool(
            item.source_pdf_version
            and current_pdf_version
            and item.source_pdf_version != current_pdf_version
        ),
    }


def acquire_architecture_lock(
    session: Session,
    paper_id: str,
    *,
    owner_type: Literal["user", "agent"],
    owner_id: str,
) -> ArchitectureDocument:
    item = _document(session, paper_id, create=True)
    assert item is not None
    active = refresh_lock(session, item)
    if active and (item.lock_owner_type, item.lock_owner_id) != (owner_type, owner_id):
        raise ArchitectureLockedError(str(item.lock_owner_type))
    if owner_type == "agent":
        task = session.get(TaskRun, owner_id)
        if task is None or task.status not in {"queued", "running"}:
            raise ArchitectureError("Agent task is not active")
        task_paper_id = task.payload_json.get("paper_id")
        if task_paper_id and task_paper_id != paper_id:
            raise ArchitectureError("Agent task belongs to another paper")
        item.lock_expires_at = None
    else:
        item.lock_expires_at = datetime.now(UTC) + timedelta(seconds=USER_LOCK_LEASE_SECONDS)
    item.lock_owner_type = owner_type
    item.lock_owner_id = owner_id
    session.commit()
    return item


def release_architecture_lock(
    session: Session,
    paper_id: str,
    *,
    owner_type: Literal["user", "agent"],
    owner_id: str,
) -> None:
    item = _document(session, paper_id)
    if item is None:
        return
    refresh_lock(session, item)
    if (item.lock_owner_type, item.lock_owner_id) == (owner_type, owner_id):
        item.lock_owner_type = None
        item.lock_owner_id = None
        item.lock_expires_at = None
        session.commit()


def release_agent_lock_for_task(session: Session, task_id: str) -> None:
    items = session.scalars(
        select(ArchitectureDocument).where(
            ArchitectureDocument.lock_owner_type == "agent",
            ArchitectureDocument.lock_owner_id == task_id,
        )
    ).all()
    for item in items:
        item.lock_owner_type = None
        item.lock_owner_id = None
        item.lock_expires_at = None


def cleanup_stale_architecture_locks(session: Session) -> int:
    items = session.scalars(
        select(ArchitectureDocument).where(ArchitectureDocument.lock_owner_type.is_not(None))
    ).all()
    cleaned = 0
    for item in items:
        before = item.lock_owner_type
        refresh_lock(session, item)
        cleaned += int(before is not None and item.lock_owner_type is None)
    if cleaned:
        session.commit()
    return cleaned


def _diagnostic(
    level: Literal["error", "warning"],
    code: str,
    message: str,
    entity_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"level": level, "code": code, "message": message}
    if entity_id:
        payload["entityId"] = entity_id
    return payload


def _rect(node: Any) -> tuple[float, float, float, float]:
    default = (240.0, 96.0) if node.type == "module" else (240.0, 150.0)
    width = node.size.width if node.size else default[0]
    height = node.size.height if node.size else default[1]
    return node.position.x, node.position.y, width, height


def _overlap_ratio(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    width = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    height = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / max(1.0, min(aw * ah, bw * bh))


def validate_architecture_scene(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        scene = ArchitectureScene.model_validate(raw)
    except ValidationError as exc:
        diagnostics = [
            _diagnostic(
                "error",
                "schema.invalid",
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}",
            )
            for error in exc.errors(include_url=False)
        ]
        return None, diagnostics

    diagnostics: list[dict[str, Any]] = []
    collections = {
        "node": scene.nodes,
        "tensor": scene.tensors,
        "edge": scene.edges,
        "annotation": scene.annotations,
        "group": scene.groups,
        "source": scene.sources,
        "asset": scene.assets,
    }
    seen: dict[str, str] = {}
    for kind, values in collections.items():
        for value in values:
            previous = seen.get(value.id)
            if previous:
                diagnostics.append(
                    _diagnostic(
                        "error",
                        "id.duplicate",
                        f"ID {value.id!r} is shared by {previous} and {kind}",
                        value.id,
                    )
                )
            else:
                seen[value.id] = kind

    nodes = {item.id: item for item in scene.nodes}
    modules = {item.id: item for item in scene.nodes if item.type == "module"}
    tensors = {item.id: item for item in scene.tensors}
    edges = {item.id: item for item in scene.edges}
    annotations = {item.id: item for item in scene.annotations}
    groups = {item.id: item for item in scene.groups}
    sources = {item.id: item for item in scene.sources}
    assets = {item.id: item for item in scene.assets}

    def require_ids(owner_id: str, values: list[str], available: dict[str, Any], code: str) -> None:
        for value in values:
            if value not in available:
                diagnostics.append(
                    _diagnostic("error", code, f"{owner_id} references missing ID {value}", owner_id)
                )

    for node in scene.nodes:
        if node.parent_module_id and node.parent_module_id not in modules:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "node.parent_missing",
                    f"Parent module {node.parent_module_id} does not exist",
                    node.id,
                )
            )
        if node.group_id and node.group_id not in groups:
            diagnostics.append(
                _diagnostic(
                    "error", "node.group_missing", f"Group {node.group_id} does not exist", node.id
                )
            )
        require_ids(node.id, node.annotation_ids, annotations, "node.annotation_missing")
        require_ids(node.id, node.source_ids, sources, "node.source_missing")
        if node.type == "data" and node.primary_example_id:
            example_ids = {example.id for example in node.examples}
            if node.primary_example_id not in example_ids:
                diagnostics.append(
                    _diagnostic(
                        "error",
                        "data.primary_example_missing",
                        f"Primary example {node.primary_example_id} does not exist",
                        node.id,
                    )
                )
        if node.type == "data":
            for example in node.examples:
                if example.asset_id and example.asset_id not in assets:
                    diagnostics.append(
                        _diagnostic(
                            "error",
                            "data.asset_missing",
                            f"Example {example.id} references missing asset {example.asset_id}",
                            node.id,
                        )
                    )

    edge_relations: dict[tuple[str, str, str], list[Any]] = {}
    for edge in scene.edges:
        edge_relations.setdefault((edge.source, edge.target, edge.kind), []).append(edge)
        if edge.source not in nodes or edge.target not in nodes:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "edge.endpoint_missing",
                    f"Edge endpoints must exist: {edge.source} -> {edge.target}",
                    edge.id,
                )
            )
        if edge.tensor_id and edge.tensor_id not in tensors:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "edge.tensor_missing",
                    f"Tensor {edge.tensor_id} does not exist",
                    edge.id,
                )
            )
        require_ids(edge.id, edge.source_ids, sources, "edge.source_missing")

    for (source, target, kind), relation_edges in edge_relations.items():
        if len(relation_edges) > 1:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "edge.duplicate_relation",
                    f"{len(relation_edges)} {kind} edges repeat {source} -> {target}; combine the processing label and Tensor reference on one semantic edge",
                    relation_edges[0].id,
                )
            )

    reported_reciprocals: set[frozenset[str]] = set()
    for (source, target, kind), relation_edges in edge_relations.items():
        if kind != "flow" or source == target:
            continue
        reverse_edges = edge_relations.get((target, source, kind))
        pair = frozenset((source, target))
        if not reverse_edges or pair in reported_reciprocals:
            continue
        reported_reciprocals.add(pair)
        diagnostics.append(
            _diagnostic(
                "warning",
                "edge.reciprocal_flow",
                f"Reciprocal flow edges connect {source} and {target}; keep internal Q/K/V, split, and residual operations inside one Module unless the paper explicitly defines recurrence or feedback",
                relation_edges[0].id,
            )
        )

    for tensor in scene.tensors:
        require_ids(tensor.id, tensor.source_ids, sources, "tensor.source_missing")
    for annotation in scene.annotations:
        require_ids(annotation.id, annotation.source_ids, sources, "annotation.source_missing")
        if annotation.target:
            targets = {"node": nodes, "edge": edges, "tensor": tensors}[annotation.target.type]
            if annotation.target.id not in targets:
                diagnostics.append(
                    _diagnostic(
                        "error",
                        "annotation.target_missing",
                        f"Annotation target {annotation.target.id} does not exist",
                        annotation.id,
                    )
                )

    for node in scene.nodes:
        chain: set[str] = set()
        current = node
        while current.parent_module_id:
            if current.parent_module_id in chain or current.parent_module_id == node.id:
                diagnostics.append(
                    _diagnostic(
                        "error", "node.parent_cycle", "Module hierarchy contains a cycle", node.id
                    )
                )
                break
            chain.add(current.parent_module_id)
            parent = modules.get(current.parent_module_id)
            if parent is None:
                break
            current = parent

    root_nodes = [node for node in scene.nodes if not node.parent_module_id]
    for index, first in enumerate(root_nodes):
        for second in root_nodes[index + 1 :]:
            if _overlap_ratio(_rect(first), _rect(second)) >= 0.2:
                diagnostics.append(
                    _diagnostic(
                        "warning",
                        "geometry.node_overlap",
                        f"Canonical nodes {first.id} and {second.id} overlap substantially",
                        first.id,
                    )
                )

    for node in root_nodes:
        if not node.group_id:
            continue
        group = groups.get(node.group_id)
        if not group:
            continue
        x, y, width, height = _rect(node)
        gx, gy = group.position.x, group.position.y
        if x < gx or y < gy or x + width > gx + group.size.width or y + height > gy + group.size.height:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "geometry.group_overflow",
                    f"Node {node.id} is outside group {group.id}",
                    node.id,
                )
            )

    for node in scene.nodes:
        if not node.parent_module_id:
            continue
        parent = modules.get(node.parent_module_id)
        if not parent or not parent.size:
            continue
        x, y, width, height = _rect(node)
        if x < 0 or y < 0 or x + width > parent.size.width or y + height > parent.size.height:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "geometry.parent_overflow",
                    f"Child node {node.id} is outside the explicit size of {parent.id}",
                    node.id,
                )
            )

    if scene.nodes and not scene.sources:
        diagnostics.append(
            _diagnostic(
                "warning",
                "source.none",
                "Scene has no source references; material architecture claims cannot be verified",
            )
        )
    for asset in scene.assets:
        if not (
            asset.src.startswith("asset://architecture/")
            or asset.src.startswith("asset://document/")
        ):
            diagnostics.append(
                _diagnostic(
                    "error",
                    "asset.scheme_invalid",
                    f"Asset {asset.id} uses an unsupported source",
                    asset.id,
                )
            )

    normalized = scene.model_dump(mode="json", by_alias=True)
    return normalized, diagnostics


def commit_architecture_scene(
    session: Session,
    paper_id: str,
    raw_scene: dict[str, Any],
    *,
    expected_revision: int,
    owner_type: Literal["user", "agent"],
    owner_id: str,
) -> tuple[ArchitectureDocument, list[dict[str, Any]]]:
    item = _document(session, paper_id, create=True)
    assert item is not None
    if not refresh_lock(session, item) or (item.lock_owner_type, item.lock_owner_id) != (
        owner_type,
        owner_id,
    ):
        raise ArchitectureLockedError(str(item.lock_owner_type or "none"))
    if item.revision != expected_revision:
        raise ArchitectureRevisionError(expected_revision, item.revision)
    normalized, diagnostics = validate_architecture_scene(raw_scene)
    if normalized is None or any(item["level"] == "error" for item in diagnostics):
        raise ArchitectureValidationError(diagnostics)
    item.scene_json = normalized
    item.schema_version = normalized["schemaVersion"]
    item.layout_epoch = normalized["layoutEpoch"]
    item.revision += 1
    item.status = "ready"
    item.error = None
    paper = session.get(Paper, paper_id)
    item.source_pdf_version = _paper_pdf_version(paper) if paper else None
    session.commit()
    return item, diagnostics


def mark_architecture_generation_failed(session: Session, task_id: str, error: str) -> None:
    item = session.scalar(
        select(ArchitectureDocument).where(ArchitectureDocument.generation_task_id == task_id)
    )
    if item:
        item.status = "ready" if item.scene_json else "failed"
        item.error = error
        item.generation_task_id = None
        release_agent_lock_for_task(session, task_id)
        session.commit()


def validate_image_bytes(content: bytes, media_type: str) -> str:
    if media_type not in ALLOWED_IMAGE_TYPES:
        raise ArchitectureError("Only PNG, JPEG, and WebP images are supported")
    if not content or len(content) > MAX_ARCHITECTURE_ASSET_BYTES:
        raise ArchitectureError("Architecture image must be between 1 byte and 10 MB")
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.shape[0] < 1 or image.shape[1] < 1:
        raise ArchitectureError("Uploaded file is not a valid image")
    if not all(math.isfinite(float(value)) for value in image.shape[:2]):
        raise ArchitectureError("Uploaded image has invalid dimensions")
    return ALLOWED_IMAGE_TYPES[media_type]


def save_architecture_asset(
    session: Session,
    paper_id: str,
    *,
    content: bytes,
    media_type: str,
    original_name: str,
) -> ArchitectureAsset:
    if session.get(Paper, paper_id) is None:
        raise ArchitectureError("Paper not found")
    suffix = validate_image_bytes(content, media_type)
    digest = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(ArchitectureAsset).where(
            ArchitectureAsset.paper_id == paper_id,
            ArchitectureAsset.content_hash == digest,
        )
    )
    if existing:
        return existing
    stored_name = f"{digest}{suffix}"
    root = settings.architecture_dir / paper_id / "assets"
    root.mkdir(parents=True, exist_ok=True)
    target = root / stored_name
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    item = ArchitectureAsset(
        paper_id=paper_id,
        content_hash=digest,
        original_name=Path(original_name).name[:255] or stored_name,
        stored_name=stored_name,
        media_type=media_type,
        size_bytes=len(content),
    )
    session.add(item)
    session.commit()
    return item


def architecture_asset_path(session: Session, paper_id: str, asset_id: str) -> tuple[ArchitectureAsset, Path]:
    item = session.get(ArchitectureAsset, asset_id)
    if item is None or item.paper_id != paper_id:
        raise FileNotFoundError(asset_id)
    path = settings.architecture_dir / paper_id / "assets" / item.stored_name
    if not path.is_file():
        raise FileNotFoundError(asset_id)
    return item, path
