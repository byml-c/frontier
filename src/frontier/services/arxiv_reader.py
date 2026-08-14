from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .outbound_network import external_httpx_kwargs

logger = logging.getLogger(__name__)

ARXIV_HTML_BASE = "https://arxiv.org/html/"
_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
_CONTENT_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


class ArxivHtmlUnavailable(RuntimeError):
    """The paper has no usable arXiv LaTeX-derived HTML representation."""


def convert_from_arxiv_html(
    arxiv_id: str,
    pdf_path: Path,
    *,
    paper_id: str,
    version: str,
    root: Path,
    schema_version: int,
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    source_id = arxiv_id.strip().removeprefix("arXiv:")
    source_url = f"{ARXIV_HTML_BASE}{source_id}"
    headers = {"User-Agent": "FrontierResearch/0.1 (personal research reader)"}
    if on_progress:
        on_progress("source", "正在读取 arXiv 源文档", 14, 2)
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=30,
        **external_httpx_kwargs(source_url),
    ) as client:
        try:
            response = client.get(source_url)
        except httpx.HTTPError as exc:
            raise ArxivHtmlUnavailable(f"arXiv HTML 连接失败：{exc}") from exc
        if response.status_code == 404:
            raise ArxivHtmlUnavailable("arXiv 尚未提供这篇论文的 HTML 版本")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ArxivHtmlUnavailable(f"arXiv HTML 请求失败：{exc}") from exc
        if "text/html" not in response.headers.get("content-type", ""):
            raise ArxivHtmlUnavailable("arXiv 返回的不是 HTML 文档")
        html = response.text
        if "ltx_document" not in html:
            raise ArxivHtmlUnavailable("arXiv HTML 中没有可读取的 LaTeX 文档结构")
        root.mkdir(parents=True, exist_ok=True)
        (root / "arxiv.html").write_text(html, encoding="utf-8")
        try:
            return convert_arxiv_html_content(
                html,
                base_url=str(response.url),
                pdf_path=pdf_path,
                paper_id=paper_id,
                version=version,
                root=root,
                schema_version=schema_version,
                fetch_asset=lambda url, target: _fetch_asset(client, url, target),
                on_progress=on_progress,
            )
        except ArxivHtmlUnavailable:
            raise
        except Exception as exc:
            raise ArxivHtmlUnavailable(f"arXiv HTML 解析失败：{exc}") from exc


