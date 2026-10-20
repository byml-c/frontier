from __future__ import annotations

import json
import logging
import shutil
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Paper
from .paper_sources import arxiv_pdf_url, download_pdf_to_temp, source_version

DOCUMENT_SCHEMA_VERSION = 10
_RUNTIME_ROOT = settings.runtime_dir / "documents"
_PENDING_LIMIT = settings.document_runtime_slots * 2
_lock = threading.RLock()
_slots: OrderedDict[str, Path] = OrderedDict()
_status: dict[str, dict[str, Any]] = {}
_inflight: dict[str, Future[None]] = {}
_executor = ThreadPoolExecutor(
    max_workers=settings.document_runtime_slots,
    thread_name_prefix="document-runtime",
)
logger = logging.getLogger(__name__)


class DocumentReaderUnavailable(RuntimeError):
    pass


def reset_document_runtime() -> None:
    """Discard every temporary document slot from a previous process."""
    with _lock:
        _slots.clear()
        _status.clear()
        _inflight.clear()
        shutil.rmtree(_RUNTIME_ROOT, ignore_errors=True)
        _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)


def _slot_dir(paper_id: str) -> Path:
    with _lock:
        existing = _slots.get(paper_id)
        if existing is not None:
            _slots.move_to_end(paper_id)
            return existing
        used = {path.name for path in _slots.values()}
        free = [
            _RUNTIME_ROOT / f"slot-{index}"
            for index in range(1, settings.document_runtime_slots + 1)
            if f"slot-{index}" not in used
        ]
        if free:
            target = free[0]
        else:
            evictable = next(
                (
                    candidate
                    for candidate in _slots
                    if candidate not in _inflight
                ),
                None,
            )
            if evictable is None:
                raise DocumentReaderUnavailable("全部 5 个文档槽位正在使用，请稍后重试")
            target = _slots.pop(evictable)
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        _slots[paper_id] = target
        return target


def _set_status(paper_id: str, version: str, **values: Any) -> dict[str, Any]:
    payload = {
        "paper_id": paper_id,
        "version": version,
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        **values,
    }
    with _lock:
        _status[paper_id] = payload
    return payload


def _reader_payload(paper_id: str, version: str) -> dict[str, Any] | None:
    with _lock:
        root = _slots.get(paper_id)
    if root is None:
        return None
    path = root / "reader.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("schema_version") != DOCUMENT_SCHEMA_VERSION:
        return None
    if payload.get("version") != version:
        return None
    return payload


def document_status(session: Session, paper: Paper) -> dict[str, Any]:
    del session
    version = source_version(paper)
    with _lock:
        status = _status.get(paper.id)
    if (
        status is not None
        and status.get("version") == version
        and status.get("status") in {"processing", "failed"}
    ):
        return status
    payload = _reader_payload(paper.id, version)
    if payload is not None:
        return payload
    return {
        "paper_id": paper.id,
        "version": version,
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "status": "missing",
    }


def queue_document_conversion(paper_id: str, *, force: bool = False) -> bool:
    with _lock:
        if paper_id in _inflight and not force:
            return False
        if len(_inflight) >= _PENDING_LIMIT:
            return False
        version = f"paper:{paper_id}"
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                return False
            version = source_version(paper)
        _set_status(
            paper_id,
            version,
            status="processing",
            phase="queued",
            phase_label="等待文档槽位",
            progress=1,
            started_at=datetime.now(UTC).isoformat(),
        )
        future = _executor.submit(_convert_task, paper_id, version, force)
        _inflight[paper_id] = future
    return True


def queue_document_conversions(paper_ids: list[str], *, limit: int = 4) -> list[str]:
    unique_ids = list(dict.fromkeys(paper_ids))[: max(0, limit)]
    return [paper_id for paper_id in unique_ids if queue_document_conversion(paper_id)]


def _convert_task(paper_id: str, version: str, force: bool) -> None:
    temporary_pdf: Path | None = None
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                _set_status(paper_id, version, status="failed", error="论文记录不存在")
                return
            _set_status(
                paper_id,
                version,
                status="processing",
                phase="preparing",
                phase_label="正在准备论文源",
                progress=8,
            )
            slot = _slot_dir(paper_id)
            cache = slot / "reader.json"
            if cache.exists() and not force:
                cached = _reader_payload(paper_id, version)
                if cached is not None:
                    _set_status(paper_id, version, status="ready", progress=100)
                    return
            if paper.pdf_path and Path(paper.pdf_path).is_file():
                pdf_path = Path(paper.pdf_path)
            elif arxiv_pdf_url(paper):
                temporary_pdf = download_pdf_to_temp(paper)
                pdf_path = temporary_pdf
            else:
                pdf_path = None
            payload: dict[str, Any] | None = None
            if paper.arxiv_id:
                from .arxiv_reader import ArxivHtmlUnavailable, convert_from_arxiv_html

                identifier = paper.arxiv_id
                if paper.arxiv_version and not identifier.endswith(
                    f"v{paper.arxiv_version}"
                ):
                    identifier = f"{identifier}v{paper.arxiv_version}"
                try:
                    payload = convert_from_arxiv_html(
                        identifier,
                        pdf_path,
                        paper_id=paper_id,
                        version=version,
                        root=slot,
                        schema_version=DOCUMENT_SCHEMA_VERSION,
                        known_authors=paper.authors or [],
                        persist_html=False,
                        fetch_assets=False,
                    )
                except ArxivHtmlUnavailable as exc:
                    _set_status(
                        paper_id,
                        version,
                        status="failed",
                        retryable=True,
                        error=str(exc),
                    )
                    return
            if payload is None:
                if pdf_path is None or not settings.enable_docling:
                    _set_status(
                        paper_id,
                        version,
                        status="failed",
                        retryable=False,
                        error="没有可用的 arXiv HTML，且当前部署未启用 PDF 结构化转换",
                    )
                    return
                from .document_reader import convert_with_docling

                payload = convert_with_docling(
                    pdf_path,
                    paper_id=paper_id,
                    version=version,
                    root=slot,
                )
            if payload.get("reader_source") == "arxiv_html":
                from .author_metadata import apply_arxiv_html_author_metadata

                apply_arxiv_html_author_metadata(
                    session,
                    paper,
                    payload.get("author_metadata") or {},
                    source_url=str(payload.get("source_url") or "") or None,
                )
                session.commit()
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(cache)
            _set_status(paper_id, version, status="ready", progress=100)
    except Exception as exc:
        logger.exception("Document runtime conversion failed for %s", paper_id)
        _set_status(paper_id, version, status="failed", retryable=True, error=str(exc))
    finally:
        if temporary_pdf is not None:
            temporary_pdf.unlink(missing_ok=True)
        with _lock:
            _inflight.pop(paper_id, None)


def document_asset(paper_id: str, filename: str) -> Path:
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    if Path(filename).name != filename or Path(filename).suffix.lower() not in allowed:
        raise ValueError("Invalid document asset")
    with _lock:
        root = _slots.get(paper_id)
    if root is None:
        raise FileNotFoundError(filename)
    path = root / "assets" / filename
    if not path.exists():
        raise FileNotFoundError(filename)
    return path


def shutdown_document_worker(*, wait_seconds: float = 2.0) -> None:
    del wait_seconds
    with _lock:
        _inflight.clear()
        _slots.clear()
        _status.clear()
    shutil.rmtree(_RUNTIME_ROOT, ignore_errors=True)
    _executor.shutdown(wait=False, cancel_futures=True)
