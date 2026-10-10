from __future__ import annotations

import json

from fastapi.testclient import TestClient

from frontier.database import SessionLocal
from frontier.models import OperationLog, Paper
from frontier.services.author_metadata import (
    AUTHOR_METADATA_PARSER_VERSION,
    BIBLIOGRAPHIC_PROVENANCE_KEY,
    MANUAL_BIBLIOGRAPHIC_FIELDS_KEY,
    apply_arxiv_html_author_metadata,
    backfill_cached_arxiv_author_metadata,
    extract_arxiv_html_author_metadata,
)

STRUCTURED_AUTHORS = """
<html><body><article class="ltx_document">
  <div class="ltx_authors">
    <span class="ltx_creator ltx_role_author">
      <span class="ltx_personname">Ada Researcher</span>
      <span class="ltx_contact ltx_role_thanks">
        <span class="ltx_contact_name">Thanks:</span>
        Corresponding author: ada@example.edu
      </span>
      <span class="ltx_contact ltx_role_affiliation">
        <span class="ltx_contact_name">Affiliation:</span>
        Frontier Robotics Institute
      </span>
    </span>
    <span class="ltx_creator ltx_role_author">
      <span class="ltx_personname">Grace Scientist</span>
      <span class="ltx_contact ltx_role_affiliation">
        <span class="ltx_contact_name">Affiliation:</span>
        Second Laboratory
      </span>
    </span>
  </div>
</article></body></html>
"""

SHARED_AFFILIATION = """
<html><body><article class="ltx_document">
  <div class="ltx_authors">
    <span class="ltx_creator ltx_role_author">
      <span class="ltx_personname">
        Ada Researcher, Grace Scientist<br />University of Example<br />
        <a href="https://example.edu/project">project</a>
      </span>
    </span>
  </div>
</article></body></html>
"""

AMBIGUOUS_CORRESPONDING = """
<html><body><article class="ltx_document">
  <div class="ltx_authors">
    <span class="ltx_creator ltx_role_author">
      <span class="ltx_personname">Ada Researcher, Grace Scientist</span>
      <span class="ltx_contact ltx_role_thanks">
        Thanks: Corresponding author: contact@example.edu
      </span>
    </span>
  </div>
</article></body></html>
"""


def known_authors() -> list[dict[str, str]]:
    return [{"name": "Ada Researcher"}, {"name": "Grace Scientist"}]


def test_extracts_explicit_corresponding_author_and_per_author_affiliation() -> None:
    result = extract_arxiv_html_author_metadata(STRUCTURED_AUTHORS, known_authors())

    assert result["first_author"] == "Ada Researcher"
    assert result["corresponding_authors"] == ["Ada Researcher"]
    assert result["primary_affiliation"] == "Frontier Robotics Institute"
    assert result["authors"][0] == {
        "name": "Ada Researcher",
        "affiliations": ["Frontier Robotics Institute"],
        "is_corresponding": True,
    }
    assert result["authors"][1]["affiliations"] == ["Second Laboratory"]


def test_extracts_one_shared_affiliation_without_guessing_author_order() -> None:
    result = extract_arxiv_html_author_metadata(SHARED_AFFILIATION, known_authors())

    assert result["first_author"] == "Ada Researcher"
    assert result["primary_affiliation"] == "University of Example"
    assert [author["affiliations"] for author in result["authors"]] == [
        ["University of Example"],
        ["University of Example"],
    ]


def test_does_not_guess_corresponding_author_from_an_unmapped_email() -> None:
    result = extract_arxiv_html_author_metadata(AMBIGUOUS_CORRESPONDING, known_authors())

    assert result["corresponding_authors"] == []
    assert result["warnings"] == ["通讯作者脚注存在，但无法可靠映射到具体作者"]


def test_explicit_html_enrichment_preserves_historical_manual_fields(
    client: TestClient,
) -> None:
    with SessionLocal() as session:
        paper = Paper(
            title="Protected author metadata",
            normalized_title="protected author metadata",
            authors=known_authors(),
            first_author="Ada Researcher",
            corresponding_author="Legacy Agent Guess",
            primary_affiliation="Manually Curated Institute",
            source="arxiv",
            arxiv_id="2608.19991",
            arxiv_version=1,
            metadata_json={},
        )
        session.add(paper)
        session.flush()
        session.add(
            OperationLog(
                actor="user",
                action="paper.metadata_updated",
                entity_type="paper",
                entity_id=paper.id,
                detail_json={"fields": ["primary_affiliation"]},
            )
        )
        session.flush()

        changed = apply_arxiv_html_author_metadata(
            session,
            paper,
            extract_arxiv_html_author_metadata(STRUCTURED_AUTHORS, paper.authors),
            source_url="https://arxiv.org/html/2608.19991v1",
        )

        assert paper.primary_affiliation == "Manually Curated Institute"
        assert paper.corresponding_author == "Ada Researcher"
        assert paper.authors[0]["affiliations"] == ["Frontier Robotics Institute"]
        assert changed == {"authors", "corresponding_author"}
        assert paper.metadata_json[MANUAL_BIBLIOGRAPHIC_FIELDS_KEY] == ["primary_affiliation"]
        session.rollback()


