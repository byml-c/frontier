from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pymupdf
from fastapi.testclient import TestClient

from frontier.agent_runner import _parse_citations
from frontier.api.papers import _translation_key
from frontier.schemas import TranslationRequest
from frontier.services.document_reader import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentReaderUnavailable,
    _document_payload_is_current,
    _estimate_conversion_seconds,
    _processing_status_is_live,
    convert_with_docling,
)
from frontier.services.ingest import parse_arxiv_abs_html


def create_paper(client: TestClient, title: str = "Tactile VLA for Fine Manipulation") -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": title,
            "abstract": "We combine tactile sensing with a vision-language-action policy.",
            "authors": [{"name": "Ada Researcher"}],
            "first_author": "Ada Researcher",
            "source": "manual",
            "published_at": "2026-08-01",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def test_parse_citations_supports_page_ranges() -> None:
    citations = _parse_citations(
        "范围 [[page:1–2]]、半角 [[p:3-5]] 与自定义名称 [[page:8—9|实验部分]]。",
        "paper-1",
    )

    assert citations == [
        {
            "paper_id": "paper-1",
            "page_number": 1,
            "end_page_number": 2,
            "label": "第 1–2 页",
            "marker": "[[page:1–2]]",
        },
        {
            "paper_id": "paper-1",
            "page_number": 3,
            "end_page_number": 5,
            "label": "第 3–5 页",
            "marker": "[[p:3-5]]",
        },
        {
            "paper_id": "paper-1",
            "page_number": 8,
            "end_page_number": 9,
            "label": "实验部分",
            "marker": "[[page:8—9|实验部分]]",
        },
    ]


def test_parse_citations_supports_separate_page_lists() -> None:
    citations = _parse_citations("两处证据 [[page:2, 12]]，另一处 [[p:4，7]]。", "paper-1")

    assert [(item["page_number"], item["label"]) for item in citations] == [
        (2, "第 2 页"),
        (12, "第 12 页"),
        (4, "第 4 页"),
        (7, "第 7 页"),
    ]
    assert citations[0]["marker"] == citations[1]["marker"] == "[[page:2, 12]]"


def test_translation_cache_key_is_stable_across_selection_geometry() -> None:
    first = TranslationRequest(
        text="action-grounded latent",
        page_number=2,
        position_json={"rects": [[0.1, 0.2, 0.3, 0.04]]},
    )
    second = TranslationRequest(
        text="action-grounded   latent",
        page_number=2,
        position_json={"rects": [[0.11, 0.21, 0.29, 0.05]]},
    )
    assert _translation_key("paper-id", first) == _translation_key("paper-id", second)


def test_arxiv_abs_metadata_parses_all_authors_without_atom_api() -> None:
    payload = parse_arxiv_abs_html(
        """
        <html><head>
          <meta property="og:url" content="https://arxiv.org/abs/2608.10232v1" />
          <meta name="citation_title" content="FACT: Failure-Aware Causal Training" />
          <meta name="citation_author" content="Peng, Quanquan" />
          <meta name="citation_author" content="Liang, Yutong" />
          <meta name="citation_date" content="2026/08/10" />
          <meta name="citation_pdf_url" content="https://arxiv.org/pdf/2608.10232" />
          <meta name="citation_arxiv_id" content="2608.10232" />
          <meta name="citation_abstract" content="Failure-aware world-action training." />
        </head></html>
        """,
        "2608.10232",
    )

    assert payload.first_author == "Quanquan Peng"
    assert [author["name"] for author in payload.authors] == [
        "Quanquan Peng",
        "Yutong Liang",
    ]
    assert payload.arxiv_version == 1
    assert payload.published_at.isoformat() == "2026-08-10"


def test_health_and_defaults(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    settings = {item["key"]: item["value_json"] for item in client.get("/api/settings").json()}
    assert settings["abstract_limit"] == 50
    assert settings["fulltext_limit"] == 19
    assert settings["brief_limit"] == 9
    assert settings["featured_limit"] == 4
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 7, "featured_limit": 3},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/settings",
            json={"brief_limit": 3, "featured_limit": 3},
        ).status_code
        == 422
    )