def convert_arxiv_html_content(
    html: str,
    *,
    base_url: str,
    pdf_path: Path,
    paper_id: str,
    version: str,
    root: Path,
    schema_version: int,
    fetch_asset: Callable[[str, Path], Path] | None = None,
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("article.ltx_document")
    if not isinstance(article, Tag):
        raise ArxivHtmlUnavailable("arXiv HTML 中没有论文正文")

    if on_progress:
        on_progress("structuring", "正在整理 LaTeX 章节与引用", 42, 3)
    title_node = article.select_one("h1.ltx_title_document")
    title = _plain_text(title_node) if isinstance(title_node, Tag) else pdf_path.stem
    page_locator = _PageLocator(pdf_path)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    blocks: list[dict[str, Any]] = []

    selected = article.select(
        "h1, h2, h3, h4, h5, h6, p.ltx_p, figure.ltx_figure, "
        "figure.ltx_table, table.ltx_equation, table.ltx_equationgroup, "
        "div.ltx_equation, div.ltx_equationgroup, li.ltx_bibitem"
    )
    candidates = [node for node in selected if _is_content_candidate(node)]
    total_items = len(candidates)
    if on_progress:
        on_progress(
            "assets",
            "正在缓存原始图片并建立跳转",
            55,
            3,
            processed_items=0,
            total_items=total_items,
        )

    update_interval = max(1, total_items // 20)
    for index, node in enumerate(candidates):
        block = _build_block(
            node,
            index=index,
            base_url=base_url,
            paper_id=paper_id,
            assets=assets,
            fetch_asset=fetch_asset,
        )
        if block is not None:
            block["page_number"] = page_locator.locate(
                f"{block.get('caption', '')} {block.get('text', '')}"
            )
            blocks.append(block)
        processed = index + 1
        if on_progress and (processed == total_items or processed % update_interval == 0):
            on_progress(
                "assets",
                "正在缓存原始图片并建立跳转",
                55 + round(38 * processed / max(1, total_items)),
                3,
                processed_items=processed,
                total_items=total_items,
            )

    if not blocks:
        raise ArxivHtmlUnavailable("arXiv HTML 正文为空")
    return {
        "paper_id": paper_id,
        "version": version,
        "status": "ready",
        "schema_version": schema_version,
        "reader_source": "arxiv_html",
        "source_url": base_url,
        "title": title,
        "blocks": blocks,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _is_content_candidate(node: Tag) -> bool:
    if node.name == "h1" and "ltx_title_document" in (node.get("class") or []):
        return False
    if node.find_parent(["figure", "figcaption"]):
        return node.name == "figure"
    if node.name == "p" and node.find_parent(["table", "nav"]):
        return False
    classes = set(node.get("class") or [])
    if classes & {"ltx_equation", "ltx_equationgroup"}:
        parent = node.find_parent(class_=["ltx_equation", "ltx_equationgroup"])
        if parent is not None:
            return False
        if node.find_parent("p"):
            return False
    return not bool(node.find_parent("nav"))


def _build_block(
    node: Tag,
    *,
    index: int,
    base_url: str,
    paper_id: str,
    assets: Path,
    fetch_asset: Callable[[str, Path], Path] | None,
) -> dict[str, Any] | None:
    anchor = _anchor_for(node)
    block_id = hashlib.sha1(
        f"arxiv:{anchor or node.name}:{index}".encode(), usedforsecurity=False
    ).hexdigest()[:16]
    classes = set(node.get("class") or [])
    if "ltx_bibitem" in classes:
        text = _plain_text(node)
        if not text:
            return None
        return {
            "id": block_id,
            "anchor": anchor,
            "kind": "bibliography_item",
            "level": 0,
            "text": text,
            "html": _safe_fragment(node, base_url, inner=True),
        }
    if node.name == "figure":
        caption_node = node.find("figcaption")
        caption = _plain_text(caption_node) if isinstance(caption_node, Tag) else ""
        asset_url = _figure_asset_url(node, base_url)
        table = node.find("table")
        is_table = "ltx_table" in classes or caption.lower().startswith("table")
        block: dict[str, Any] = {
            "id": block_id,
            "anchor": anchor,
            "kind": "table" if is_table else "picture",
            "level": 0,
            "text": _plain_text(table) if isinstance(table, Tag) else "",
            "caption": caption,
        }
        if asset_url and fetch_asset:
            source_url = asset_url
            suffix = Path(urlparse(source_url).path).suffix.lower()
            if suffix not in _ASSET_SUFFIXES:
                suffix = ".img"
            target = assets / f"{hashlib.sha1(source_url.encode(), usedforsecurity=False).hexdigest()[:20]}{suffix}"
            try:
                saved = fetch_asset(source_url, target)
                block["image_url"] = f"/api/papers/{paper_id}/document/assets/{saved.name}"
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("Could not cache arXiv figure %s: %s", source_url, exc)
        if not block.get("image_url") and isinstance(table, Tag):
            block["html"] = _safe_fragment(table, base_url)
        if not block.get("image_url") and not block.get("html"):
            return None
        return block

    equation = bool(classes & {"ltx_equation", "ltx_equationgroup"})
    if equation:
        text = _plain_text(node)
        return {
            "id": block_id,
            "anchor": anchor,
            "kind": "formula",
            "level": 0,
            "text": text,
            "html": _safe_fragment(node, base_url),
        }

    text = _plain_text(node)
    if not text:
        return None
    if node.name and node.name.startswith("h"):
        return {
            "id": block_id,
            "anchor": anchor,
            "kind": "bibliography_header"
            if "ltx_title_bibliography" in classes
            else "section_header",
            "level": max(1, int(node.name[1:])),
            "text": text,
            "html": _safe_fragment(node, base_url, inner=True),
        }
    return {
        "id": block_id,
        "anchor": anchor,
        "kind": "list_item" if node.find_parent("li") else "text",
        "level": 0,
        "text": text,
        "html": _safe_fragment(node, base_url, inner=True),
    }


def _figure_asset_url(node: Tag, base_url: str) -> str | None:
    """Return the primary arXiv figure asset across LaTeXML media variants."""
    media_nodes = node.find_all(["img", "object", "embed", "source"])
    if not media_nodes:
        # LaTeXML sometimes emits a full-width teaser as a standalone, textless
        # paragraph immediately before a caption-only figure (for example
        # arXiv:2608.11739 Figure 1). Treat that pair as one semantic figure.
        previous = node.find_previous_sibling()
        if isinstance(previous, Tag):
            previous_classes = set(previous.get("class") or [])
            eligible_wrapper = (
                (previous.name == "div" and "ltx_para" in previous_classes)
                or (previous.name == "p" and "ltx_p" in previous_classes)
            )
            if eligible_wrapper and not _plain_text(previous):
                media_nodes = previous.find_all(["img", "object", "embed", "source"])
    for media in media_nodes:
        value: str | None = None
        if media.name == "object":
            value = str(media.get("data") or "")
        elif media.name in {"img", "embed"}:
            value = str(media.get("src") or "")
            if not value and media.name == "img":
                value = _best_srcset_candidate(str(media.get("srcset") or ""))
        elif media.name == "source":
            value = _best_srcset_candidate(str(media.get("srcset") or ""))
        if value:
            return urljoin(base_url, value)
    return None


def _best_srcset_candidate(srcset: str) -> str | None:
    candidates: list[tuple[float, str]] = []
    for index, raw_candidate in enumerate(srcset.split(",")):
        parts = raw_candidate.strip().split()
        if not parts:
            continue
        score = float(index)
        if len(parts) > 1:
            descriptor = parts[-1].lower()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 10_000
            except ValueError:
                pass
        candidates.append((score, parts[0]))
    return max(candidates, default=(0, ""))[1] or None


def _safe_fragment(node: Tag, base_url: str, *, inner: bool = False) -> str:
    clone_soup = BeautifulSoup(str(node), "lxml")
    clone = clone_soup.find(node.name)
    if not isinstance(clone, Tag):
        return ""
    for unwanted in clone.select("script, style, noscript"):
        unwanted.decompose()
    # arXiv HTML contains both presentation MathML and the original TeX annotation.
    # Keep only the source TeX as a neutral placeholder; the web reader renders it
    # with the existing KaTeX package. This avoids OCR errors and keeps equations
    # sharp, selectable, and copyable at every zoom level.
    for math in clone.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
        tex = annotation.get_text() if isinstance(annotation, Tag) else str(math.get("alttext") or "")
        placeholder = clone_soup.new_tag("span")
        placeholder["data-reader-tex"] = tex
        placeholder["data-reader-display"] = (
            "block" if math.get("display") == "block" else "inline"
        )
        math.replace_with(placeholder)
    for link in clone.find_all("a"):
        href = str(link.get("href") or "")
        if href.startswith("#") and len(href) > 1:
            link["data-reader-ref"] = href[1:]
            link["href"] = f"#reader-{href[1:]}"
        elif href:
            link["href"] = urljoin(base_url, href)
            link["target"] = "_blank"
            link["rel"] = "noopener noreferrer"
    return clone.decode_contents() if inner else str(clone)


def _anchor_for(node: Tag) -> str | None:
    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        section = node.find_parent("section")
        if isinstance(section, Tag) and section.get("id"):
            return str(section["id"])
    if node.get("id"):
        return str(node["id"])
    if node.name not in {"h1", "h2", "h3", "h4", "h5", "h6", "figure", "table", "div"}:
        return None
    parent = node.find_parent(["section", "figure", "li"])
    return str(parent["id"]) if isinstance(parent, Tag) and parent.get("id") else None


def _plain_text(node: Tag | None) -> str:
    if node is None:
        return ""
    clone_soup = BeautifulSoup(str(node), "lxml")
    clone = clone_soup.find(node.name)
    if not isinstance(clone, Tag):
        return ""
    for math in clone.find_all("math"):
        annotation = math.find("annotation", attrs={"encoding": "application/x-tex"})
        tex = annotation.get_text() if isinstance(annotation, Tag) else str(math.get("alttext") or "")
        math.replace_with(f" {tex} ")
    return re.sub(r"\s+", " ", clone.get_text(" ", strip=True)).strip()


def _fetch_asset(client: httpx.Client, url: str, target: Path) -> Path:
    host = (urlparse(url).hostname or "").lower()
    if host != "arxiv.org" and not host.endswith(".arxiv.org"):
        raise ValueError("arXiv 资源地址不可信")
    if target.suffix in _ASSET_SUFFIXES and target.exists():
        return target
    response = client.get(url)
    response.raise_for_status()
    if len(response.content) > 30 * 1024 * 1024:
        raise ValueError("arXiv 图片资源超过 30 MB")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    suffix = target.suffix if target.suffix in _ASSET_SUFFIXES else _CONTENT_SUFFIXES.get(content_type)
    if not suffix:
        raise ValueError(f"不支持的图片类型：{content_type or 'unknown'}")
    final_target = target.with_suffix(suffix)
    if not final_target.exists():
        temporary = final_target.with_suffix(f"{final_target.suffix}.tmp")
        temporary.write_bytes(response.content)
        temporary.replace(final_target)
    return final_target


class _PageLocator:
    def __init__(self, pdf_path: Path) -> None:
        import pymupdf

        with pymupdf.open(pdf_path) as source:
            self.pages = [_normalize(page.get_text()) for page in source]
        self.current = 1

    def locate(self, text: str) -> int:
        needle = _normalize(text)
        if len(needle) < 12:
            return self.current
        probes = [needle[:160], needle[:100], needle[:60]]
        for start in (self.current - 1, 0):
            for index in range(start, len(self.pages)):
                if any(len(probe) >= 20 and probe in self.pages[index] for probe in probes):
                    self.current = index + 1
                    return self.current
        return self.current


def _normalize(value: str) -> str:
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()
