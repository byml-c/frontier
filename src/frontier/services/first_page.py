from __future__ import annotations

import html
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from bs4 import BeautifulSoup, Tag
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Paper
from .outbound_network import external_httpx_kwargs

_MAX_SNIPPET_CHARS = 2_500
_PDF_TIMEOUT_SECONDS = 45.0


def _arxiv_html_url(paper: Paper) -> str | None:
    if not paper.arxiv_id:
        return None
    identifier = paper.arxiv_id
    if paper.arxiv_version and not identifier.endswith(f"v{paper.arxiv_version}"):
        identifier = f"{identifier}v{paper.arxiv_version}"
    return f"https://arxiv.org/html/{identifier}"


def _arxiv_pdf_url(paper: Paper) -> str | None:
    if not paper.arxiv_id:
        return None
    identifier = paper.arxiv_id
    if paper.arxiv_version and not identifier.endswith(f"v{paper.arxiv_version}"):
        identifier = f"{identifier}v{paper.arxiv_version}"
    return f"https://arxiv.org/pdf/{identifier}"


def _html_first_page(paper: Paper, max_chars: int) -> str | None:
    source_url = _arxiv_html_url(paper)
    if source_url is None:
        return None
    try:
        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            **external_httpx_kwargs(source_url),
        ) as client:
            response = client.get(source_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            article = soup.select_one("article.ltx_document")
            if not isinstance(article, Tag):
                return None
            parts: list[str] = []
            for node in article.select("h1, h2, p, figcaption"):
                text = " ".join(node.get_text(" ", strip=True).split())
                if text:
                    parts.append(text)
                if sum(len(part) for part in parts) >= max_chars:
                    break
            return html.unescape(" ".join(parts))[:max_chars] or None
    except httpx.HTTPError:
        return None


def _extract_pdf_first_pages(path: Path, max_chars: int) -> str:
    with pymupdf.open(path) as document:
        parts = []
        for index in range(min(2, document.page_count)):
            parts.append(document[index].get_text("text"))
        return " ".join(" ".join(parts).split())[:max_chars]


def _pdf_first_page(paper: Paper, max_chars: int) -> str | None:
    if paper.pdf_path:
        local = Path(paper.pdf_path)
        if local.is_file():
            return _extract_pdf_first_pages(local, max_chars)
    source_url = _arxiv_pdf_url(paper)
    if source_url is None:
        return None
    with tempfile.TemporaryDirectory() as root:
        target = Path(root) / "paper.pdf"
        try:
            with httpx.Client(
                timeout=_PDF_TIMEOUT_SECONDS,
                follow_redirects=True,
                **external_httpx_kwargs(source_url),
            ) as client, client.stream("GET", source_url) as response:
                response.raise_for_status()
                received = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        received += len(chunk)
                        if received > settings.paper_download_max_bytes:
                            return None
                        output.write(chunk)
        except (httpx.HTTPError, OSError):
            return None
        if target.stat().st_size < 5 or target.read_bytes()[:4] != b"%PDF":
            return None
        return _extract_pdf_first_pages(target, max_chars)


def paper_first_page_text(
    session: Session, paper: Paper, *, max_chars: int = _MAX_SNIPPET_CHARS
) -> dict[str, Any]:
    """Return a small, non-persisted affiliation/author snippet for Agent matching."""
    del session
    limit = max(200, min(int(max_chars), 8_000))
    text = _html_first_page(paper, limit)
    source = "arxiv_html"
    if not text:
        text = _pdf_first_page(paper, limit)
        source = "pdf"
    return {
        "paper_id": paper.id,
        "source": source if text else None,
        "text": text or "",
        "truncated": bool(text and len(text) >= limit),
    }
