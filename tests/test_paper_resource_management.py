from __future__ import annotations

import json
import multiprocessing
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import Paper
from frontier.services import document_reader, ingest


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _local_pdf_paper(session: Session, path: Path) -> Paper:
    paper = Paper(
        title="Bounded local PDF",
        normalized_title="bounded local pdf",
        pdf_path=str(path),
    )
    session.add(paper)
    session.commit()
    return paper


def _write_pdf(path: Path, text: str = "bounded extraction") -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_document_conversion_uses_one_bounded_worker_and_shutdown_marks_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document_reader.shutdown_document_worker(wait_seconds=2)
    monkeypatch.setattr(document_reader.settings, "data_dir", tmp_path)
    monkeypatch.setattr(document_reader, "_DOCUMENT_CONVERSION_QUEUE_LIMIT", 2)
    document_reader.settings.ensure_directories()
    entered = threading.Event()
    release = threading.Event()
    restarted = threading.Event()
    converted: list[str] = []

    def fake_convert(paper_id: str, _force: bool) -> None:
        converted.append(paper_id)
        entered.set()
        release.wait(timeout=2)
        if paper_id == "paper-restarted":
            restarted.set()
        with document_reader._conversion_condition:
            document_reader._converting.discard(paper_id)

    monkeypatch.setattr(document_reader, "_convert_document_task", fake_convert)

    assert document_reader.queue_document_conversion("paper-active") is True
    assert entered.wait(timeout=1)
    assert document_reader.queue_document_conversion("paper-queued-1") is True
    assert document_reader.queue_document_conversion("paper-queued-1") is False
    assert document_reader.queue_document_conversion("paper-queued-2") is True
    assert document_reader.queue_document_conversion("paper-overflow") is False

    workers = [
        thread
        for thread in threading.enumerate()
        if thread.name == "document-reader-worker"
    ]
    assert len(workers) == 1
    overflow_status = json.loads(
        document_reader._paths("paper-overflow")[2].read_text(encoding="utf-8")
    )
    assert overflow_status["status"] == "failed"
    assert overflow_status["retryable"] is True
    assert "队列已满" in overflow_status["error"]

    document_reader.shutdown_document_worker(wait_seconds=0)
    for paper_id in ("paper-queued-1", "paper-queued-2"):
        status = json.loads(
            document_reader._paths(paper_id)[2].read_text(encoding="utf-8")
        )
        assert status["status"] == "failed"
        assert status["retryable"] is True
        assert "服务正在关闭" in status["error"]

    release.set()
    workers[0].join(timeout=1)
    assert converted == ["paper-active"]
    assert document_reader.queue_document_conversion("paper-restarted") is True
    assert restarted.wait(timeout=1)
    document_reader.shutdown_document_worker(wait_seconds=1)


def test_pdf_download_enforces_monotonic_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: Session,
) -> None:
    paper = Paper(
        title="Slow PDF",
        normalized_title="slow pdf",
        pdf_url="https://example.com/slow.pdf",
    )
    session.add(paper)
    session.commit()
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    monkeypatch.setattr(ingest, "_safe_remote_url", lambda url: url)
    monkeypatch.setattr(ingest, "external_httpx_kwargs", lambda _url: {})
    timestamps = iter((0.0, 1.0, 91.0))
    monkeypatch.setattr(ingest, "monotonic", lambda: next(timestamps))
    ingest.settings.ensure_directories()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"%PDF-1.7\nslow stream",
                request=request,
            )
        )
    )
    monkeypatch.setattr(ingest.httpx, "stream", client.stream)

    with pytest.raises(TimeoutError, match="total deadline"):
        ingest.download_paper_pdf(session, paper)

    target = ingest.settings.pdf_dir / f"{paper.id}.pdf"
    assert not target.exists()
    assert not target.with_suffix(".pdf.download").exists()
    client.close()


def test_pdf_text_extraction_process_writes_validated_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: Session,
) -> None:
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    ingest.settings.ensure_directories()
    path = ingest.settings.pdf_dir / "source.pdf"
    _write_pdf(path, "subprocess extraction works")
    paper = _local_pdf_paper(session, path)

    cache = ingest.ensure_pdf_text_cache(session, paper)
    payload = ingest.extract_pdf_text(session, paper)

    assert cache.exists()
    assert payload["paper_id"] == paper.id
    assert payload["page_count"] == 1
    assert "subprocess extraction works" in payload["pages"][0]["text"]
    assert not list(cache.parent.glob(f"{cache.name}.tmp-*"))


def test_cached_pdf_text_is_revalidated_against_current_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: Session,
) -> None:
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    monkeypatch.setattr(ingest.settings, "paper_text_max_chars", 5)
    ingest.settings.ensure_directories()
    path = ingest.settings.pdf_dir / "cached.pdf"
    _write_pdf(path)
    paper = _local_pdf_paper(session, path)
    cache = ingest.settings.text_dir / f"{paper.id}.json"
    cache.write_text(
        json.dumps(
            {
                "paper_id": paper.id,
                "page_count": 1,
                "pages": [{"page_number": 1, "text": "too-large"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Cached PDF text exceeds"):
        ingest.ensure_pdf_text_cache(session, paper)


def test_extraction_timeout_terminates_child_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_context = multiprocessing.get_context("spawn")
    observed: dict[str, object] = {}

    class FakeProcess:
        exitcode = None

        def __init__(self, **_kwargs: object) -> None:
            self.alive = False
            self.terminated = False

        def start(self) -> None:
            self.alive = True

        def join(self, timeout: float | None = None) -> None:
            observed.setdefault("join_timeouts", []).append(timeout)  # type: ignore[union-attr]

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False
            observed["terminated"] = True

        def kill(self) -> None:
            self.alive = False
            observed["killed"] = True

        def close(self) -> None:
            return None

    class FakeContext:
        def Pipe(self, *, duplex: bool):  # type: ignore[no-untyped-def]
            return real_context.Pipe(duplex=duplex)

        def Process(self, **kwargs: object) -> FakeProcess:
            process = FakeProcess(**kwargs)
            observed["process"] = process
            return process

    monkeypatch.setattr(ingest.multiprocessing, "get_context", lambda _method: FakeContext())
    cache = tmp_path / "paper.json"

    with pytest.raises(TimeoutError, match="hard timeout"):
        ingest._extract_pdf_text_to_cache(tmp_path / "source.pdf", cache, "paper-id")

    assert observed["terminated"] is True
    assert not cache.exists()
    assert not list(tmp_path.glob("paper.json.tmp-*"))
