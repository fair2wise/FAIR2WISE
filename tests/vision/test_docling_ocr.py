"""T30 Docling-backed figure OCR: the adapter glue runs against an injected fake converter (no weights),
selection and degradation are checked without Docling, and a real-weights test skips when they are absent."""

import hashlib
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
from vision_fakes import parsed, raster_figure_pdf

from app.modules.literature.parsers import DOCLING_ARTIFACTS_ENV
import app.modules.vision.docling_ocr as docling_ocr
from app.modules.vision.docling_ocr import DoclingOcrEngine, weights_available
from app.modules.vision.figure_extraction import extract_figures
from app.modules.vision.kernels.geometry import FigureFrame
from app.modules.vision.ocr import (
    OCR_ENGINE_ENV, Detection, RapidOcrEngine, default_engine, read_bar_labels, read_figure_text,
)
from app.modules.vision.results import VisionUnavailable


def cell(text, x0, y0, x1, y1, conf, origin="BOTTOMLEFT"):
    """A Docling text cell: axis-aligned rect, corners counter-clockwise from bottom-left."""
    rect = NS(r_x0=x0, r_y0=y0, r_x1=x1, r_y1=y0, r_x2=x1, r_y2=y1, r_x3=x0, r_y3=y1, coord_origin=origin)
    return NS(text=text, rect=rect, confidence=conf)


class FakeConverter:
    def __init__(self, cells=(), size=(200.0, 100.0), error=None):
        self.cells, self.size, self.error, self.sources = list(cells), size, error, []

    def convert(self, source):
        self.sources.append(source)
        if self.error:
            raise self.error
        page = NS(size=NS(width=self.size[0], height=self.size[1]), cells=self.cells)
        return NS(pages=[page])


def engine(conv):
    return DoclingOcrEngine(converter=conv, source_factory=lambda png: png)


IMG = np.full((100, 200), 255, dtype=np.uint8)


def test_boxes_flip_to_top_left_and_confidence_is_kept():
    conv = FakeConverter([cell("1.0", 20, 10, 60, 30, 0.93)])           # bottom-left origin, page 100 high
    (det,) = engine(conv).detect(IMG)
    assert det == Detection("1.0", (20.0, 70.0, 60.0, 90.0), 0.93)
    assert conv.sources[0][:8] == b"\x89PNG\r\n\x1a\n"                   # the figure goes in as a PNG, in memory


def test_top_left_origin_is_kept_and_page_size_scales_to_image_pixels():
    conv = FakeConverter([cell("0.5", 10, 20, 30, 30, 0.9, origin="TOPLEFT")], size=(100.0, 50.0))   # half scale
    (det,) = engine(conv).detect(IMG)
    assert det.box_px == pytest.approx((20.0, 40.0, 60.0, 60.0))


def test_real_docling_core_rectangles_map_the_same_way():
    page = pytest.importorskip("docling_core.types.doc.page")
    from docling_core.types.doc import CoordOrigin

    rect = page.BoundingRectangle(r_x0=20, r_y0=10, r_x1=60, r_y1=10, r_x2=60, r_y2=30, r_x3=20, r_y3=30,
                                  coord_origin=CoordOrigin.BOTTOMLEFT)
    conv = FakeConverter([NS(text="q", rect=rect, confidence=0.8)])
    assert engine(conv).detect(IMG)[0].box_px == (20.0, 70.0, 60.0, 90.0)


def test_unreadable_cells_are_dropped_not_guessed():
    conv = FakeConverter([cell("   ", 0, 0, 5, 5, 0.9), cell("x", 0, 0, 5, 5, None), cell("ok", 0, 0, 5, 5, 0.7)])
    assert [d.text for d in engine(conv).detect(IMG)] == ["ok"]


def test_converter_failure_is_retryable_unavailable_not_a_crash():
    with pytest.raises(VisionUnavailable) as err:
        engine(FakeConverter(error=RuntimeError("boom"))).detect(IMG)
    assert err.value.capability == "OCR engine" and "boom" in str(err.value)


def test_read_figure_text_filters_confidence_and_maps_to_page_points():
    conv = FakeConverter([cell("0.5", 20, 10, 60, 30, 0.95), cell("1.0", 80, 10, 120, 30, 0.2),
                          cell("200 nm", 130, 10, 190, 30, 0.9)])
    frame = FigureFrame((100.0, 200.0, 200.0, 250.0), 2.0, "TOPLEFT", size_px=(200, 100))
    reading = read_figure_text(IMG, frame, engine=engine(conv))
    assert reading.engine == "docling-rapidocr" and [s.text for s in reading.spans] == ["0.5", "200 nm"]
    assert all(s.is_ocr for s in reading.spans) and reading.min_confidence == 0.9
    assert [s.text for s in read_bar_labels(IMG, frame, engine=engine(conv)).spans] == ["200 nm"]


def test_analyze_figure_uses_docling_engine_spans_like_any_engine():
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    cells = [cell(t, 40 + i * 90, 65, 60 + i * 90, 80, 0.97) for i, t in enumerate(["0.5", "1.0", "1.5", "2.0"])]
    conv = FakeConverter(cells, size=(1000.0, 480.0))                    # the figure render is 1000x480 px
    fig = extract_figures(parsed(cap), raster_figure_pdf(cap), work_id="10.1000/test", use_ocr=True,
                          ocr_engine=engine(conv)).figures[0]
    assert fig.text_status == "ocr_fallback" and fig.ocr["engine"] == "docling-rapidocr"
    assert fig.ocr["n_spans"] == 4


