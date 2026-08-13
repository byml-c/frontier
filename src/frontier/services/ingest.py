from __future__ import annotations

import html
import ipaddress
import socket
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import pymupdf
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Paper, PaperVersion
from ..schemas import PaperCreate
from .common import log_operation, normalize_title, parse_arxiv_id
from .outbound_network import external_httpx_kwargs

USER_AGENT = "FrontierResearch/0.1 (personal research assistant)"
ARXIV_API = "https://export.arxiv.org/api/query"


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.meta_all: dict[str, list[str]] = {}
        self.title_parts: list[str] = []
        self.in_title = False
        self.pdf_link: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content")
            if key and content:
                normalized_key = key.lower()
                normalized_content = content.strip()
                self.meta[normalized_key] = normalized_content
                self.meta_all.setdefault(normalized_key, []).append(normalized_content)
        if tag.lower() == "link" and values.get("type") == "application/pdf":
            self.pdf_link = values.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def _safe_remote_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only http and https URLs are supported")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host: {parsed.hostname}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Private or local network URLs are not allowed")
    return url


def find_existing_paper(
    session: Session, *, arxiv_id: str | None, doi: str | None, title: str
) -> Paper | None:
    clauses = [Paper.normalized_title == normalize_title(title)]
    if arxiv_id:
        clauses.append(Paper.arxiv_id == arxiv_id)
    if doi:
        clauses.append(Paper.doi == doi)
    return session.scalar(select(Paper).where(or_(*clauses)))


def create_or_update_paper(
    session: Session, payload: PaperCreate, *, actor: str
) -> tuple[Paper, bool]:
    arxiv_id = payload.arxiv_id
    arxiv_version = payload.arxiv_version
    if arxiv_id:
        parsed = parse_arxiv_id(arxiv_id)
        if parsed:
            arxiv_id, parsed_version = parsed
            arxiv_version = arxiv_version or parsed_version

    existing = find_existing_paper(session, arxiv_id=arxiv_id, doi=payload.doi, title=payload.title)
    if existing:
        if arxiv_version and (existing.arxiv_version or 0) < arxiv_version:
            previous = existing.arxiv_version or 1
            if (
                session.scalar(
                    select(PaperVersion).where(
                        PaperVersion.paper_id == existing.id, PaperVersion.version == previous
                    )
                )
                is None
            ):
                session.add(
                    PaperVersion(
                        paper_id=existing.id,
                        version=previous,
                        source_url=existing.source_url,
                        pdf_path=existing.pdf_path,
                        metadata_json=existing.metadata_json,
                    )
                )
            existing.arxiv_version = arxiv_version
        for name in (
            "abstract",
            "authors",
            "first_author",
            "corresponding_author",
            "primary_affiliation",
            "source_url",
            "pdf_url",
            "doi",
            "published_at",
        ):
            value = getattr(payload, name)
            if value and not getattr(existing, name):
                setattr(existing, name, value)
        existing.metadata_json = {**existing.metadata_json, **payload.metadata_json}
        log_operation(
            session,
            actor=actor,
            action="paper.duplicate_merged",
            entity_type="paper",
            entity_id=existing.id,
        )
        session.commit()
        return existing, False

    paper = Paper(
        **payload.model_dump(exclude={"arxiv_id", "arxiv_version"}),
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        normalized_title=normalize_title(payload.title),
    )
    if not paper.first_author and paper.authors:
        paper.first_author = paper.authors[0].get("name")
    session.add(paper)
    session.flush()
    log_operation(
        session,
        actor=actor,
        action="paper.created",
        entity_type="paper",
        entity_id=paper.id,
    )
    session.commit()
    return paper, True


