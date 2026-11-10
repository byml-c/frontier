from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from frontier.schemas import PaperCreate
from frontier.services import arxiv_rss, ingest


def _feed(*items: tuple[str, str], date: str = "Mon, 09 Nov 2026 05:00:00 +0000") -> str:
    entries = "".join(
        f"""<item><guid>oai:arXiv.org:{identifier}</guid><title>{title}</title>
        <description>arXiv:{identifier} Announce Type: new\nAbstract: Robot policy.</description>
        <category>cs.RO</category><pubDate>{date}</pubDate>
        <dc:creator>Ada Lee, Lin Kim</dc:creator><arxiv:announce_type>new</arxiv:announce_type>
        </item>"""
        for identifier, title in items
    )
    return f"""<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:arxiv="http://arxiv.org/schemas/atom"><channel><pubDate>{date}</pubDate>
    {entries}</channel></rss>"""


def _response(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, request=httpx.Request("GET", arxiv_rss.RSS_BASE))


def _fetch():
    return arxiv_rss.fetch_rss_records(
        end=datetime(2026, 11, 10, 2, tzinfo=UTC), categories=["cs.RO", "cs.AI"]
    )


def test_rss_keeps_whole_batch_and_works_when_metadata_api_is_down(monkeypatch):
    # A known-looking/old ID at the start must not terminate the RSS batch.
    body = _feed(("2501.12345v2", "Old paper new version"), ("2611.08576v1", "Late new paper"))
    monkeypatch.setattr(arxiv_rss, "_arxiv_get", lambda *a, **k: _response(body))

    def unavailable(ids):
        raise httpx.ReadTimeout("metadata API down")

    monkeypatch.setattr(arxiv_rss, "fetch_arxiv_ids", unavailable)
    items, stats = _fetch()
    assert [(item.arxiv_id, item.arxiv_version) for item in items] == [
        ("2501.12345", 2),
        ("2611.08576", 1),
    ]
    assert items[0].authors == [{"name": "Ada Lee"}, {"name": "Lin Kim"}]
    assert items[0].metadata_json["categories"] == ["cs.RO"]
    assert items[0].abstract == "Robot policy."
    assert items[0].published_at is None
    assert "arxiv_updated_at" not in items[0].metadata_json
    assert stats["metadata_pending"] == 2
    assert stats["cursor_can_advance"] is False
    assert stats["complete"] is True


def test_rss_enrichment_uses_exact_versions_and_keeps_announcement_clock_separate(monkeypatch):
    body = _feed(("2611.08576v1", "Late article"))
    monkeypatch.setattr(arxiv_rss, "_arxiv_get", lambda *a, **k: _response(body))
    calls = []

    def metadata(ids):
        calls.append(ids)
        return [
            PaperCreate(
                title="Late article",
                arxiv_id="2611.08576",
                arxiv_version=1,
                metadata_json={"arxiv_updated_at": "2026-11-05T18:44:00Z"},
            )
        ]

    monkeypatch.setattr(arxiv_rss, "fetch_arxiv_ids", metadata)
    items, stats = _fetch()
    assert calls == [["2611.08576v1"]]
    assert items[0].metadata_json["arxiv_updated_at"] == "2026-11-05T18:44:00Z"
    assert items[0].metadata_json["arxiv_announced_at"] == "2026-11-09T05:00:00+00:00"
    assert items[0].metadata_json["arxiv_timestamp_pending"] is False
    assert stats["cursor_can_advance"] is True


@pytest.mark.parametrize(
    "body",
    [
        "<html>bad gateway</html>",
        _feed().replace("<pubDate>", "<invalid>").replace("</pubDate>", "</invalid>"),
        _feed(("bad-id", "Broken paper")),
    ],
)
def test_invalid_rss_is_not_a_successful_empty_batch(monkeypatch, body):
    monkeypatch.setattr(arxiv_rss, "_arxiv_get", lambda *a, **k: _response(body))
    with pytest.raises(ValueError):
        _fetch()


def test_rss_splits_capped_combined_feed_and_rejects_capped_single_category(monkeypatch):
    monkeypatch.setattr(arxiv_rss, "RSS_RESULT_LIMIT", 1)
    body = _feed(("2611.08576v1", "Potentially truncated"))
    calls = []

    def get(params, **kwargs):
        calls.append(kwargs["url"])
        return _response(body)

    monkeypatch.setattr(arxiv_rss, "_arxiv_get", get)
    with pytest.raises(ValueError, match="truncated"):
        _fetch()
    assert calls == [arxiv_rss.RSS_BASE + "cs.RO+cs.AI", arxiv_rss.RSS_BASE + "cs.RO"]


def test_rss_rejects_newer_batch_for_historical_cutoff(monkeypatch):
    monkeypatch.setattr(arxiv_rss, "_arxiv_get", lambda *a, **k: _response(_feed()))
    with pytest.raises(ValueError, match="newer batch"):
        arxiv_rss.fetch_rss_records(end=datetime(2026, 11, 8, 2, tzinfo=UTC), categories=["cs.RO"])


def test_legacy_article_ids_support_version_identity():
    from frontier.services.common import parse_arxiv_id

    assert parse_arxiv_id("oai:arXiv.org:hep-th/9901001v2") == ("hep-th/9901001", 2)


def test_bulk_id_metadata_does_not_silently_drop_articles(monkeypatch):
    body = """<feed xmlns="http://www.w3.org/2005/Atom"
    xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
    <opensearch:startIndex>0</opensearch:startIndex>
    <opensearch:totalResults>0</opensearch:totalResults></feed>"""
    seen = []

    def get(params, **kwargs):
        seen.append(params)
        return _response(body)

    monkeypatch.setattr(ingest, "_arxiv_get", get)
    with pytest.raises(ValueError, match="omitted requested"):
        ingest.fetch_arxiv_ids(["2611.08576v1", "2501.12345v2"])
    assert seen[0]["id_list"] == "2611.08576v1,2501.12345v2"
    assert seen[0]["max_results"] == 2