def test_paper_dedup_annotations_and_feedback(client: TestClient) -> None:
    paper = create_paper(client)
    duplicate = create_paper(client)
    assert duplicate["id"] == paper["id"]
    assert (
        client.patch(f"/api/papers/{paper['id']}", json={"status": "completed"}).status_code == 422
    )

    started = client.post(f"/api/papers/{paper['id']}/start-reading")
    assert started.status_code == 200
    assert started.json()["status"] == "reading"

    annotation = client.post(
        f"/api/papers/{paper['id']}/annotations",
        json={
            "kind": "highlight",
            "page_number": 2,
            "selected_text": "tactile sensing",
            "position_json": {"rects": [[0.1, 0.2, 0.3, 0.1]]},
        },
    )
    assert annotation.status_code == 200
    assert annotation.json()["kind"] == "note"
    assert annotation.json()["content"] == ""
    assert len(client.get(f"/api/papers/{paper['id']}/annotations").json()) == 1
    updated_annotation = client.patch(
        f"/api/papers/{paper['id']}/annotations/{annotation.json()['id']}",
        json={
            "kind": "note",
            "page_number": 2,
            "selected_text": "tactile sensing",
            "content": "Compare this representation with vision features.",
            "position_json": {"rects": [[0.1, 0.2, 0.3, 0.1]]},
            "color": "#9CCCF4",
        },
    )
    assert updated_annotation.json()["color"] == "#9CCCF4"
    assert (
        client.delete(
            f"/api/papers/{paper['id']}/annotations/{annotation.json()['id']}"
        ).status_code
        == 204
    )
    assert client.get(f"/api/papers/{paper['id']}/annotations").json() == []

    tag = client.post("/api/tags", json={"name": "VLA"}).json()
    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={
            "summary": "A useful tactile VLA paper [[page:2]].",
            "suggested_interested": True,
            "suggested_quality": True,
            "suggested_tags": ["VLA"],
        },
    )
    assert finish.json()["status"] == "feedback_pending"
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={
            "interested": True,
            "quality": True,
            "tag_ids": [tag["id"]],
            "new_tags": ["触觉"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    graph_tasks = [
        task
        for task in client.get("/api/tasks", params={"limit": 20}).json()
        if task["kind"] == "graph_maintenance"
        and task["payload_json"].get("context", {}).get("paper_id") == paper["id"]
    ]
    assert graph_tasks
    library = client.get("/api/papers", params={"library": True}).json()
    assert any(item["id"] == paper["id"] for item in library["items"])
    assert library["total"] >= 1
    node = client.post(
        "/api/graph/nodes",
        json={"name": "Tactile Library Test", "core_question": "How does touch improve control?"},
    ).json()
    assert (
        client.post(
            "/api/graph/paper-links",
            json={"paper_id": paper["id"], "node_id": node["id"], "reason": "Library test"},
        ).status_code
        == 200
    )
    cancelled = client.post(f"/api/papers/{paper['id']}/cancel-reading")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "candidate"
    assert cancelled.json()["reading_completed_at"] is None
    assert cancelled.json()["is_interested"] is False
    assert cancelled.json()["is_quality"] is False
    assert cancelled.json()["pending_feedback"] == {}
    assert cancelled.json()["nodes"] == []
    assert {item["name"] for item in cancelled.json()["tags"]} == {"VLA", "触觉"}
    assert not any(
        item["id"] == paper["id"]
        for item in client.get("/api/papers", params={"library": True}).json()["items"]
    )


def test_papers_are_grouped_by_reading_status(client: TestClient) -> None:
    candidate = create_paper(client, "Status order candidate")
    reading = create_paper(client, "Status order reading")
    pending = create_paper(client, "Status order pending")
    completed = create_paper(client, "Status order completed")
    ignored = create_paper(client, "Status order ignored")

    assert client.post(f"/api/papers/{reading['id']}/start-reading").status_code == 200
    assert client.post(f"/api/papers/{pending['id']}/finish", json={}).status_code == 200
    assert client.post(f"/api/papers/{completed['id']}/finish", json={}).status_code == 200
    assert client.post(
        f"/api/papers/{completed['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    ).status_code == 200
    assert client.post(
        f"/api/papers/{ignored['id']}/ignore",
        json={"reason": "not relevant"},
    ).status_code == 200

    target_ids = {candidate["id"], reading["id"], pending["id"], completed["id"], ignored["id"]}
    ordered = [
        item["status"]
        for item in client.get("/api/papers", params={"limit": 200}).json()["items"]
        if item["id"] in target_ids
    ]
    assert ordered == ["feedback_pending", "reading", "candidate", "completed", "ignored"]


def test_timestamps_are_serialized_in_beijing_time(client: TestClient) -> None:
    memory = client.post(
        "/api/memories",
        json={"kind": "agent_experience", "title": "Timezone", "content": "UTC storage"},
    ).json()
    assert memory["created_at"].endswith("+08:00")


def test_graph_and_brief_snapshot(client: TestClient) -> None:
    paper = create_paper(client, "A New Robot World Model")
    parent = client.post(
        "/api/graph/nodes",
        json={"name": "VLA Test", "core_question": "How should robots act?"},
    ).json()
    child = client.post(
        "/api/graph/nodes",
        json={"name": "Tactile VLA Test", "core_question": "How should touch improve action?"},
    ).json()
    assert (
        client.post(
            "/api/graph/edges", json={"source_id": parent["id"], "target_id": child["id"]}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/graph/paper-links",
            json={
                "paper_id": paper["id"],
                "node_id": child["id"],
                "reason": "Addresses the core question.",
                "is_key": True,
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/progress",
            json={
                "title": "New evidence",
                "content": "Touch improved success.",
                "paper_id": paper["id"],
            },
        ).status_code
        == 409
    )
    assert client.get(f"/api/graph/nodes/{child['id']}").json()["paper_count"] == 0

    finish = client.post(
        f"/api/papers/{paper['id']}/finish",
        json={"summary": "Reading complete."},
    )
    assert finish.status_code == 200
    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert (
        client.post(
            "/api/graph/paper-links",
            json={
                "paper_id": paper["id"],
                "node_id": child["id"],
                "reason": "Addresses the core question.",
                "is_key": True,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/progress",
            json={
                "title": "New evidence",
                "content": "Touch improved success.",
                "paper_id": paper["id"],
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/graph/nodes/{child['id']}/ideas",
            json={"content": "Test asynchronous fusion.", "status": "to_explore"},
        ).status_code
        == 200
    )
    detail = client.get(f"/api/graph/nodes/{child['id']}").json()
    assert detail["parents"][0]["id"] == parent["id"]
    assert detail["papers"][0]["id"] == paper["id"]
    assert detail["ideas"][0]["status"] == "to_explore"

    brief_paper = create_paper(client, "Unread paper for brief snapshot")
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-29", "title": "Test brief"},
    ).json()
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": brief_paper["id"], "rank": 1, "tier": "featured"},
        ).status_code
        == 200
    )
    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200
    assert published.json()["items"][0]["paper"]["title"] == brief_paper["title"]
    assert client.post(
        f"/api/papers/{brief_paper['id']}/finish",
        json={"summary": "Reading complete after publication."},
    ).status_code == 200
    assert client.post(
        f"/api/papers/{brief_paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    ).status_code == 200
    historical = client.get(f"/api/briefs/{brief['id']}").json()
    assert historical["items"][0]["paper"]["completion_summary"] == (
        "Reading complete after publication."
    )
    assert (
        client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": brief_paper["id"], "rank": 2, "tier": "related"},
        ).status_code
        == 409
    )


def test_daily_brief_excludes_papers_that_are_being_or_have_been_read(
    client: TestClient,
) -> None:
    candidate = create_paper(client, "Unread daily candidate")
    other_candidate = create_paper(client, "Second unread daily candidate")
    reading = create_paper(client, "Already being read")
    completed = create_paper(client, "Already completed")
    assert client.post(f"/api/papers/{reading['id']}/start-reading").status_code == 200
    assert client.post(f"/api/papers/{completed['id']}/finish", json={}).status_code == 200
    assert client.post(
        f"/api/papers/{completed['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    ).status_code == 200

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    brief = client.post(
        "/api/briefs",
        json={"brief_date": today, "title": "Unread-only daily brief"},
    ).json()
    for rank, paper in enumerate((candidate, other_candidate), start=1):
        assert client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": rank, "tier": "featured"},
        ).status_code == 200
    for paper in (reading, completed):
        rejected = client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": 3, "tier": "related"},
        )
        assert rejected.status_code == 409
        assert "Only unread candidate papers" in rejected.json()["detail"]

    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200, published.text
    assert {item["paper"]["id"] for item in published.json()["items"]} == {
        candidate["id"],
        other_candidate["id"],
    }

    assert client.post(f"/api/papers/{candidate['id']}/start-reading").status_code == 200
    current = client.get(f"/api/briefs/{brief['id']}").json()
    assert [item["paper"]["id"] for item in current["items"]] == [other_candidate["id"]]

    assert client.post(f"/api/papers/{candidate['id']}/cancel-reading").status_code == 200
    restored = client.get(f"/api/briefs/{brief['id']}").json()
    assert {item["paper"]["id"] for item in restored["items"]} == {
        candidate["id"],
        other_candidate["id"],
    }


def test_brief_publish_drops_papers_that_started_reading_after_ranking(
    client: TestClient,
) -> None:
    stale = create_paper(client, "Candidate that starts reading before publish")
    eligible = create_paper(client, "Candidate that remains unread")
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-28", "title": "Publish race test"},
    ).json()
    for rank, paper in enumerate((stale, eligible), start=1):
        assert client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": rank, "tier": "featured"},
        ).status_code == 200
    assert client.post(f"/api/papers/{stale['id']}/start-reading").status_code == 200

    published = client.post(f"/api/briefs/{brief['id']}/publish")

    assert published.status_code == 200, published.text
    assert [item["paper"]["id"] for item in published.json()["items"]] == [eligible["id"]]


