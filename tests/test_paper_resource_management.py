from __future__ import annotations

import multiprocessing
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


def test_document_runtime_keeps_at_most_five_temporary_slots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(document_reader._document_runtime, "_RUNTIME_ROOT", tmp_path / "runtime")
    document_reader._document_runtime.reset_document_runtime()
    document_reader._document_runtime._slots.clear()
    document_reader._document_runtime._inflight.clear()

    for index in range(6):
        slot = document_reader._document_runtime._slot_dir(f"paper-{index}")
        assert slot.is_dir()

    assert len(document_reader._document_runtime._slots) == 5
    assert "paper-0" not in document_reader._document_runtime._slots
    assert len(list((tmp_path / "runtime").iterdir())) == 5
    document_reader.shutdown_document_worker(wait_seconds=0)


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


def test_pdf_text_extraction_is_ephemeral(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: Session,
) -> None:
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    ingest.settings.ensure_directories()
    path = ingest.settings.pdf_dir / "source.pdf"
    _write_pdf(path, "subprocess extraction works")
    paper = _local_pdf_paper(session, path)

    payload = ingest.extract_pdf_text(session, paper)

    assert payload["paper_id"] == paper.id
    assert payload["page_count"] == 1
    assert "subprocess extraction works" in payload["pages"][0]["text"]
    assert not list(ingest.settings.text_dir.glob("*.json"))


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
