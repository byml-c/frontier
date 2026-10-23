from __future__ import annotations

import html
import ipaddress
import json
import logging
import multiprocessing
import os
import socket
import tempfile
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic, sleep
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
from .heavy_work import paper_processing_slot
from .outbound_network import external_httpx_kwargs
from .paper_sources import arxiv_pdf_url, download_pdf_to_temp

USER_AGENT = "FrontierResearch/0.1 (personal research assistant)"
ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_QUERY_LOCK = threading.Lock()
_arxiv_last_query_at = 0.0
ARXIV_RETRY_BUDGET_SECONDS = 600.0
_ARXIV_DEADLINE: ContextVar[float | None] = ContextVar("arxiv_deadline", default=None)
_arxiv_cooldown_until = 0.0
_logger = logging.getLogger(__name__)
_PDF_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 90.0
_PDF_DOWNLOAD_IO_TIMEOUT_SECONDS = 5.0
_PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS = 75.0
_PDF_TEXT_PROCESS_STOP_TIMEOUT_SECONDS = 2.0
_PDF_TEXT_CACHE_OVERHEAD_BYTES = 16 * 1024**2


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
    return search_arxiv_page(query, max_results=max_results)["items"]


@contextmanager
def arxiv_request_budget():
    """Share one ten-minute budget across all pages in a discovery call."""
    if _ARXIV_DEADLINE.get() is not None:
        yield
        return
    token = _ARXIV_DEADLINE.set(monotonic() + ARXIV_RETRY_BUDGET_SECONDS)
    try:
        yield
    finally:
        _ARXIV_DEADLINE.reset(token)