def test_failing_engine_degrades_to_ocr_unavailable():
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    fig = extract_figures(parsed(cap), raster_figure_pdf(cap), work_id="10.1000/test", use_ocr=True,
                          ocr_engine=engine(FakeConverter(error=OSError("no weights")))).figures[0]
    assert fig.text_status == "ocr_unavailable" and fig.capability_error["retryable"] is True


# --- availability and selection ---------------------------------------------------------------------------

def make_weights(root):
    folder = root / "RapidOcr"
    folder.mkdir(parents=True)
    for name in ("PP-OCRv6_det_small.pth", "ch_ppocr_mobile_v2.0_cls_mobile.pth", "PP-OCRv6_rec_small.pth"):
        (folder / name).write_bytes(b"x")


def test_weights_available_needs_all_three_checkpoints(tmp_path):
    assert not weights_available(tmp_path)
    make_weights(tmp_path)
    assert weights_available(tmp_path)
    (tmp_path / "RapidOcr" / "PP-OCRv6_rec_small.pth").unlink()
    assert not weights_available(tmp_path)


def test_engine_without_weights_is_unavailable(tmp_path):
    with pytest.raises(VisionUnavailable, match="weights not found"):
        DoclingOcrEngine(tmp_path)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(OCR_ENGINE_ENV, raising=False)
    monkeypatch.delenv(DOCLING_ARTIFACTS_ENV, raising=False)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)       # as in the images: not installed
    return monkeypatch


def test_default_without_weights_is_exactly_the_old_behaviour(clean_env):
    with pytest.raises(VisionUnavailable, match="rapidocr-onnxruntime"):
        default_engine()


def test_auto_with_weights_selects_docling_and_none_disables(clean_env, tmp_path):
    make_weights(tmp_path)
    clean_env.setenv(DOCLING_ARTIFACTS_ENV, str(tmp_path))
    built = []
    clean_env.setattr(docling_ocr, "_build_converter", lambda root: built.append(root) or FakeConverter())
    assert isinstance(default_engine(), DoclingOcrEngine) and built == [tmp_path]
    clean_env.setenv(OCR_ENGINE_ENV, "rapidocr")
    with pytest.raises(VisionUnavailable, match="rapidocr-onnxruntime"):
        default_engine()
    clean_env.setenv(OCR_ENGINE_ENV, "none")
    with pytest.raises(VisionUnavailable, match="disabled"):
        default_engine()
    clean_env.setenv(OCR_ENGINE_ENV, "bogus")
    with pytest.raises(VisionUnavailable, match="unknown"):
        default_engine()


def test_forcing_docling_without_weights_is_unavailable_not_a_fallback(clean_env, tmp_path):
    clean_env.setenv(OCR_ENGINE_ENV, "docling")
    clean_env.setenv(DOCLING_ARTIFACTS_ENV, str(tmp_path))
    with pytest.raises(VisionUnavailable, match="weights not found"):
        default_engine()
    assert RapidOcrEngine.name == "rapidocr"


def test_broken_model_load_is_unavailable(clean_env, tmp_path):
    make_weights(tmp_path)

    def boom(root):
        raise FileNotFoundError("layout weights missing")

    clean_env.setattr(docling_ocr, "_build_converter", boom)
    with pytest.raises(VisionUnavailable, match="could not start"):
        DoclingOcrEngine(tmp_path)


def test_importing_the_engine_module_does_not_import_docling():
    code = ("import sys, app.modules.vision.docling_ocr; "
            "sys.exit(any(m == 'docling' or m.startswith('docling.') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-I", "-c", code], check=False).returncode == 0


# --- fetch script verification ------------------------------------------------------------------------------

def test_fetch_script_verifies_checkpoints_against_the_registry(tmp_path):
    sys.path.insert(0, "scripts")
    try:
        import fetch_docling_models as fetch
    finally:
        sys.path.remove("scripts")
    (tmp_path / "a.pth").write_bytes(b"weights")
    good = hashlib.sha256(b"weights").hexdigest()
    registry = {"torch": {"det": {"a": {"model_dir": "https://h/x/a.pth", "SHA256": good.upper()}}}}
    assert fetch.verify_checkpoints(tmp_path, registry) == [tmp_path / "a.pth"]
    registry["torch"]["det"]["a"]["SHA256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        fetch.verify_checkpoints(tmp_path, registry)
    with pytest.raises(ValueError, match="not listed"):
        fetch.verify_checkpoints(tmp_path, {})


# --- real Docling OCR (needs the weights fetched at image build) ----------------------------------------------

@pytest.mark.skipif(not weights_available(), reason="Docling OCR model weights not available "
                                                    f"(RapidOcr/ under ${DOCLING_ARTIFACTS_ENV})")
def test_real_docling_ocr_reads_axis_labels_of_a_synthetic_figure():
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:   # pragma: no cover - very old Pillow
        font = ImageFont.load_default()
    img = Image.new("L", (640, 300), 255)
    draw = ImageDraw.Draw(img)
    labels = {"0.5": (60, 230), "1.0": (200, 230), "1.5": (340, 230), "2.0": (480, 230)}
    for text, xy in labels.items():
        draw.text(xy, text, fill=0, font=font)
    draw.text((200, 20), "q (nm-1)", fill=0, font=font)
    found = DoclingOcrEngine().detect(np.asarray(img))
    by_text = {d.text.strip(): d for d in found}
    for text, (x, y) in labels.items():
        assert text in by_text, (text, sorted(by_text))
        x0, y0, _, _ = by_text[text].box_px
        assert abs(x0 - x) < 15 and abs(y0 - y) < 15
