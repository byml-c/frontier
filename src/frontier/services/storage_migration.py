from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ArchitectureDocument, Paper
from .common import log_operation

MIGRATION_NAME = "storage.arxiv_link_only.v1"


def _path_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _architecture_document_papers(session: Session) -> set[str]:
    keep: set[str] = set()
    for document in session.scalars(select(ArchitectureDocument)).all():
        assets = (document.scene_json or {}).get("assets")
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("src") or "").startswith("asset://document/"):
                keep.add(document.paper_id)
                break
    return keep


def run_storage_migration(session: Session, *, dry_run: bool = True) -> dict[str, Any]:
    """Remove derived arXiv PDFs/text/document caches; keep uploads and architecture refs."""
    keep_documents = _architecture_document_papers(session)
    papers = list(session.scalars(select(Paper)).all())
    pdf_deleted = text_deleted = document_deleted = 0
    bytes_freed = 0
    errors: list[str] = []
    keep_pdf_ids = {
        paper.id
        for paper in papers
        if paper.source == "upload" or not paper.arxiv_id
    }
    keep_pdf_paths = {
        Path(paper.pdf_path).resolve()
        for paper in papers
        if paper.id in keep_pdf_ids and paper.pdf_path and Path(paper.pdf_path).exists()
    }

    for paper in papers:
        pdf_path = Path(paper.pdf_path) if paper.pdf_path else settings.pdf_dir / f"{paper.id}.pdf"
        if paper.id not in keep_pdf_ids and pdf_path.exists():
            size = _path_bytes(pdf_path)
            if not dry_run:
                try:
                    pdf_path.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(f"pdf:{paper.id}:{exc}")
                else:
                    paper.pdf_path = None
            bytes_freed += size
            pdf_deleted += 1
        text_path = settings.text_dir / f"{paper.id}.json"
        if text_path.exists():
            size = _path_bytes(text_path)
            if not dry_run:
                try:
                    text_path.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(f"text:{paper.id}:{exc}")
            bytes_freed += size
            text_deleted += 1
        document_root = settings.document_dir / paper.id
        if document_root.exists() and paper.id not in keep_documents:
            size = _path_bytes(document_root)
            if not dry_run:
                try:
                    shutil.rmtree(document_root)
                except OSError as exc:
                    errors.append(f"document:{paper.id}:{exc}")
            bytes_freed += size
            document_deleted += 1

    for text_path in settings.text_dir.glob("*"):
        if text_path.is_file() and text_path.name not in {
            f"{paper.id}.json" for paper in papers
        }:
            size = _path_bytes(text_path)
            if not dry_run:
                text_path.unlink(missing_ok=True)
            bytes_freed += size
            text_deleted += 1

    for directory in settings.document_dir.iterdir() if settings.document_dir.exists() else []:
        if not directory.is_dir() or directory.name in keep_documents:
            continue
        if any(paper.id == directory.name for paper in papers):
            continue
        size = _path_bytes(directory)
        if not dry_run:
            shutil.rmtree(directory, ignore_errors=True)
        bytes_freed += size
        document_deleted += 1

    for pdf_path in settings.pdf_dir.glob("*.pdf"):
        if pdf_path.resolve() in keep_pdf_paths:
            continue
        if any(paper.id == pdf_path.stem for paper in papers):
            continue
        size = _path_bytes(pdf_path)
        if not dry_run:
            pdf_path.unlink(missing_ok=True)
        bytes_freed += size
        pdf_deleted += 1

    result = {
        "migration": MIGRATION_NAME,
        "dry_run": dry_run,
        "deleted_pdfs": pdf_deleted,
        "deleted_text_caches": text_deleted,
        "deleted_document_bundles": document_deleted,
        "bytes_freed": bytes_freed,
        "kept_upload_pdfs": len(keep_pdf_paths),
        "kept_architecture_document_bundles": sorted(keep_documents),
        "errors": errors,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if not dry_run:
        manifest = settings.data_dir / "migrations" / f"{MIGRATION_NAME}.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log_operation(
            session,
            actor="system",
            action=MIGRATION_NAME,
            entity_type="storage",
            detail=result,
        )
    return result