def test_metadata_editor_preserves_enrichment_and_agent_cannot_override_manual_value(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/papers",
        json={
            "title": "Metadata protection through API",
            "authors": known_authors(),
            "first_author": "Ada Researcher",
            "corresponding_author": "Ada Researcher",
            "primary_affiliation": "Frontier Robotics Institute",
            "source": "arxiv",
            "arxiv_id": "2608.19992",
            "metadata_json": {},
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()["paper"]
    with SessionLocal() as session:
        paper = session.get(Paper, created["id"])
        assert paper is not None
        paper.authors = [
            {**paper.authors[0], "affiliations": ["Frontier Robotics Institute"]},
            *paper.authors[1:],
        ]
        session.commit()

    saved = client.patch(
        f"/api/papers/{created['id']}/metadata",
        json={
            "authors": known_authors(),
            "primary_affiliation": "User Selected Institute",
            "abstract": "Only this prose and the affiliation changed.",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["authors"][0]["affiliations"] == ["Frontier Robotics Institute"]
    assert saved.json()["metadata_json"][MANUAL_BIBLIOGRAPHIC_FIELDS_KEY] == ["primary_affiliation"]

    agent_update = client.patch(
        f"/api/papers/{created['id']}",
        json={"primary_affiliation": "Agent Guess"},
    )
    assert agent_update.status_code == 200, agent_update.text
    assert agent_update.json()["primary_affiliation"] == "User Selected Institute"


def test_agent_can_save_verified_bibliography_and_group_summary(client: TestClient) -> None:
    response = client.post(
        "/api/papers",
        json={
            "title": "Agent bibliography extraction",
            "authors": [{"name": "Raw Author"}],
            "first_author": "Raw Author",
            "source": "arxiv",
            "arxiv_id": "2608.19994",
        },
    )
    assert response.status_code == 200, response.text
    paper_id = response.json()["paper"]["id"]

    saved = client.patch(
        f"/api/papers/{paper_id}",
        json={
            "first_author": "Verified Author",
            "primary_affiliation": "Verified Robotics Lab",
            "corresponding_author": "Corresponding Author",
            "research_group_summary": "Verified Robotics Lab 主要完成真实机器人操作与触觉控制研究。",
            "metadata_json": {
                "agent_bibliography": {
                    "source": "agent",
                    "confidence": "high",
                    "evidence": "论文首页作者单位与通讯作者脚注。",
                }
            },
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["first_author"] == "Verified Author"
    assert body["primary_affiliation"] == "Verified Robotics Lab"
    assert body["corresponding_author"] == "Corresponding Author"
    assert body["research_group_summary"].startswith("Verified Robotics Lab")
    assert body["metadata_json"]["agent_bibliography"]["confidence"] == "high"

    detail = client.get(f"/api/papers/{paper_id}")
    assert detail.status_code == 200
    assert detail.json()["research_group_summary"] == body["research_group_summary"]


def test_backfills_existing_cached_arxiv_html_without_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("frontier.services.author_metadata.settings.data_dir", tmp_path)
    with SessionLocal() as session:
        paper = Paper(
            title="Cached author metadata",
            normalized_title="cached author metadata",
            authors=known_authors(),
            first_author="Ada Researcher",
            source="arxiv",
            arxiv_id="2608.19993",
            arxiv_version=2,
            metadata_json={},
        )
        session.add(paper)
        session.commit()
        root = tmp_path / "documents" / paper.id
        root.mkdir(parents=True)
        (root / "arxiv.html").write_text(STRUCTURED_AUTHORS, encoding="utf-8")
        (root / "reader.json").write_text(
            json.dumps({"source_url": "https://arxiv.org/html/2608.19993v2"}),
            encoding="utf-8",
        )

        assert backfill_cached_arxiv_author_metadata(session) == 1
        session.refresh(paper)
        assert paper.corresponding_author == "Ada Researcher"
        assert paper.primary_affiliation == "Frontier Robotics Institute"
        provenance = paper.metadata_json[BIBLIOGRAPHIC_PROVENANCE_KEY]
        assert provenance["parser_version"] == AUTHOR_METADATA_PARSER_VERSION
        assert provenance["arxiv_version"] == 2
        assert backfill_cached_arxiv_author_metadata(session) == 0
        session.delete(paper)
        session.commit()
