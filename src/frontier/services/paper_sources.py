from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from ..config import settings
from ..models import Paper
from .outbound_network import external_httpx_kwargs


def arxiv_identifier(paper: Paper) -> str | None:
    if not paper.arxiv_id:
        return None
    identifier = paper.arxiv_id
    if paper.arxiv_version and not identifier.endswith(f"v{paper.arxiv_version}"):
        identifier = f"{identifier}v{paper.arxiv_version}"
    return identifier


def arxiv_pdf_url(paper: Paper) -> str | None:
    identifier = arxiv_identifier(paper)
    return f"https://arxiv.org/pdf/{identifier}" if identifier else None


def arxiv_html_url(paper: Paper) -> str | None:
    identifier = arxiv_identifier(paper)
    return f"https://arxiv.org/html/{identifier}" if identifier else None


def paper_source_version(paper: Paper) -> str | None:
    """Version key for an arXiv or locally stored source, or None if unavailable."""
    identifier = arxiv_identifier(paper)
    if identifier:
        return f"arxiv:{identifier}"
    if paper.pdf_path:
        path = Path(paper.pdf_path)
        if path.is_file():
            stat = path.stat()
            return f"file:{stat.st_mtime_ns:x}-{stat.st_size:x}"
    return None


def source_version(paper: Paper) -> str:
    """Stable version key that does not require a cached PDF."""
    return paper_source_version(paper) or f"paper:{paper.id}"


def download_pdf_to_temp(paper: Paper) -> Path:
    """Download an arXiv PDF to a caller-owned temporary file."""
    url = arxiv_pdf_url(paper)
    if not url:
        raise ValueError("Paper has no arXiv PDF URL")
    with NamedTemporaryFile(prefix="frontier-pdf-", suffix=".pdf", delete=False) as temporary:
        target = Path(temporary.name)
    try:
        with httpx.Client(
            timeout=60,
            follow_redirects=True,
            **external_httpx_kwargs(url),
        ) as client, client.stream("GET", url) as response:
            response.raise_for_status()
            received = 0
            with target.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    received += len(chunk)
                    if received > settings.paper_download_max_bytes:
                        raise ValueError("PDF exceeds the configured download size limit")
                    output.write(chunk)
        if target.stat().st_size < 5 or target.read_bytes()[:4] != b"%PDF":
            raise ValueError("Downloaded file is not a PDF")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