def test_brief_publish_prewarms_only_first_four_featured_papers(
    client: TestClient,
    monkeypatch,
) -> None:
    queued_batches: list[list[str]] = []
    monkeypatch.setattr(
        "frontier.api.briefs.queue_document_conversions",
        lambda paper_ids: queued_batches.append(list(paper_ids)),
    )
    monkeypatch.setattr(
        "frontier.api.briefs.get_setting",
        lambda _session, key, default: 6 if key == "featured_limit" else default,
    )
    brief = client.post(
        "/api/briefs",
        json={"brief_date": "2026-07-30", "title": "Featured text cache test"},
    ).json()
    featured_ids = []
    for index in range(5):
        paper = create_paper(client, f"Featured cache paper {index}")
        featured_ids.append(paper["id"])
        assert client.post(
            f"/api/briefs/{brief['id']}/items",
            json={"paper_id": paper["id"], "rank": index + 1, "tier": "featured"},
        ).status_code == 200
    related = create_paper(client, "Related paper without text prewarm")
    assert client.post(
        f"/api/briefs/{brief['id']}/items",
        json={"paper_id": related["id"], "rank": 6, "tier": "related"},
    ).status_code == 200

    published = client.post(f"/api/briefs/{brief['id']}/publish")

    assert published.status_code == 200, published.text
    assert queued_batches == [featured_ids[:4]]


