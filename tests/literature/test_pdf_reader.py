"""pdf_reader: page text, metadata, geometry, spans, image boxes and rendering over pypdfium2."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")
reportlab = pytest.importorskip("reportlab")

from PIL import Image  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.modules.literature.pdf_reader import PdfDocument, PdfSpan, _clean_text, open_pdf  # noqa: E402

W, H = 612.0, 792.0


def _make(path: Path, *, rotation: int = 0) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=(W, H), invariant=1)
    pdf.setTitle("Reader fixture")
    pdf.setAuthor("A. Writer")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, H - 100, "Alpha line")
    pdf.drawString(72, H - 120, "Beta line")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(300, H - 400, "q (nm-1)")
    pdf.setFillColorRGB(0.2, 0.2, 0.2)
    pdf.rect(100, H - 340, 150, 60, stroke=0, fill=1)
    image = Image.new("RGB", (60, 40), (90, 90, 90))
    pdf.drawImage(ImageReader(image), 400, H - 330, width=120, height=80)
    if rotation:
        pdf.setPageRotation(rotation)
    pdf.showPage()
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, H - 100, "Second page")
    pdf.showPage()
    pdf.save()
    return path


@pytest.fixture()
def pdf_path(tmp_path):
    return _make(tmp_path / "reader.pdf")


def test_open_from_path_and_bytes_and_context_manager(pdf_path):
    with open_pdf(pdf_path) as doc:
        assert isinstance(doc, PdfDocument) and len(doc) == doc.page_count == 2
    with open_pdf(str(pdf_path)) as doc:
        assert len(doc) == 2
    with open_pdf(pdf_path.read_bytes()) as doc:
        assert len(doc) == 2


def test_errors_are_specific(tmp_path, pdf_path):
    with pytest.raises(FileNotFoundError):
        open_pdf(tmp_path / "missing.pdf")
    with open_pdf(pdf_path) as doc:
        with pytest.raises(IndexError):
            doc[5]
        with pytest.raises(ValueError, match="only 'text'"):
            doc[0].get_text("dict")
        assert doc[-1].number == 1


def test_page_text_has_newlines_and_ends_with_one(pdf_path):
    with open_pdf(pdf_path) as doc:
        text = doc[0].get_text("text")
        assert "Alpha line" in text and "Beta line" in text and "\r" not in text
        assert text.endswith("\n")
        assert doc[1].get_text() == "Second page\n"
        assert [p.number for p in doc] == [0, 1]


def test_text_cleaning_drops_pdfium_hyphen_marks_and_normalises_breaks():
    assert _clean_text("poly￾\r\nmer\rstart") == "poly\nmer\nstart"
    assert "￾" not in _clean_text("a￾b")


def test_metadata_uses_fitz_style_keys(pdf_path):
    with open_pdf(pdf_path) as doc:
        meta = doc.metadata
    assert set(meta) >= {"format", "title", "author", "subject", "keywords", "creator", "producer",
                         "creationDate", "modDate"}
    assert meta["title"] == "Reader fixture" and meta["author"] == "A. Writer"
    assert meta["keywords"] == "" and meta["format"].startswith("PDF ")


def test_page_geometry_and_rotation(tmp_path):
    with open_pdf(_make(tmp_path / "a.pdf")) as doc:
        page = doc[0]
        assert (page.width, page.height, page.rotation) == (pytest.approx(W), pytest.approx(H), 0)
    with open_pdf(_make(tmp_path / "b.pdf", rotation=90)) as doc:
        page = doc[0]
        # width/height are the displayed (rotation applied) frame; reportlab swaps the MediaBox for a
        # rotated page, so the displayed size here is still 612 x 792.
        assert page.rotation == 90 and {round(page.width), round(page.height)} == {612, 792}


def test_spans_are_verbatim_runs_boxed_in_top_left_points(pdf_path):
    with open_pdf(pdf_path) as doc:
        spans = doc[0].get_spans()
    assert all(isinstance(s, PdfSpan) for s in spans)
    by_text = {s.text.strip(): s for s in spans}
    assert {"Alpha line", "Beta line", "q (nm-1)"} <= set(by_text)
    alpha, beta, axis = by_text["Alpha line"], by_text["Beta line"], by_text["q (nm-1)"]
    assert alpha.bbox[0] == pytest.approx(72, abs=1.0) and alpha.size == pytest.approx(12.0)
    assert alpha.bbox[1] < beta.bbox[1] < axis.bbox[1]  # top-left origin: y grows downwards
    assert axis.bbox[1] == pytest.approx(400 - 9, abs=4.0) and axis.size == pytest.approx(9.0)


def test_image_boxes_are_top_left_points(pdf_path):
    with open_pdf(pdf_path) as doc:
        boxes = doc[0].image_boxes()
        assert doc[1].image_boxes() == []
    assert len(boxes) == 1
    assert boxes[0] == pytest.approx((400.0, 250.0, 520.0, 330.0), abs=1.0)  # drawn 120 x 80 pt at y 462..542 (bottom-left)


def test_render_gray_shape_scale_and_content(pdf_path):
    with open_pdf(pdf_path) as doc:
        region = (100.0, 280.0, 250.0, 340.0)  # the filled rectangle, top-left points
        array = doc[0].render_gray(region, 2.0)
        whole = doc[0].render_gray(None, 1.0)
    assert array.dtype.name == "uint8" and array.shape == (120, 300)
    assert array.mean() < 90  # mostly the dark-gray rectangle
    assert whole.shape == (792, 612) and whole.mean() > 200  # mostly white page


def test_render_png_returns_png_bytes(pdf_path):
    with open_pdf(pdf_path) as doc:
        png = doc[0].render_png((100.0, 280.0, 250.0, 340.0), 2.0)
    assert png.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (300, 120) and image.mode == "RGB"


def test_close_is_idempotent(pdf_path):
    doc = open_pdf(pdf_path)
    doc[0].get_text()
    doc.close()
    doc.close()
