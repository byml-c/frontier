from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Paper
from .ingest import download_paper_pdf

_conversion_lock = threading.Lock()
_converting: set[str] = set()
# Increment whenever cached reader output semantics change. Old caches and old
# worker statuses then become stale and are rebuilt lazily on the next access.
DOCUMENT_SCHEMA_VERSION = 6
logger = logging.getLogger(__name__)


class DocumentReaderUnavailable(RuntimeError):
    """Raised when no enabled structured-document source can serve a paper."""


def _paths(paper_id: str) -> tuple[Path, Path, Path]:
    root = settings.document_dir / paper_id
    return root, root / "reader.json", root / "status.json"


def _pdf_version(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _processing_status_is_live(paper_id: str, status: dict[str, Any]) -> bool:
    with _conversion_lock:
        if paper_id in _converting:
            return True
    updated = status.get("updated_at") or status.get("started_at")
    if not updated:
        return False
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(str(updated))).total_seconds()
    except ValueError:
        return False
    if status.get("phase") == "queued":
        return age < 90
    estimate = int(status.get("estimated_seconds_high") or 0)
    return age < max(600, estimate * 2)


def _document_payload_is_current(
    payload: dict[str, Any], version: str, *, allow_missing_version: bool = False
) -> bool:
    if payload.get("schema_version") != DOCUMENT_SCHEMA_VERSION:
        return False
    payload_version = payload.get("version")
    return payload_version == version or (allow_missing_version and payload_version is None)


def document_status(session: Session, paper: Paper) -> dict[str, Any]:
    root, cache, status_path = _paths(paper.id)
    path = download_paper_pdf(session, paper)
    version = _pdf_version(path)
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        if _document_payload_is_current(payload, version):
            return payload
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") == "failed" and _document_payload_is_current(status, version):
            return status
        if (
            status.get("status") == "processing"
            and _document_payload_is_current(status, version, allow_missing_version=True)
            and _processing_status_is_live(paper.id, status)
        ):
            return {**status, "version": version}
    root.mkdir(parents=True, exist_ok=True)
    return {"paper_id": paper.id, "version": version, "status": "missing"}


