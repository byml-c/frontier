from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import ArchitectureDocument, Paper
from frontier.services import storage_migration


def test_storage_migration_removes_arxiv_derivatives_and_keeps_uploads(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(storage_migration.settings, "data_dir", tmp_path)
    storage_migration.settings.ensure_directories()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    arxiv_pdf = storage_migration.settings.pdf_dir / "arxiv.pdf"
    upload_pdf = storage_migration.settings.pdf_dir / "upload.pdf"
    arxiv_pdf.write_bytes(b"arxiv")
    upload_pdf.write_bytes(b"upload")
    arxiv_text = storage_migration.settings.text_dir / "arxiv.json"
    upload_text = storage_migration.settings.text_dir / "upload.json"
    arxiv_text.write_text("{}", encoding="utf-8")
    upload_text.write_text("{}", encoding="utf-8")
    arxiv_doc = storage_migration.settings.document_dir / "arxiv"
    upload_doc = storage_migration.settings.document_dir / "upload"
    arxiv_doc.mkdir()
    upload_doc.mkdir()
    (arxiv_doc / "reader.json").write_text("{}", encoding="utf-8")
    (upload_doc / "figure.png").write_bytes(b"image")

    with Session(engine) as session:
        arxiv = Paper(
            id="arxiv",
            title="arXiv paper",
            normalized_title="arxiv paper",
            source="arxiv",
            arxiv_id="2410.24164",
            arxiv_version=1,
            pdf_path=str(arxiv_pdf),
        )
        upload = Paper(
            id="upload",
            title="Uploaded paper",
            normalized_title="uploaded paper",
            source="upload",
            pdf_path=str(upload_pdf),
        )
        session.add_all([arxiv, upload])
        session.add(
            ArchitectureDocument(
                paper_id="upload",
                scene_json={"assets": [{"src": "asset://document/figure.png"}]},
            )
        )
        session.commit()

        result = storage_migration.run_storage_migration(session, dry_run=False)
        session.commit()

        assert result["deleted_pdfs"] == 1
        assert result["deleted_text_caches"] == 2
        assert result["deleted_document_bundles"] == 1
        assert not arxiv_pdf.exists()
        assert not arxiv_text.exists()
        assert not arxiv_doc.exists()
        assert upload_pdf.exists()
        assert not upload_text.exists()
        assert upload_doc.exists()
        assert arxiv.pdf_path is None
        assert upload.pdf_path == str(upload_pdf)
        assert (tmp_path / "migrations" / "storage.arxiv_link_only.v1.json").exists()
