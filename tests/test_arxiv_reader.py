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

CAPTION_ONLY_FIGURE_HTML = """
<html><body><article class="ltx_document">
  <h1 class="ltx_title_document">A paper with a detached teaser</h1>
  <div id="p1" class="ltx_para ltx_noindent">
    <p class="ltx_p"><span class="ltx_inline-block">
      <img src="2608.11739v1/teaser.png" alt="[Uncaptioned image]" />
    </span></p>
  </div>
  <figure id="S0.F1" class="ltx_figure">
    <figcaption>Figure 1: open the drawer <math display="inline"><semantics><mo>→</mo><annotation encoding="application/x-tex">\\rightarrow</annotation></semantics></math> close it.</figcaption>
  </figure>
</article></body></html>
"""

COMPOSITE_FIGURE_HTML = """
<html><body><article class="ltx_document">
  <h1 class="ltx_title_document">Composite figures</h1>
  <figure id="S1.F2" class="ltx_figure">
    <div class="ltx_flex_figure">
      <div class="ltx_flex_cell"><img src="paper-v1/figure-2-left.png" /></div>
      <div class="ltx_flex_cell"><img src="paper-v1/figure-2-right.png" /></div>
    </div>
    <figcaption>Fig. 2: Two coordinated panels.</figcaption>
  </figure>
  <figure id="S4.F4" class="ltx_figure">
    <div class="ltx_transformed_outer"><span class="ltx_transformed_inner">
      <svg class="ltx_picture" viewBox="0 0 200 80">
        <g><foreignObject width="90" height="50">
          <span class="ltx_foreignobject_content">
            <img src="paper-v1/sequence-frame.jpg" /><b>SWITCH</b>
          </span>
        </foreignObject></g>
      </svg>
    </span></div>
    <figcaption>Fig. 4: An inline SVG sequence.</figcaption>
  </figure>
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


def test_arxiv_html_attaches_a_preceding_textless_image_to_its_caption(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "paper.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf)
    document.close()
    fetched: list[str] = []

    def fetch_asset(url: str, target: Path) -> Path:
        fetched.append(url)
        target.write_bytes(b"teaser")
        return target

    payload = convert_arxiv_html_content(
        CAPTION_ONLY_FIGURE_HTML,
        base_url="https://arxiv.org/html/2608.11739v1",
        pdf_path=pdf,
        paper_id="paper-with-teaser",
        version="v1",
        root=tmp_path / "reader",
        schema_version=7,
        fetch_asset=fetch_asset,
    )

    pictures = [block for block in payload["blocks"] if block["kind"] == "picture"]
    assert len(pictures) == 1
    assert pictures[0]["caption"].startswith("Figure 1:")
    assert 'data-reader-tex="\\rightarrow"' in pictures[0]["caption_html"]
    assert pictures[0]["image_url"].endswith(".png")
    assert fetched == ["https://arxiv.org/html/2608.11739v1/teaser.png"]


def test_arxiv_html_preserves_multi_panel_and_inline_svg_figures(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf)
    document.close()
    fetched: list[str] = []

    def fetch_asset(url: str, target: Path) -> Path:
        fetched.append(url)
        target.write_bytes(b"figure-panel")
        return target

    payload = convert_arxiv_html_content(
        COMPOSITE_FIGURE_HTML,
        base_url="https://arxiv.org/html/2608.13362v1",
        pdf_path=pdf,
        paper_id="nestdex",
        version="v1",
        root=tmp_path / "reader",
        schema_version=7,
        fetch_asset=fetch_asset,
    )

    figure_two, figure_four = [
        block for block in payload["blocks"] if block["kind"] == "picture"
    ]
    assert "image_url" not in figure_two
    assert len(figure_two["image_urls"]) == 2
    assert figure_two["html"].count("/api/papers/nestdex/document/assets/") == 2
    assert "figure-2-left.png" not in figure_two["html"]
    assert "figure-2-right.png" not in figure_two["html"]
    assert "image_url" not in figure_four
    assert len(figure_four["image_urls"]) == 1
    assert "<svg" in figure_four["html"]
    assert "foreignobject" in figure_four["html"].lower()
    assert "SWITCH" in figure_four["html"]
    assert "sequence-frame.jpg" not in figure_four["html"]
    assert fetched == [
        "https://arxiv.org/html/paper-v1/figure-2-left.png",
        "https://arxiv.org/html/paper-v1/figure-2-right.png",
        "https://arxiv.org/html/paper-v1/sequence-frame.jpg",
    ]