def test_pdf_upload_and_extract(client: TestClient, monkeypatch) -> None:
    queued_batches: list[tuple[list[str], int]] = []
    monkeypatch.setattr(
        "frontier.api.papers.queue_document_conversions",
        lambda paper_ids, *, limit=4: queued_batches.append((list(paper_ids), limit)),
    )
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A Tactile Robot Paper")
    page.insert_text((72, 100), "The experiment improves manipulation success.")
    content = document.tobytes()
    document.close()
    response = client.post(
        "/api/papers/ingest/pdf",
        files={"file": ("paper.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    paper = response.json()["paper"]
    assert queued_batches == [([paper["id"]], 1)]
    assert paper["pdf_version"]
    pdf_response = client.get(f"/api/papers/{paper['id']}/pdf?v={paper['pdf_version']}")
    assert pdf_response.status_code == 200
    assert pdf_response.headers["cache-control"] == "private, max-age=31536000, immutable"
    text = client.get(f"/api/papers/{paper['id']}/text").json()
    assert text["page_count"] == 1
    assert "manipulation success" in text["pages"][0]["text"]
    rendered = client.post(
        f"/api/papers/{paper['id']}/render-page",
        params={"page_number": 1, "dpi": 96},
    )
    assert rendered.status_code == 200
    assert rendered.json()["path"].endswith("96dpi.png")
    assert (
        client.post(
            f"/api/papers/{paper['id']}/render-page",
            params={"page_number": 2},
        ).status_code
        == 400
    )


def test_document_reader_queues_and_serves_cached_result(client: TestClient, monkeypatch) -> None:
    paper = create_paper(client, "A Structured Mobile Reader Paper")
    queued: list[str] = []
    monkeypatch.setattr(
        "frontier.api.papers.document_status",
        lambda _session, item: {
            "paper_id": item.id,
            "version": "v1",
            "status": "missing",
        },
    )
    monkeypatch.setattr(
        "frontier.api.papers.queue_document_conversion",
        lambda paper_id, **_kwargs: queued.append(paper_id) or True,
    )
    response = client.get(f"/api/papers/{paper['id']}/document")
    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert queued == [paper["id"]]

    monkeypatch.setattr(
        "frontier.api.papers.document_status",
        lambda _session, item: {
            "paper_id": item.id,
            "version": "v1",
            "status": "ready",
            "blocks": [
                {
                    "id": "intro",
                    "kind": "text",
                    "page_number": 1,
                    "level": 0,
                    "text": "A readable paragraph.",
                }
            ],
        },
    )
    ready = client.get(f"/api/papers/{paper['id']}/document")
    assert ready.json()["blocks"][0]["id"] == "intro"


def test_document_reader_estimate_scales_with_page_count() -> None:
    short = _estimate_conversion_seconds(4)
    long = _estimate_conversion_seconds(40)
    assert short[0] < short[1]
    assert long[0] > short[0]
    assert long[1] > short[1]


def test_document_reader_detects_stale_queued_status() -> None:
    fresh = datetime.now(UTC).isoformat()
    stale = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    assert _processing_status_is_live("not-running", {"phase": "queued", "updated_at": fresh})
    assert not _processing_status_is_live("not-running", {"phase": "queued", "updated_at": stale})


def test_document_reader_invalidates_old_cache_and_worker_status() -> None:
    version = "pdf-v1"

    assert _document_payload_is_current(
        {"version": version, "schema_version": DOCUMENT_SCHEMA_VERSION}, version
    )
    assert not _document_payload_is_current(
        {"version": version, "schema_version": DOCUMENT_SCHEMA_VERSION - 1}, version
    )
    assert not _document_payload_is_current(
        {"version": "pdf-v0", "schema_version": DOCUMENT_SCHEMA_VERSION}, version
    )
    assert _document_payload_is_current(
        {"schema_version": DOCUMENT_SCHEMA_VERSION}, version, allow_missing_version=True
    )


def test_docling_is_blocked_before_import_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("frontier.services.document_reader.settings.enable_docling", False)
    try:
        convert_with_docling(
            tmp_path / "unused.pdf",
            paper_id="paper-without-html",
            version="v1",
            root=tmp_path,
        )
    except DocumentReaderUnavailable as exc:
        assert "Docling 已在当前部署中关闭" in str(exc)
    else:
        raise AssertionError("disabled Docling must not be imported or started")


def test_manual_ingest_stages_daily_recommendation_and_metadata_edit(
    client: TestClient,
    monkeypatch,
) -> None:
    queued_batches: list[tuple[list[str], int]] = []
    monkeypatch.setattr(
        "frontier.api.papers.queue_document_conversions",
        lambda paper_ids, *, limit=4: queued_batches.append((list(paper_ids), limit)),
    )
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A User Added Tactile Foundation Policy")
    page.insert_text((72, 100), "The full paper studies transferable tactile manipulation.")
    content = document.tobytes()
    document.close()

    response = client.post(
        "/api/papers/ingest/pdf",
        data={"recommend": "true"},
        files={"file": ("manual-tactile.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_task_id"]
    assert result["paper"]["analysis_status"] == "queued"
    assert queued_batches == [([result["paper"]["id"]], 1)]

    latest = client.get("/api/briefs/latest")
    assert latest.status_code == 200
    manual_items = [
        item for item in latest.json()["items"] if item["paper"]["id"] == result["paper"]["id"]
    ]
    assert len(manual_items) == 1
    assert manual_items[0]["tier"] == "manual"
    assert manual_items[0]["paper"]["analysis_status"] == "queued"

    suggested = client.patch(
        f"/api/papers/{result['paper']['id']}",
        json={"suggested_tags": ["触觉", "基础策略"]},
    )
    assert suggested.status_code == 200
    assert suggested.json()["suggested_tags"] == ["触觉", "基础策略"]

    edited = client.patch(
        f"/api/papers/{result['paper']['id']}/metadata",
        json={
            "title": "Edited Tactile Foundation Policy",
            "authors": [{"name": "First Author"}, {"name": "Second Author"}],
            "first_author": "First Author",
            "corresponding_author": "Second Author",
            "primary_affiliation": "Tactile Robotics Lab",
            "summary": "A manually reviewed summary.",
            "agent_comment": "Promising cross-sensor transfer.",
            "recommendation_reason": "Relevant to tactile policy generalization.",
            "tag_ids": [],
            "new_tags": ["触觉"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Edited Tactile Foundation Policy"
    assert edited.json()["corresponding_author"] == "Second Author"
    assert [tag["name"] for tag in edited.json()["tags"]] == ["触觉"]

    refreshed = client.get("/api/briefs/latest").json()
    manual = next(
        item for item in refreshed["items"] if item["paper"]["id"] == result["paper"]["id"]
    )
    assert manual["paper"]["title"] == "Edited Tactile Foundation Policy"
    assert manual["paper"]["summary"] == "A manually reviewed summary."


def test_manual_ingest_can_defer_agent_analysis(client: TestClient, monkeypatch) -> None:
    queued_batches: list[tuple[list[str], int]] = []
    monkeypatch.setattr(
        "frontier.api.papers.queue_document_conversions",
        lambda paper_ids, *, limit=4: queued_batches.append((list(paper_ids), limit)),
    )
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Deferred Agent Analysis Paper")
    content = document.tobytes()
    document.close()

    response = client.post(
        "/api/papers/ingest/pdf",
        data={"recommend": "true", "analyze": "false"},
        files={"file": ("deferred-analysis.pdf", content, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis_task_id"] is None
    assert result["paper"]["analysis_status"] is None
    assert queued_batches == [([result["paper"]["id"]], 1)]
    latest = client.get("/api/briefs/latest").json()
    assert any(item["paper"]["id"] == result["paper"]["id"] for item in latest["items"])


def test_memory_task_and_conversation(client: TestClient) -> None:
    memory = client.post(
        "/api/memories",
        json={
            "kind": "interest_profile",
            "title": "Tactile interests",
            "content": "Prefer tactile VLA papers.",
        },
    )
    assert memory.status_code == 200
    assert (
        client.patch(f"/api/memories/{memory.json()['id']}", json={"active": False}).status_code
        == 200
    )

    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Research assistant"}
    ).json()
    sent = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Show me recent tactile work.", "context_json": {"page": "brief"}},
    )
    assert sent.status_code == 200
    task = client.get(f"/api/tasks/{sent.json()['task_id']}").json()
    assert task["status"] == "queued"


def test_streamed_conversation_persists_activity_with_answer(
    client: TestClient,
    monkeypatch,
) -> None:
    from frontier.api import conversations

    monkeypatch.setattr(conversations.settings, "enable_codex_runs", True)

    def fake_stream(*_args, **_kwargs):
        yield {"type": "thread", "thread_id": "thread-with-activity"}
        yield {
            "type": "activity",
            "activity_id": "tool-1",
            "kind": "tool",
            "label": "正在读取论文",
            "status": "running",
        }
        yield {
            "type": "activity",
            "activity_id": "tool-1",
            "kind": "tool",
            "label": "已读取论文",
            "status": "completed",
        }
        yield {"type": "delta", "delta": "**结论** 正文"}

    monkeypatch.setattr(conversations, "stream_codex_prompt", fake_stream)
    conversation = client.post(
        "/api/conversations", json={"kind": "global", "title": "Activity persistence"}
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "请分析"},
    )
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assistant = detail["messages"][-1]
    assert assistant["content"] == "**结论** 正文"
    activities = assistant["context_json"]["activities"]
    assert next(item for item in activities if item["id"] == "tool-1") == {
        "id": "tool-1",
        "kind": "tool",
        "label": "已读取论文",
        "status": "completed",
    }


def test_metadata_generation_can_be_requested_without_adding_a_brief_item(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Explicit Metadata Generation Test")
    requested = client.post(f"/api/papers/{paper['id']}/metadata/generate")
    assert requested.status_code == 200, requested.text
    assert requested.json()["created"] is True
    repeated = client.post(f"/api/papers/{paper['id']}/metadata/generate")
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["id"] == requested.json()["id"]

    detail = client.get(f"/api/papers/{paper['id']}").json()
    assert detail["analysis_status"] == "queued"
    after = client.get("/api/briefs/latest")
    after_ids = (
        {item["paper"]["id"] for item in after.json().get("items", [])}
        if after.status_code == 200
        else set()
    )
    assert paper["id"] not in after_ids


def test_confirm_feedback_preserves_metadata_tags_when_tag_ids_are_omitted(
    client: TestClient,
) -> None:
    paper = create_paper(client, "Preserve Metadata Tags on Reading Completion")
    tag = client.post("/api/tags", json={"name": "保留标签"}).json()
    saved = client.patch(
        f"/api/papers/{paper['id']}/metadata",
        json={"tag_ids": [tag["id"]]},
    )
    assert [item["name"] for item in saved.json()["tags"]] == ["保留标签"]
    assert client.post(f"/api/papers/{paper['id']}/finish", json={}).status_code == 200

    confirmed = client.post(
        f"/api/papers/{paper['id']}/confirm-feedback",
        json={"interested": False, "quality": False},
    )
    assert confirmed.status_code == 200
    assert [item["name"] for item in confirmed.json()["tags"]] == ["保留标签"]


def test_scheduled_daily_is_idempotent(client: TestClient) -> None:
    from frontier.config import settings
    from frontier.scheduler import scheduled_daily

    target = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    scheduled_daily()
    scheduled_daily()
    tasks = client.get("/api/tasks", params={"limit": 200}).json()
    matching = [
        task
        for task in tasks
        if task["kind"] == "daily_brief" and task["payload_json"].get("date") == target
    ]
    assert len(matching) == 1
