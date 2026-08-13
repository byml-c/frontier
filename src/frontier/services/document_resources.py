from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

RESOURCE_KINDS = {"formula", "figure", "table", "reference"}


def structured_document_resources(
    document: dict[str, Any],
    *,
    kind: str | None = None,
    block_id: str | None = None,
    asset_resolver: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    """Expose cached reader blocks as a compact, Agent-oriented resource model."""
    if kind and kind not in RESOURCE_KINDS:
        raise ValueError(f"Unsupported resource kind: {kind}")
    blocks = [
        block
        for block in document.get("blocks", [])
        if isinstance(block, dict) and (not block_id or block.get("id") == block_id)
    ]
    anchor_targets = {
        str(block["anchor"]): {
            "block_id": str(block.get("id") or ""),
            "kind": str(block.get("kind") or "text"),
            "page_number": block.get("page_number"),
        }
        for block in document.get("blocks", [])
        if isinstance(block, dict) and block.get("anchor")
    }
    formulas: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    bibliography: list[dict[str, Any]] = []

    for block in blocks:
        html = str(block.get("html") or "")
        soup = BeautifulSoup(html, "lxml") if html else None
        block_resource = _block_identity(block)
        seen_tex: set[tuple[str, bool]] = set()
        if soup:
            for index, marker in enumerate(soup.select("[data-reader-tex]")):
                tex = str(marker.get("data-reader-tex") or "").strip()
                display = str(marker.get("data-reader-display") or "inline") == "block"
                if not tex or (tex, display) in seen_tex:
                    continue
                seen_tex.add((tex, display))
                formulas.append(
                    {
                        **block_resource,
                        "id": f"{block_resource['block_id']}:formula:{index}",
                        "tex": tex,
                        "display": display,
                        "context": str(block.get("text") or ""),
                    }
                )
            for index, link in enumerate(soup.select("[data-reader-ref]")):
                target_anchor = str(link.get("data-reader-ref") or "").strip()
                if not target_anchor:
                    continue
                links.append(
                    {
                        **block_resource,
                        "id": f"{block_resource['block_id']}:reference:{index}",
                        "label": link.get_text(" ", strip=True),
                        "target_anchor": target_anchor,
                        "target": anchor_targets.get(target_anchor),
                    }
                )
        block_kind = str(block.get("kind") or "").lower()
        if block_kind == "picture":
            figures.append(_visual_resource(block, asset_resolver))
        elif block_kind == "table":
            tables.append({**_visual_resource(block, asset_resolver), "html": html or None})
        elif block_kind == "bibliography_item":
            bibliography.append(
                {
                    **block_resource,
                    "text": str(block.get("text") or ""),
                    "html": html or None,
                }
            )

    result: dict[str, Any] = {
        "paper_id": document.get("paper_id"),
        "version": document.get("version"),
        "status": document.get("status"),
        "reader_source": document.get("reader_source"),
    }
    if kind in {None, "formula"}:
        result["formulas"] = formulas
    if kind in {None, "figure"}:
        result["figures"] = figures
    if kind in {None, "table"}:
        result["tables"] = tables
    if kind in {None, "reference"}:
        result["references"] = {"links": links, "bibliography": bibliography}
    return result


def _block_identity(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "block_id": str(block.get("id") or ""),
        "anchor": block.get("anchor"),
        "page_number": block.get("page_number"),
    }


def _visual_resource(
    block: dict[str, Any], asset_resolver: Callable[[str], Path] | None
) -> dict[str, Any]:
    image_url = str(block.get("image_url") or "") or None
    asset_path: str | None = None
    if image_url and asset_resolver:
        filename = Path(urlparse(image_url).path).name
        if filename:
            try:
                asset_path = str(asset_resolver(filename).resolve())
            except (FileNotFoundError, ValueError):
                asset_path = None
    return {
        **_block_identity(block),
        "caption": str(block.get("caption") or ""),
        "text": str(block.get("text") or ""),
        "image_url": image_url,
        "asset_path": asset_path,
    }