def search_arxiv(query: str, max_results: int = 50) -> list[PaperCreate]:
    max_results = max(1, min(max_results, 100))
    response = httpx.get(
        ARXIV_API,
        params={
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=45,
        follow_redirects=True,
        **external_httpx_kwargs(ARXIV_API),
    )
    response.raise_for_status()
    feed = feedparser.parse(response.text)
    results: list[PaperCreate] = []
    for entry in feed.entries:
        parsed = parse_arxiv_id(entry.id)
        arxiv_id, version = parsed if parsed else (None, None)
        links = {link.get("type"): link.get("href") for link in entry.get("links", [])}
        authors = [{"name": item.get("name", "")} for item in entry.get("authors", [])]
        published = datetime.fromisoformat(entry.published).date()
        results.append(
            PaperCreate(
                title=" ".join(entry.title.split()),
                abstract=" ".join(entry.summary.split()),
                authors=authors,
                first_author=authors[0]["name"] if authors else None,
                source="arxiv",
                source_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else entry.id,
                pdf_url=links.get("application/pdf")
                or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None),
                arxiv_id=arxiv_id,
                arxiv_version=version or 1,
                published_at=published,
                metadata_json={
                    "categories": [tag.get("term") for tag in entry.get("tags", [])],
                    "comment": entry.get("arxiv_comment"),
                    "journal_ref": entry.get("arxiv_journal_ref"),
                },
            )
        )
    return results


def _citation_author_name(value: str) -> str:
    family, separator, given = value.partition(",")
    if separator and given.strip():
        return f"{given.strip()} {family.strip()}"
    return value.strip()