def queue_document_conversion(paper_id: str, *, force: bool = False) -> bool:
    with _conversion_lock:
        if paper_id in _converting:
            return False
        _converting.add(paper_id)
    # Persist the queued state before starting the worker. The API may be polled
    # again before the thread opens the PDF; without this timestamp the UI looked
    # permanently frozen at its local 3% placeholder.
    now = datetime.now(UTC).isoformat()
    _write_status(
        paper_id,
        {
            "paper_id": paper_id,
            "schema_version": DOCUMENT_SCHEMA_VERSION,
            "status": "processing",
            "phase": "queued",
            "phase_label": "等待转换任务启动",
            "progress": 1,
            "stage": 0,
            "stage_count": 4,
            "started_at": now,
            "updated_at": now,
        },
    )
    thread = threading.Thread(
        target=_convert_document_task,
        args=(paper_id, force),
        name=f"document-reader-{paper_id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:
        with _conversion_lock:
            _converting.discard(paper_id)
        _write_status(
            paper_id,
            {
                "paper_id": paper_id,
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "status": "failed",
                "started_at": now,
                "error": f"转换任务启动失败：{exc}",
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        logger.exception("Failed to start document conversion thread for %s", paper_id)
        return False
    return True


def queue_document_conversions(paper_ids: list[str], *, limit: int = 4) -> list[str]:
    """Queue a small paper batch on one worker to protect low-resource servers."""
    unique_ids = list(dict.fromkeys(paper_ids))[: max(0, limit)]
    claimed: list[str] = []
    with _conversion_lock:
        for paper_id in unique_ids:
            if paper_id not in _converting:
                _converting.add(paper_id)
                claimed.append(paper_id)
    if not claimed:
        return []

    now = datetime.now(UTC).isoformat()
    for paper_id in claimed:
        _write_status(
            paper_id,
            {
                "paper_id": paper_id,
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "status": "processing",
                "phase": "queued",
                "phase_label": "等待每日推荐文本缓存",
                "progress": 1,
                "stage": 0,
                "stage_count": 4,
                "started_at": now,
                "updated_at": now,
            },
        )

    def convert_batch() -> None:
        for paper_id in claimed:
            _convert_document_task(paper_id, False)

    thread = threading.Thread(
        target=convert_batch,
        name="document-reader-featured",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:
        with _conversion_lock:
            for paper_id in claimed:
                _converting.discard(paper_id)
        for paper_id in claimed:
            _write_status(
                paper_id,
                {
                    "paper_id": paper_id,
                    "schema_version": DOCUMENT_SCHEMA_VERSION,
                    "status": "failed",
                    "started_at": now,
                    "error": f"每日推荐文本缓存启动失败：{exc}",
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        logger.exception("Failed to start featured document conversion batch")
        return []
    return claimed


def _write_status(paper_id: str, payload: dict[str, Any]) -> None:
    root, _cache, status_path = _paths(paper_id)
    root.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(status_path)


def _estimate_conversion_seconds(page_count: int) -> tuple[int, int]:
    # Includes the one-off model warm-up. CPU speed varies substantially across
    # deployment hosts, so expose an honest range rather than a false countdown.
    return max(25, 15 + page_count * 2), max(60, 45 + page_count * 7)


def _convert_document_task(paper_id: str, force: bool) -> None:
    version: str | None = None
    started_at = datetime.now(UTC).isoformat()
    page_count = 0
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                _write_status(
                    paper_id,
                    {
                        "paper_id": paper_id,
                        "schema_version": DOCUMENT_SCHEMA_VERSION,
                        "status": "failed",
                        "started_at": started_at,
                        "error": "论文记录不存在，转换任务无法启动",
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                )
                return
            path = download_paper_pdf(session, paper)
            version = _pdf_version(path)
            import pymupdf

            with pymupdf.open(path) as source:
                page_count = source.page_count
            estimate_low, estimate_high = _estimate_conversion_seconds(page_count)

            def report_progress(
                phase: str,
                phase_label: str,
                progress: int,
                stage: int,
                *,
                processed_items: int | None = None,
                total_items: int | None = None,
            ) -> None:
                payload: dict[str, Any] = {
                    "paper_id": paper_id,
                    "version": version,
                    "schema_version": DOCUMENT_SCHEMA_VERSION,
                    "status": "processing",
                    "phase": phase,
                    "phase_label": phase_label,
                    "progress": progress,
                    "stage": stage,
                    "stage_count": 4,
                    "page_count": page_count,
                    "started_at": started_at,
                    "estimated_seconds_low": estimate_low,
                    "estimated_seconds_high": estimate_high,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                if processed_items is not None:
                    payload["processed_items"] = processed_items
                if total_items is not None:
                    payload["total_items"] = total_items
                _write_status(paper_id, payload)

            report_progress("preparing", "正在准备 PDF", 5, 1)
            root, cache, _status_path = _paths(paper_id)
            if cache.exists() and not force:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if _document_payload_is_current(cached, version):
                    _write_status(
                        paper_id,
                        {
                            "paper_id": paper_id,
                            "version": version,
                            "schema_version": DOCUMENT_SCHEMA_VERSION,
                            "status": "ready",
                            "updated_at": datetime.now(UTC).isoformat(),
                        },
                    )
                    return
            payload: dict[str, Any] | None = None
            arxiv_html_error: str | None = None
            if paper.arxiv_id:
                from .arxiv_reader import ArxivHtmlUnavailable, convert_from_arxiv_html

                try:
                    source_identifier = paper.arxiv_id
                    if paper.arxiv_version and not source_identifier.endswith(
                        f"v{paper.arxiv_version}"
                    ):
                        source_identifier = f"{source_identifier}v{paper.arxiv_version}"
                    payload = convert_from_arxiv_html(
                        source_identifier,
                        path,
                        paper_id=paper_id,
                        version=version,
                        root=root,
                        schema_version=DOCUMENT_SCHEMA_VERSION,
                        on_progress=report_progress,
                    )
                except ArxivHtmlUnavailable as exc:
                    arxiv_html_error = str(exc)
                    logger.info(
                        "arXiv HTML unavailable for %s: %s",
                        paper_id,
                        exc,
                    )
            if payload is None:
                if not settings.enable_docling:
                    if paper.arxiv_id:
                        detail = f"（{arxiv_html_error}）" if arxiv_html_error else ""
                        raise DocumentReaderUnavailable(
                            "arXiv 暂未提供这篇论文的 HTML 正文"
                            f"{detail}。当前服务器已关闭 PDF 结构化转换，请使用 PDF 模式。"
                        )
                    raise DocumentReaderUnavailable(
                        "这篇论文没有可用的 arXiv HTML 正文。当前服务器已关闭 PDF "
                        "结构化转换，请使用 PDF 模式。"
                    )
                report_progress("parsing", "正在解析 PDF", 20, 2)
                payload = convert_with_docling(
                    path,
                    paper_id=paper_id,
                    version=version,
                    root=root,
                    on_progress=report_progress,
                )
            report_progress("finalizing", "正在写入阅读缓存", 98, 4)
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(cache)
            _write_status(
                paper_id,
                {
                    "paper_id": paper_id,
                    "version": version,
                    "schema_version": DOCUMENT_SCHEMA_VERSION,
                    "status": "ready",
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
    except DocumentReaderUnavailable as exc:
        logger.info("Structured reader unavailable for %s: %s", paper_id, exc)
        _write_status(
            paper_id,
            {
                "paper_id": paper_id,
                "version": version,
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "status": "failed",
                "retryable": False,
                "page_count": page_count or None,
                "started_at": started_at,
                "error": str(exc),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
    except Exception as exc:
        logger.exception("Document conversion failed for %s", paper_id)
        _write_status(
            paper_id,
            {
                "paper_id": paper_id,
                "version": version,
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "status": "failed",
                "page_count": page_count or None,
                "started_at": started_at,
                "error": str(exc),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
    finally:
        with _conversion_lock:
            _converting.discard(paper_id)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value or "text"))


def _provenance(item: Any) -> tuple[int, dict[str, float] | None]:
    provenance = getattr(item, "prov", None) or []
    if not provenance:
        return 1, None
    first = provenance[0]
    page = int(getattr(first, "page_no", 1) or 1)
    bbox = getattr(first, "bbox", None)
    if bbox is None:
        return page, None
    return page, {
        "left": float(getattr(bbox, "l", 0)),
        "top": float(getattr(bbox, "t", 0)),
        "right": float(getattr(bbox, "r", 0)),
        "bottom": float(getattr(bbox, "b", 0)),
    }


def _block_id(item: Any, index: int) -> str:
    reference = str(getattr(item, "self_ref", "") or f"item-{index}")
    return hashlib.sha1(reference.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _render_pdf_region(
    source: Any,
    *,
    page_number: int,
    bbox: dict[str, float] | None,
    target: Path,
    dpi: int,
) -> bool:
    if not bbox or page_number < 1 or page_number > source.page_count:
        return False
    import pymupdf

    page = source[page_number - 1]
    # Docling provenance uses a bottom-left origin; PyMuPDF uses top-left.
    left = max(0.0, bbox["left"] - 3)
    right = min(float(page.rect.width), bbox["right"] + 3)
    top = max(0.0, float(page.rect.height) - bbox["top"] - 3)
    bottom = min(float(page.rect.height), float(page.rect.height) - bbox["bottom"] + 3)
    clip = pymupdf.Rect(left, top, right, bottom) & page.rect
    if clip.is_empty or clip.width < 1 or clip.height < 1:
        return False
    page.get_pixmap(clip=clip, dpi=dpi, alpha=False).save(target)
    return True


def convert_with_docling(
    path: Path,
    *,
    paper_id: str,
    version: str,
    root: Path,
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    if not settings.enable_docling:
        raise DocumentReaderUnavailable(
            "Docling 已在当前部署中关闭；如需使用，请安装 docling 可选依赖并启用配置。"
        )
    import pymupdf
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.object_detection_engine_options import (
            TransformersObjectDetectionEngineOptions,
        )
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            LayoutObjectDetectionOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import PictureItem, TableItem
    except ModuleNotFoundError as exc:
        raise DocumentReaderUnavailable(
            "Docling 可选依赖尚未安装，请运行 `uv sync --extra docling` 后重试。"
        ) from exc

    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    pipeline = PdfPipelineOptions()
    pipeline.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
    pipeline.layout_options = LayoutObjectDetectionOptions(
        engine_options=TransformersObjectDetectionEngineOptions(compile_model=False)
    )
    # arXiv and user-submitted research PDFs normally contain a text layer. OCR adds
    # a large model startup cost and can degrade equations, so keep the fast digital
    # document path as the reader default.
    pipeline.do_ocr = False
    pipeline.images_scale = 1.5
    pipeline.generate_page_images = True
    pipeline.generate_picture_images = True
    # Compilation has a costly cold start and makes a web request depend on the
    # host C/CUDA toolchain. Eager CPU inference is slower per page but predictable
    # across laptops and the production VM, and the result is cached permanently.
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}
    )
    if on_progress:
        on_progress("parsing", "正在解析版面结构", 20, 2)
    result = converter.convert(path)
    if on_progress:
        on_progress("structuring", "正在整理正文结构", 68, 3)
    document = result.document
    raw = document.export_to_dict()
    (root / "docling.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    items = list(document.iterate_items())
    total_items = len(items)
    if on_progress:
        on_progress(
            "assets",
            "正在生成高清图片与表格",
            72,
            3,
            processed_items=0,
            total_items=total_items,
        )
    source = pymupdf.open(path)
    try:
        update_interval = max(1, total_items // 20)
        for index, (item, level) in enumerate(items):
            kind = _value(getattr(item, "label", "text"))
            block_id = _block_id(item, index)
            page, bbox = _provenance(item)
            block: dict[str, Any] = {
                "id": block_id,
                "kind": kind,
                "page_number": page,
                "bbox": bbox,
                "level": int(level or 0),
            }
            if isinstance(item, TableItem):
                block["kind"] = "table"
                # Kept for search and accessibility; the reader displays only the
                # source-faithful high-resolution crop.
                block["text"] = item.export_to_markdown(document)
                block["caption"] = item.caption_text(document)
            elif isinstance(item, PictureItem):
                block["kind"] = "picture"
                block["caption"] = item.caption_text(document)
            else:
                text = str(getattr(item, "text", "") or getattr(item, "orig", "") or "")
                if not text.strip() and kind != "formula":
                    continue
                block["text"] = text
                if kind == "formula":
                    equation_number = text.strip().rsplit(" ", 1)[-1]
                    if equation_number.startswith("(") and equation_number.endswith(")"):
                        block["caption"] = f"Equation {equation_number[1:-1]}"
            if block["kind"] in {"table", "picture", "formula"}:
                filename = f"{block_id}.png"
                target = assets / filename
                dpi = (
                    400 if block["kind"] == "formula" else 320 if block["kind"] == "table" else 240
                )
                rendered = _render_pdf_region(
                    source,
                    page_number=page,
                    bbox=bbox,
                    target=target,
                    dpi=dpi,
                )
                if not rendered and hasattr(item, "get_image"):
                    image = item.get_image(document)
                    if image is not None:
                        image.save(target, "PNG")
                        rendered = True
                if rendered:
                    block["image_url"] = f"/api/papers/{paper_id}/document/assets/{filename}"
            blocks.append(block)
            processed_items = index + 1
            if on_progress and (
                processed_items == total_items or processed_items % update_interval == 0
            ):
                progress = 72 + round(23 * processed_items / max(1, total_items))
                on_progress(
                    "assets",
                    "正在生成高清图片与表格",
                    progress,
                    3,
                    processed_items=processed_items,
                    total_items=total_items,
                )
    finally:
        source.close()
    return {
        "paper_id": paper_id,
        "version": version,
        "status": "ready",
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "reader_source": "docling",
        "title": str(getattr(document, "name", "") or path.stem),
        "blocks": blocks,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def document_asset(paper_id: str, filename: str) -> Path:
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    if Path(filename).name != filename or Path(filename).suffix.lower() not in allowed:
        raise ValueError("Invalid document asset")
    root, _cache, _status = _paths(paper_id)
    path = root / "assets" / filename
    if not path.exists():
        raise FileNotFoundError(filename)
    return path
