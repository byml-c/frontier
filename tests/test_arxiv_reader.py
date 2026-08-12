from pathlib import Path

import pymupdf

from frontier.services.arxiv_reader import convert_arxiv_html_content

ARXIV_HTML = """
<html><body><article class="ltx_document">
  <h1 class="ltx_title_document">A source-faithful paper</h1>
  <section id="S1">
    <h2 id="S1">1 Introduction</h2>
    <p class="ltx_p">We optimize <math alttext="x^2"><semantics><mrow><msup><mi>x</mi><mn>2</mn></msup></mrow><annotation encoding="application/x-tex">x^2</annotation></semantics></math>; see <a href="#A1">Appendix A</a>.</p>
    <figure id="S1.F1" class="ltx_figure">
      <img src="paper-v1/figure.png" alt="Refer to caption" />
      <figcaption>Figure 1: One original figure.</figcaption>
    </figure>
    <figure id="S1.F2" class="ltx_figure">
      <object type="image/svg+xml" data="paper-v1/vector-figure.svg"></object>
      <figcaption>Figure 2: One vector figure.</figcaption>
    </figure>
    <table id="S1.E1" class="ltx_equation"><tr><td><math display="block"><semantics><mrow><mi>y</mi></mrow><annotation encoding="application/x-tex">y = x^2</annotation></semantics></math></td></tr></table>
  </section>
  <section id="bib" class="ltx_bibliography"><h2 class="ltx_title ltx_title_bibliography">References</h2><ul><li id="bib.bib1" class="ltx_bibitem"><span class="ltx_tag ltx_bib_key">[1]</span><span class="ltx_bibblock">A. Author (2026). A complete reference.</span></li></ul></section>
  <section id="A1"><h2 id="A1">Appendix A Details</h2><p class="ltx_p">Additional evidence.</p></section>
</article></body></html>
"""


def test_arxiv_html_preserves_semantics_without_duplicate_captions(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Introduction We optimize x squared. Appendix A Details")
    document.save(pdf)
    document.close()

    def fetch_asset(_url: str, target: Path) -> Path:
        target.write_bytes(b"original-image")
        return target

    payload = convert_arxiv_html_content(
        ARXIV_HTML,
        base_url="https://arxiv.org/html/1234.56789",
        pdf_path=pdf,
        paper_id="paper-1",
        version="v1",
        root=tmp_path / "reader",
        schema_version=3,
        fetch_asset=fetch_asset,
    )
    blocks = payload["blocks"]

    assert payload["reader_source"] == "arxiv_html"
    assert not [block for block in blocks if block["kind"] == "caption"]
    pictures = [block for block in blocks if block["kind"] == "picture"]
    assert [picture["caption"] for picture in pictures] == [
        "Figure 1: One original figure.",
        "Figure 2: One vector figure.",
    ]
    assert pictures[0]["image_url"].endswith(".png")
    assert pictures[1]["image_url"].endswith(".svg")
    paragraph = next(block for block in blocks if "We optimize" in block.get("text", ""))
    assert 'data-reader-tex="x^2"' in paragraph["html"]
    assert 'data-reader-ref="A1"' in paragraph["html"]
    formula = next(block for block in blocks if block["kind"] == "formula")
    assert 'data-reader-tex="y = x^2"' in formula["html"]
    assert any(block.get("anchor") == "A1" for block in blocks)
    assert any(block["kind"] == "bibliography_header" for block in blocks)
    reference = next(block for block in blocks if block["kind"] == "bibliography_item")
    assert reference["anchor"] == "bib.bib1"
    assert "A complete reference" in reference["text"]