def parse_arxiv_abs_html(content: str, arxiv_id: str) -> PaperCreate:
    """Parse the stable citation metadata embedded in an arXiv abstract page."""
    parser = MetadataParser()
    parser.feed(content)
    title = parser.meta.get("citation_title") or parser.meta.get("og:title")
    abstract = parser.meta.get("citation_abstract") or parser.meta.get("og:description")
    if not title or not abstract:
        raise ValueError("arXiv abstract page did not contain paper metadata")
    page_id = parser.meta.get("citation_arxiv_id") or arxiv_id
    parsed_page_id = parse_arxiv_id(page_id)
    if not parsed_page_id or parsed_page_id[0] != arxiv_id:
        raise ValueError("arXiv abstract page returned a different paper")
    canonical = parser.meta.get("og:url", "")
    parsed_canonical = parse_arxiv_id(canonical)
    version = parsed_canonical[1] if parsed_canonical else parsed_page_id[1]
    authors = [
        {"name": _citation_author_name(value)}
        for value in parser.meta_all.get("citation_author", [])
        if value.strip()
    ]
    published_at = None
    date_value = parser.meta.get("citation_date") or parser.meta.get("citation_online_date")
    if date_value:
        try:
            published_at = datetime.fromisoformat(date_value[:10].replace("/", "-")).date()
        except ValueError:
            pass
    return PaperCreate(
        title=html.unescape(" ".join(title.split())),
        abstract=html.unescape(" ".join(abstract.split())),
        authors=authors,
        first_author=authors[0]["name"] if authors else None,
        source="arxiv",
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=parser.meta.get("citation_pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_id=arxiv_id,
        arxiv_version=version or 1,
        published_at=published_at,
    )


def ingest_arxiv(session: Session, value: str, *, actor: str = "user") -> tuple[Paper, bool]:
    parsed = parse_arxiv_id(value)
    if not parsed:
        raise ValueError("Invalid arXiv ID or URL")
    arxiv_id, _version = parsed
    try:
        source_url = f"https://arxiv.org/abs/{arxiv_id}"
        response = httpx.get(
            source_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            follow_redirects=True,
            **external_httpx_kwargs(source_url),
        )
        response.raise_for_status()
        payload = parse_arxiv_abs_html(response.text, arxiv_id)
    except (httpx.HTTPError, ValueError):
        results = search_arxiv(f"id:{arxiv_id}", max_results=1)
        if not results:
            raise ValueError(f"arXiv paper not found: {arxiv_id}") from None
        payload = results[0]
    return create_or_update_paper(session, payload, actor=actor)


def ingest_url(session: Session, url: str, *, actor: str = "user") -> tuple[Paper, bool]:
    parsed_arxiv = parse_arxiv_id(url)
    if parsed_arxiv:
        return ingest_arxiv(session, url, actor=actor)
    safe_url = _safe_remote_url(url)
    response = httpx.get(
        safe_url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
        **external_httpx_kwargs(safe_url),
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "application/pdf" in content_type or safe_url.lower().endswith(".pdf"):
        return ingest_pdf_bytes(
            session, response.content, Path(urlparse(safe_url).path).name, actor=actor
        )
    parser = MetadataParser()
    parser.feed(response.text)
    title = (
        parser.meta.get("citation_title")
        or parser.meta.get("og:title")
        or "".join(parser.title_parts).strip()
        or safe_url
    )
    author_value = parser.meta.get("citation_author") or parser.meta.get("author")
    authors = [{"name": author_value}] if author_value else []
    pdf_url = parser.meta.get("citation_pdf_url") or parser.pdf_link
    if pdf_url:
        pdf_url = urljoin(str(response.url), pdf_url)
    published_value = parser.meta.get("citation_publication_date") or parser.meta.get("date")
    published_at = None
    if published_value:
        try:
            published_at = datetime.fromisoformat(published_value[:10]).date()
        except ValueError:
            pass
    return create_or_update_paper(
        session,
        PaperCreate(
            title=html.unescape(title),
            authors=authors,
            first_author=authors[0]["name"] if authors else None,
            source="url",
            source_url=safe_url,
            pdf_url=pdf_url,
            doi=parser.meta.get("citation_doi"),
            published_at=published_at,
        ),
        actor=actor,
    )


def ingest_pdf_bytes(
    session: Session, content: bytes, filename: str, *, actor: str = "user"
) -> tuple[Paper, bool]:
    document = pymupdf.open(stream=content, filetype="pdf")
    metadata = document.metadata or {}
    first_page = document[0].get_text("text") if document.page_count else ""
    title = (metadata.get("title") or "").strip()
    if not title:
        first_lines = [line.strip() for line in first_page.splitlines() if line.strip()]
        title = first_lines[0] if first_lines else Path(filename).stem
    author_names = [
        item.strip() for item in (metadata.get("author") or "").split(",") if item.strip()
    ]
    authors = [{"name": name} for name in author_names]
    paper, created = create_or_update_paper(
        session,
        PaperCreate(
            title=title,
            authors=authors,
            first_author=author_names[0] if author_names else None,
            source="upload",
            metadata_json={"original_filename": filename, "pdf_metadata": metadata},
        ),
        actor=actor,
    )
    target = settings.pdf_dir / f"{paper.id}.pdf"
    target.write_bytes(content)
    paper.pdf_path = str(target)
    session.commit()
    document.close()
    return paper, created


def download_paper_pdf(session: Session, paper: Paper) -> Path:
    if paper.pdf_path and Path(paper.pdf_path).exists():
        return Path(paper.pdf_path)
    if not paper.pdf_url:
        raise ValueError("Paper has no PDF URL")
    safe_url = _safe_remote_url(paper.pdf_url)
    response = httpx.get(
        safe_url,
        headers={"User-Agent": USER_AGENT},
        timeout=90,
        follow_redirects=True,
        **external_httpx_kwargs(safe_url),
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError("Downloaded file is not a PDF")
    target = settings.pdf_dir / f"{paper.id}.pdf"
    target.write_bytes(response.content)
    paper.pdf_path = str(target)
    session.commit()
    return target


def extract_pdf_text(session: Session, paper: Paper, *, force: bool = False) -> dict:
    import json

    cache = settings.text_dir / f"{paper.id}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    path = download_paper_pdf(session, paper)
    document = pymupdf.open(path)
    pages = []
    for index, page in enumerate(document):
        pages.append(
            {
                "page_number": index + 1,
                "text": page.get_text("text"),
                "width": page.rect.width,
                "height": page.rect.height,
            }
        )
    result = {
        "paper_id": paper.id,
        "page_count": document.page_count,
        "pages": pages,
    }
    document.close()
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def render_pdf_page(session: Session, paper: Paper, page_number: int, *, dpi: int = 144) -> Path:
    """Render one PDF page for Agent-side inspection of figures and layouts."""
    path = download_paper_pdf(session, paper)
    document = pymupdf.open(path)
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}")
        target = settings.text_dir / f"{paper.id}-page-{page_number}-{dpi}dpi.png"
        if not target.exists():
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(target)
        return target
    finally:
        document.close()