def _arxiv_retry_after(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return max(0.0, (when - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _arxiv_get(params: dict) -> httpx.Response:
    global _arxiv_last_query_at, _arxiv_cooldown_until
    with arxiv_request_budget():
        deadline = _ARXIV_DEADLINE.get()
        assert deadline is not None
        remaining = deadline - monotonic()
        if remaining <= 0 or not _ARXIV_QUERY_LOCK.acquire(timeout=remaining):
            raise httpx.TimeoutException("arXiv ten-minute request budget exhausted")
        try:
            backoff = 60.0
            last_error = None
            while True:
                remaining = deadline - monotonic()
                delay = max(
                    0.0,
                    _arxiv_last_query_at + 3.0 - monotonic(),
                    _arxiv_cooldown_until - monotonic(),
                )
                if remaining <= 0 or delay >= remaining:
                    if remaining > 0:
                        sleep(remaining)
                    raise httpx.TimeoutException(
                        "arXiv ten-minute request budget exhausted; retry later"
                    ) from last_error
                if delay:
                    sleep(delay)
                _arxiv_last_query_at = monotonic()
                try:
                    response = httpx.get(
                        ARXIV_API,
                        params=params,
                        headers={"User-Agent": USER_AGENT},
                        timeout=min(45.0, max(0.001, (deadline - monotonic()) / 4)),
                        follow_redirects=True,
                        **external_httpx_kwargs(ARXIV_API),
                    )
                    response.raise_for_status()
                    return response
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {429, 502, 503, 504}:
                        raise
                    wait = max(backoff, _arxiv_retry_after(exc.response))
                    last_error = exc
                except httpx.TransportError as exc:
                    wait = backoff
                    last_error = exc
                _arxiv_cooldown_until = monotonic() + wait
                _logger.warning(
                    "arXiv request failed (%s); retry in %.0fs, budget remaining %.0fs",
                    last_error,
                    wait,
                    max(0.0, deadline - monotonic()),
                )
                backoff = min(backoff * 2, 300.0)
        finally:
            _ARXIV_QUERY_LOCK.release()


def search_arxiv_page(
    query: str,
    *,
    max_results: int = 100,
    start: int = 0,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
) -> dict:
    """Read one metadata page without ingestion, retaining upstream pagination."""
    max_results = max(1, min(max_results, 100))
    if start < 0:
        raise ValueError("arXiv start must be nonnegative")
    response = _arxiv_get(
        {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
    )
    feed = feedparser.parse(response.text)
    if feed.get("bozo") or feed.get("version") != "atom10":
        raise ValueError("arXiv returned a malformed Atom feed")
    results: list[PaperCreate] = []
    for entry in feed.entries:
        if "/api/errors" in entry.get("id", ""):
            raise ValueError(f"arXiv API error: {entry.get('summary', 'unknown error')}")
        parsed = parse_arxiv_id(entry.id)
        arxiv_id, version = parsed if parsed else (None, None)
        links = {link.get("type"): link.get("href") for link in entry.get("links", [])}
        authors = []
        for item in entry.get("authors", []):
            author = {"name": item.get("name", "")}
            affiliation = item.get("arxiv_affiliation")
            if isinstance(affiliation, str) and affiliation.strip():
                author["affiliation"] = " ".join(affiliation.split())
            authors.append(author)
        published = datetime.fromisoformat(entry.published).date()
        primary_category = entry.get("arxiv_primary_category")
        primary_term = (
            primary_category.get("term")
            if isinstance(primary_category, dict)
            else primary_category
        )
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
                doi=entry.get("arxiv_doi"),
                published_at=published,
                metadata_json={
                    "categories": [tag.get("term") for tag in entry.get("tags", [])],
                    "primary_category": primary_term,
                    "comment": entry.get("arxiv_comment"),
                    "journal_ref": entry.get("arxiv_journal_ref"),
                    "arxiv_updated_at": entry.get("updated"),
                    "arxiv_published_at": entry.get("published"),
                },
            )
        )
    total = feed.feed.get("opensearch_totalresults")
    reported_start = feed.feed.get("opensearch_startindex")
    if reported_start is not None and int(reported_start) != start:
        raise ValueError("arXiv returned a different page offset")
    total = int(total) if total is not None else None
    if total is not None and total < 0:
        raise ValueError("arXiv returned a negative totalResults")
    if total is not None and start < total and not results:
        raise ValueError("arXiv returned an empty page before the end; retry this offset")
    return {"items": results, "total_results": total, "start": start}


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
    with paper_processing_slot():
        with pymupdf.open(stream=content, filetype="pdf") as document:
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
        return paper, created


def download_paper_pdf(session: Session, paper: Paper) -> Path:
    with paper_processing_slot():
        if paper.pdf_path and Path(paper.pdf_path).exists():
            return Path(paper.pdf_path)
        if not paper.pdf_url:
            raise ValueError("Paper has no PDF URL")
        deadline = monotonic() + _PDF_DOWNLOAD_TOTAL_TIMEOUT_SECONDS
        safe_url = _safe_remote_url(paper.pdf_url)
        target = settings.pdf_dir / f"{paper.id}.pdf"
        temporary = target.with_suffix(".pdf.download")
        temporary.unlink(missing_ok=True)
        try:
            current_url = safe_url
            for _redirect in range(11):
                with httpx.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=_download_operation_timeout(deadline),
                    follow_redirects=False,
                    **external_httpx_kwargs(current_url),
                ) as response:
                    _check_download_deadline(deadline)
                    if response.has_redirect_location:
                        current_url = _safe_remote_url(
                            urljoin(str(response.url), response.headers["location"])
                        )
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if (
                        content_length is not None
                        and int(content_length) > settings.paper_download_max_bytes
                    ):
                        raise ValueError("PDF exceeds the configured download size limit")
                    received = 0
                    signature = b""
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes(chunk_size=64 * 1024):
                            _check_download_deadline(deadline)
                            if not chunk:
                                continue
                            received += len(chunk)
                            if received > settings.paper_download_max_bytes:
                                raise ValueError("PDF exceeds the configured download size limit")
                            if len(signature) < 4:
                                signature += chunk[: 4 - len(signature)]
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    _check_download_deadline(deadline)
                    if signature != b"%PDF":
                        raise ValueError("Downloaded file is not a PDF")
                    break
            else:
                raise ValueError("PDF download exceeded the redirect limit")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        paper.pdf_path = str(target)
        session.commit()
        return target


def _check_download_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise TimeoutError(
            "PDF download exceeded the configured total deadline of "
            f"{_PDF_DOWNLOAD_TOTAL_TIMEOUT_SECONDS:g} seconds"
        )


def _download_operation_timeout(deadline: float) -> httpx.Timeout:
    remaining = deadline - monotonic()
    if remaining <= 0:
        _check_download_deadline(deadline)
    operation_timeout = min(_PDF_DOWNLOAD_IO_TIMEOUT_SECONDS, remaining)
    return httpx.Timeout(operation_timeout)


def _pdf_text_cache_path(paper_id: str) -> Path:
    return settings.text_dir / f"{paper_id}.json"


def _validate_pdf_text_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
        raise TypeError("Cached PDF text has an invalid structure")
    extracted_chars = 0
    for page in payload["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("text"), str):
            raise TypeError("Cached PDF text has an invalid page entry")
        extracted_chars += len(page["text"])
        if extracted_chars > settings.paper_text_max_chars:
            raise ValueError("Cached PDF text exceeds the configured size limit")
    return payload


def _load_pdf_text_cache(cache: Path) -> dict:
    max_cache_bytes = settings.paper_text_max_chars * 4 + _PDF_TEXT_CACHE_OVERHEAD_BYTES
    if cache.stat().st_size > max_cache_bytes:
        raise ValueError("Cached PDF text file exceeds the configured size limit")
    with cache.open("r", encoding="utf-8") as source:
        return _validate_pdf_text_payload(json.load(source))


def _pdf_text_extraction_worker(
    path: str,
    temporary: str,
    paper_id: str,
    max_chars: int,
    result_pipe: object,
) -> None:
    """Extract into JSON incrementally; the parent owns timeout and publication."""
    try:
        with pymupdf.open(path) as document, Path(temporary).open("w", encoding="utf-8") as output:
            output.write('{"paper_id":')
            json.dump(paper_id, output, ensure_ascii=False)
            output.write(f',"page_count":{document.page_count},"pages":[')
            extracted_chars = 0
            for index, page in enumerate(document):
                text = page.get_text("text")
                extracted_chars += len(text)
                if extracted_chars > max_chars:
                    raise ValueError("Extracted PDF text exceeds the configured size limit")
                if index:
                    output.write(",")
                json.dump(
                    {
                        "page_number": index + 1,
                        "text": text,
                        "width": page.rect.width,
                        "height": page.rect.height,
                    },
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            output.write("]}")
            output.flush()
            os.fsync(output.fileno())
        result_pipe.send(("ok", extracted_chars))  # type: ignore[attr-defined]
    except BaseException as exc:  # noqa: BLE001 - report child failures to the parent
        try:
            result_pipe.send(("error", f"{type(exc).__name__}: {exc}"))  # type: ignore[attr-defined]
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_pipe.close()  # type: ignore[attr-defined]


def _stop_extraction_process(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=_PDF_TEXT_PROCESS_STOP_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(timeout=_PDF_TEXT_PROCESS_STOP_TIMEOUT_SECONDS)


def _extract_pdf_text_to_cache(path: Path, cache: Path, paper_id: str) -> None:
    temporary = cache.with_name(f"{cache.name}.tmp-{uuid.uuid4().hex}")
    context = multiprocessing.get_context("spawn")
    result_pipe, child_pipe = context.Pipe(duplex=False)
    process = context.Process(
        target=_pdf_text_extraction_worker,
        args=(
            str(path),
            str(temporary),
            paper_id,
            settings.paper_text_max_chars,
            child_pipe,
        ),
        name=f"pdf-text-{paper_id[:8]}",
        daemon=True,
    )
    try:
        process.start()
        child_pipe.close()
        process.join(timeout=_PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS)
        if process.is_alive():
            _stop_extraction_process(process)
            raise TimeoutError(
                "PDF text extraction exceeded the configured hard timeout of "
                f"{_PDF_TEXT_EXTRACTION_TIMEOUT_SECONDS:g} seconds"
            )
        message = result_pipe.recv() if result_pipe.poll() else None
        if process.exitcode != 0 or not message or message[0] != "ok":
            detail = message[1] if message and len(message) > 1 else process.exitcode
            raise ValueError(f"PDF text extraction failed: {detail}")
        temporary.replace(cache)
    finally:
        child_pipe.close()
        result_pipe.close()
        if process.is_alive():
            _stop_extraction_process(process)
        try:
            process.close()
        except ValueError:
            pass
        temporary.unlink(missing_ok=True)


def ensure_pdf_text_cache(session: Session, paper: Paper, *, force: bool = False) -> Path:
    """Compatibility shim for callers that still expect a temporary text path."""
    del force
    payload = extract_pdf_text(session, paper)
    with tempfile.NamedTemporaryFile(
        prefix=f"frontier-text-{paper.id[:8]}-", suffix=".json", delete=False
    ) as temporary:
        target = Path(temporary.name)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def extract_pdf_text(session: Session, paper: Paper, *, force: bool = False) -> dict:
    """Extract text into a temporary file and never retain it after the call."""
    del force
    with paper_processing_slot(), tempfile.TemporaryDirectory() as root:
        target = Path(root) / "text.json"
        source = None
        temporary_pdf: Path | None = None
        if paper.pdf_path and Path(paper.pdf_path).is_file():
            source = Path(paper.pdf_path)
        elif arxiv_pdf_url(paper):
            temporary_pdf = download_pdf_to_temp(paper)
            source = temporary_pdf
        elif paper.pdf_url:
            source = download_paper_pdf(session, paper)
        if source is None:
            raise ValueError("PDF unavailable")
        try:
            _extract_pdf_text_to_cache(source, target, paper.id)
            return _load_pdf_text_cache(target)
        finally:
            if temporary_pdf is not None:
                temporary_pdf.unlink(missing_ok=True)


def render_pdf_page(session: Session, paper: Paper, page_number: int, *, dpi: int = 144) -> Path:
    """Render one PDF page for Agent-side inspection of figures and layouts."""
    with paper_processing_slot(), tempfile.TemporaryDirectory():
        temporary_pdf: Path | None = None
        if paper.pdf_path and Path(paper.pdf_path).is_file():
            path = Path(paper.pdf_path)
        elif arxiv_pdf_url(paper):
            temporary_pdf = download_pdf_to_temp(paper)
            path = temporary_pdf
        else:
            path = download_paper_pdf(session, paper)
        document = pymupdf.open(path)
        try:
            if page_number < 1 or page_number > document.page_count:
                raise ValueError(f"Page must be between 1 and {document.page_count}")
            renders = settings.runtime_dir / "renders"
            renders.mkdir(parents=True, exist_ok=True)
            target = renders / f"{paper.id}-page-{page_number}-{dpi}dpi.png"
            page = document[page_number - 1]
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(target)
            return target
        finally:
            document.close()
            if temporary_pdf is not None:
                temporary_pdf.unlink(missing_ok=True)
