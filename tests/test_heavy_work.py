from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import Paper
from frontier.services import heavy_work, ingest


def test_reentrant_gate_serializes_threads_without_deadlocking_nested_calls() -> None:
    gate = heavy_work._ReentrantGate(1)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with gate.slot(), gate.slot():
            first_entered.set()
            release_first.wait(timeout=1)

    def second() -> None:
        first_entered.wait(timeout=1)
        with gate.slot():
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=1)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    assert second_entered.wait(timeout=1)
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _remote_paper(session: Session) -> Paper:
    paper = Paper(
        title="Bounded remote PDF",
        normalized_title="bounded remote pdf",
        pdf_url="https://example.com/paper.pdf",
    )
    session.add(paper)
    session.commit()
    return paper


def test_pdf_download_streams_to_an_atomic_file(monkeypatch, tmp_path: Path, session: Session) -> None:
    paper = _remote_paper(session)
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    monkeypatch.setattr(ingest, "_safe_remote_url", lambda url: url)
    monkeypatch.setattr(ingest, "external_httpx_kwargs", lambda _url: {})
    ingest.settings.ensure_directories()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"%PDF-1.7\nsmall document",
                request=request,
            )
        )
    )
    monkeypatch.setattr(ingest.httpx, "stream", client.stream)

    path = ingest.download_paper_pdf(session, paper)

    assert path.read_bytes() == b"%PDF-1.7\nsmall document"
    assert not path.with_suffix(".pdf.download").exists()
    client.close()


def test_pdf_download_rejects_actual_bytes_over_limit(
    monkeypatch,
    tmp_path: Path,
    session: Session,
) -> None:
    paper = _remote_paper(session)
    monkeypatch.setattr(ingest.settings, "data_dir", tmp_path)
    monkeypatch.setattr(ingest.settings, "paper_download_max_bytes", 8)
    monkeypatch.setattr(ingest, "_safe_remote_url", lambda url: url)
    monkeypatch.setattr(ingest, "external_httpx_kwargs", lambda _url: {})
    ingest.settings.ensure_directories()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"%PDF-1.7-too-large",
                request=request,
            )
        )
    )
    monkeypatch.setattr(ingest.httpx, "stream", client.stream)

    with pytest.raises(ValueError, match="size limit"):
        ingest.download_paper_pdf(session, paper)

    target = ingest.settings.pdf_dir / f"{paper.id}.pdf"
    assert not target.exists()
    assert not target.with_suffix(".pdf.download").exists()
    client.close()
