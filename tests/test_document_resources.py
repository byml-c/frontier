from pathlib import Path

from frontier.services.document_resources import structured_document_resources


def test_structured_resources_expose_tex_assets_tables_and_links(tmp_path: Path) -> None:
    figure = tmp_path / "figure.svg"
    table = tmp_path / "table.png"
    figure.write_text("<svg />", encoding="utf-8")
    table.write_bytes(b"table")
    assets = {figure.name: figure, table.name: table}
    document = {
        "paper_id": "paper-1",
        "version": "v1",
        "status": "ready",
        "reader_source": "arxiv_html",
        "blocks": [
            {
                "id": "body",
                "anchor": "S1",
                "kind": "text",
                "page_number": 1,
                "text": "Use x squared and compare prior work.",
                "html": (
                    '<span data-reader-tex="x^2" data-reader-display="inline"></span>'
                    '<a data-reader-ref="bib.bib1">[1]</a>'
                ),
            },
            {
                "id": "figure-1",
                "anchor": "S1.F1",
                "kind": "picture",
                "page_number": 2,
                "caption": "Figure 1",
                "image_url": "/api/papers/paper-1/document/assets/figure.svg",
            },
            {
                "id": "table-1",
                "anchor": "S1.T1",
                "kind": "table",
                "page_number": 3,
                "caption": "Table 1",
                "text": "Method | Score",
                "html": "<table><tr><td>Method</td></tr></table>",
                "image_url": "/api/papers/paper-1/document/assets/table.png",
            },
            {
                "id": "bib-1",
                "anchor": "bib.bib1",
                "kind": "bibliography_item",
                "page_number": 9,
                "text": "A. Author. Prior work.",
                "html": "<span>A. Author. Prior work.</span>",
            },
        ],
    }

    result = structured_document_resources(
        document,
        asset_resolver=lambda filename: assets[filename],
    )

    assert result["formulas"] == [
        {
            "block_id": "body",
            "anchor": "S1",
            "page_number": 1,
            "id": "body:formula:0",
            "tex": "x^2",
            "display": False,
            "context": "Use x squared and compare prior work.",
        }
    ]
    assert result["figures"][0]["asset_path"] == str(figure.resolve())
    assert result["tables"][0]["text"] == "Method | Score"
    link = result["references"]["links"][0]
    assert link["target"] == {
        "block_id": "bib-1",
        "kind": "bibliography_item",
        "page_number": 9,
    }
    assert result["references"]["bibliography"][0]["text"] == "A. Author. Prior work."


def test_structured_resources_filter_kind_and_block() -> None:
    document = {
        "paper_id": "paper-1",
        "status": "ready",
        "blocks": [
            {
                "id": "formula-1",
                "kind": "formula",
                "html": '<span data-reader-tex="a+b" data-reader-display="block"></span>',
            },
            {"id": "figure-1", "kind": "picture", "caption": "Figure 1"},
        ],
    }

    result = structured_document_resources(document, kind="formula", block_id="formula-1")

    assert list(result) == ["paper_id", "version", "status", "reader_source", "formulas"]
    assert result["formulas"][0]["tex"] == "a+b"
