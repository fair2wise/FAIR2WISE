"""DoclingParser: mapping a DoclingDocument onto ParsedDocument, and the optional real conversion.

The mapping tests build a ``DoclingDocument`` by hand (no model weights, no inference) and run
``DoclingParser.document_from_docling`` over it. The conversion test needs Docling's layout and
table models and is skipped unless ``F2W_DOCLING_ARTIFACTS_PATH`` points at them (images fetch them at
build time; they are never in the repo).
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

docling_core = pytest.importorskip("docling_core")
reportlab = pytest.importorskip("reportlab")

from PIL import Image  # noqa: E402
from docling_core.types.doc import BoundingBox as DBox  # noqa: E402
from docling_core.types.doc import (
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ImageRef,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from reportlab.pdfgen import canvas  # noqa: E402

from app.modules.literature.parsed_document import document_from_dict  # noqa: E402
from app.modules.literature.parsers import PARSER_DOCLING, DoclingParser  # noqa: E402

SOURCE = "sha256:" + "ab" * 32
PAGE_W, PAGE_H = 600.0, 800.0


def _prov(page: int, box: tuple[float, float, float, float], origin: CoordOrigin = CoordOrigin.BOTTOMLEFT) -> ProvenanceItem:
    left, top, right, bottom = box
    return ProvenanceItem(page_no=page, bbox=DBox(l=left, t=top, r=right, b=bottom, coord_origin=origin), charspan=(0, 1))


def _pdf_with_figure_text(path: Path) -> Path:
    """A one-page PDF whose text layer holds two runs inside the figure box and one outside it."""
    pdf = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H), invariant=1)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(100, PAGE_H - 200, "q (nm-1)")   # inside the figure (top-left y 150..300)
    pdf.drawString(120, PAGE_H - 230, "0.5")        # inside
    pdf.drawString(100, PAGE_H - 600, "body text")  # outside
    pdf.showPage()
    pdf.save()
    return path


def _document() -> DoclingDocument:
    doc = DoclingDocument(name="paper")
    doc.add_page(page_no=1, size=Size(width=PAGE_W, height=PAGE_H))
    doc.add_title("A Study of Thin Films", prov=_prov(1, (50, 780, 550, 750)))
    doc.add_heading("1. Introduction", level=1, prov=_prov(1, (50, 700, 300, 685)))
    doc.add_text(DocItemLabel.TEXT, "Thin films matter.", prov=_prov(1, (50, 680, 550, 660)))
    doc.add_text(DocItemLabel.PAGE_FOOTER, "Page 1 of 1", prov=_prov(1, (250, 30, 350, 20)))
    doc.add_heading("1.1 Methods", level=2, prov=_prov(1, (50, 640, 300, 625)))
    doc.add_text(DocItemLabel.TEXT, "We measured everything.", prov=_prov(1, (50, 620, 550, 600)))

    image = Image.new("RGB", (400, 300), "white")
    for x in range(400):  # not blank: a usable figure has contrast
        image.putpixel((x, x % 300), (10, 10, 10))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    caption = doc.add_text(DocItemLabel.CAPTION, "Figure 1. A scattering pattern.", prov=_prov(1, (90, 560, 410, 545)))
    doc.add_picture(
        image=ImageRef.from_pil(image, dpi=144),
        caption=caption,
        prov=_prov(1, (90, 650, 290, 500)),  # BOTTOMLEFT: 200 x 150 pt, top-left y 150..300
    )

    cells = [
        TableCell(text="Peak", start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=0,
                  end_col_offset_idx=1, row_span=1, col_span=1, column_header=True,
                  bbox=DBox(l=60, t=420, r=150, b=442, coord_origin=CoordOrigin.TOPLEFT)),
        TableCell(text="q (nm^-1)", start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=1,
                  end_col_offset_idx=2, row_span=1, col_span=1, column_header=True,
                  bbox=DBox(l=150, t=420, r=240, b=442, coord_origin=CoordOrigin.TOPLEFT)),
        TableCell(text="1", start_row_offset_idx=1, end_row_offset_idx=2, start_col_offset_idx=0,
                  end_col_offset_idx=1, row_span=1, col_span=1,
                  bbox=DBox(l=60, t=442, r=150, b=464, coord_origin=CoordOrigin.TOPLEFT)),
        TableCell(text="0.31", start_row_offset_idx=1, end_row_offset_idx=2, start_col_offset_idx=1,
                  end_col_offset_idx=2, row_span=1, col_span=1,
                  bbox=DBox(l=150, t=442, r=240, b=464, coord_origin=CoordOrigin.TOPLEFT)),
    ]
    table_caption = doc.add_text(DocItemLabel.CAPTION, "Table 1. Peak positions", prov=_prov(1, (60, 360, 240, 348)))
    doc.add_table(
        data=TableData(num_rows=2, num_cols=2, table_cells=cells),
        caption=table_caption,
        prov=_prov(1, (60, 380, 240, 336)),
    )
    return doc


@pytest.fixture()
def parsed(tmp_path):
    pdf = _pdf_with_figure_text(tmp_path / "paper.pdf")
    return DoclingParser(min_figure_side=100).document_from_docling(
        _document(), pdf_path=pdf, work_dir=tmp_path / "work", source_ref=SOURCE, conversion_seconds=1.5
    )


def test_identity_and_versioned_key(parsed):
    assert parsed.parser == PARSER_DOCLING == "docling"
    assert parsed.parser_version and parsed.parser_key.startswith(f"docling-{parsed.parser_version}-")
    assert parsed.parser_config["do_ocr"] is False and parsed.parser_config["adapter_version"] == 1
    assert parsed.timings == {"parser_conversion_s": 1.5}
    assert parsed.num_pages == 1 and parsed.pages[0].width == PAGE_W and parsed.pages[0].rotation == 0


def test_headings_open_sections_with_levels_and_the_title_is_front_matter(parsed):
    assert [(s.name, s.level, s.page) for s in parsed.sections] == [("1. Introduction", 1, 1), ("1.1 Methods", 2, 1)]
    intro, methods = parsed.sections
    assert "Thin films matter." in intro.text and "Page 1 of 1" not in intro.text  # page furniture is dropped
    assert methods.text.startswith("We measured everything.")
    assert intro.bbox is not None and intro.bbox.coord_origin == "BOTTOMLEFT"
    assert [s.structural_id for s in parsed.sections] == ["section-00000", "section-00001"]
    assert "A Study of Thin Films" not in "".join(s.name for s in parsed.sections)


def test_figure_has_geometry_caption_crop_and_text_spans_from_the_pdf(parsed, tmp_path):
    (figure,) = parsed.figures
    assert figure.figure_id == "fig-p001-00" and figure.page == 1 and figure.index == 0
    assert figure.caption == "Figure 1. A scattering pattern."
    assert figure.caption_bbox is not None
    assert (figure.bbox.left, figure.bbox.top, figure.bbox.right, figure.bbox.bottom) == (90.0, 650.0, 290.0, 500.0)
    assert figure.bbox.coord_origin == "BOTTOMLEFT" and figure.bbox.units == "pt"
    assert figure.images_scale == pytest.approx(400 / 200)  # PNG pixels per figure point
    assert figure.path is not None and figure.path.read_bytes().startswith(b"\x89PNG")
    import json

    spans = json.loads(figure.spans_path.read_text(encoding="utf-8"))
    assert [s["text"] for s in spans] == ["q (nm-1)", "0.5"]  # verbatim runs inside the figure only
    assert all(s["bbox"]["origin"] == "TOPLEFT" for s in spans)


def test_table_has_shape_cells_with_their_own_origin_and_csv(parsed):
    (table,) = parsed.tables
    assert table.table_id == "tab-p001-00" and table.page == 1 and (table.n_rows, table.n_cols) == (2, 2)
    assert table.caption == "Table 1. Peak positions"
    assert [(c.row, c.col, c.text) for c in table.cells] == [(0, 0, "Peak"), (0, 1, "q (nm^-1)"), (1, 0, "1"), (1, 1, "0.31")]
    assert {c.bbox.coord_origin for c in table.cells} == {"TOPLEFT"}  # each cell keeps the origin Docling gave it
    assert table.bbox.coord_origin == "BOTTOMLEFT"
    assert table.data_path is not None and table.data_path.read_text(encoding="utf-8").splitlines() == [
        "Peak,q (nm^-1)", "1,0.31"]


def test_record_round_trip_keeps_cells_and_bboxes(parsed):
    again = document_from_dict(parsed.to_record())
    assert again.tables[0].cells == parsed.tables[0].cells
    assert again.tables[0].bbox == parsed.tables[0].bbox and again.figures[0].bbox == parsed.figures[0].bbox
    assert again.parser_key == parsed.parser_key


def test_documents_stored_before_cells_existed_still_load(parsed):
    record = parsed.to_record()
    for table in record["tables"]:
        table.pop("cells", None)
    assert document_from_dict(record).tables[0].cells == ()


def test_unusable_images_are_not_figures(tmp_path):
    doc = _document()
    parser = DoclingParser(min_figure_side=500)  # the 400 x 300 crop is below the floor
    result = parser.document_from_docling(
        doc, pdf_path=_pdf_with_figure_text(tmp_path / "p.pdf"), work_dir=tmp_path / "w", source_ref=SOURCE)
    assert result.figures == ()


def test_missing_docling_is_an_import_error_for_the_parse_service(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("docling.") or name == "docling":
            raise ImportError("no docling")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError):
        DoclingParser().parse(_pdf_with_figure_text(tmp_path / "p.pdf"), tmp_path / "w", source_ref=SOURCE)


@pytest.mark.skipif(not os.environ.get("F2W_DOCLING_ARTIFACTS_PATH"), reason="Docling model weights not available")
def test_real_conversion_of_a_generated_pdf(tmp_path):
    """Docling's own models read a text PDF: headings, body text and page geometry come through."""
    path = tmp_path / "real.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(PAGE_W, PAGE_H), invariant=1)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, PAGE_H - 100, "1. Introduction")
    pdf.setFont("Helvetica", 11)
    # Varied, paragraph-like prose: twelve identical lines make the layout model call the block a picture.
    prose = [
        "Thin films of polymer semiconductors were measured by scattering.",
        "The samples were cast from chlorobenzene solution onto silicon substrates",
        "and annealed at 150 degrees Celsius for ten minutes before measurement.",
        "Grazing incidence geometry at the incident angle of 0.12 degrees gave",
        "sufficient footprint to probe the full thickness of each specimen.",
        "Two dimensional detector images were reduced to radial and azimuthal",
        "profiles, from which crystalline coherence lengths were then estimated.",
        "Edge-on and face-on populations differ markedly between the two blends,",
        "suggesting that the processing history controls the molecular orientation.",
        "We discuss how these orientation distributions relate to charge transport,",
        "which is known to depend strongly on the in-plane packing of backbones.",
        "Further work will extend the analysis to additional casting conditions.",
    ]
    for i, line in enumerate(prose):
        pdf.drawString(72, PAGE_H - 130 - 14 * i, line)
    pdf.showPage()
    pdf.save()
    parsed = DoclingParser().parse(path, tmp_path / "work", source_ref=SOURCE)
    assert parsed.num_pages == 1 and parsed.pages[0].width == PAGE_W
    assert "Thin films of polymer semiconductors" in parsed.markdown
    assert parsed.parser == "docling" and parsed.parser_version
