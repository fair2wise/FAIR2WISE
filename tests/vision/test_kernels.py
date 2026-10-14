"""T09 kernels: coordinate conversion, units, panels, q-axis, scale bar, types (characterization)."""

import numpy as np
import pytest

from app.modules.vision.kernels.barmeasure import measure_scale_bar_result
from app.modules.vision.kernels.geometry import FigureFrame, span_to_pixels, spans_inside
from app.modules.vision.kernels.panels import (
    label_panels_from_spans, panel_labels, reconcile_panels, segment_panels,
)
from app.modules.vision.kernels.qaxis import read_q_axis
from app.modules.vision.kernels.scalebar import caption_bar_lengths, pair_lengths_to_panels, read_panel_scale
from app.modules.vision.kernels.types import PanelBox, TextSpan
from app.modules.vision.kernels.units import (
    caption_inverse_unit, document_inverse_unit, fold_inverse_length, fold_length, iter_lengths, normalise_unit,
)


def test_frame_bottom_left_and_top_left_agree():
    # Same physical figure, 100pt wide / 50pt tall, on a 200pt-tall page.
    bl = FigureFrame((10, 150, 110, 100), 2.0, "BOTTOMLEFT")   # y up; top=150 is the larger number
    tl = FigureFrame((10, 50, 110, 100), 2.0, "TOPLEFT")        # y down
    assert bl.to_pixels(10, 150) == (0.0, 0.0) == tl.to_pixels(10, 50)
    assert bl.to_pixels(110, 100) == (200.0, 100.0) == tl.to_pixels(110, 100)
    # a point near the visual top of the figure is near pixel y=0 in both conventions
    assert bl.to_pixels(60, 140)[1] == 20.0 and tl.to_pixels(60, 60)[1] == 20.0


def test_frame_round_trip_and_degenerate():
    f = FigureFrame((10, 150, 110, 100), 2.0, "BOTTOMLEFT")
    assert f.to_points(*f.to_pixels(33.0, 120.0)) == pytest.approx((33.0, 120.0))
    with pytest.raises(ValueError):
        FigureFrame((1, 1, 1, 5), 1.0)
    with pytest.raises(ValueError):
        FigureFrame((0, 0, 1, 5), 0.0)


def test_span_filter_uses_centre_and_skips_unboxed():
    f = FigureFrame((0, 0, 100, 100), 1.0, "TOPLEFT")
    inside = TextSpan("a", (10, 10, 20, 20))
    caption = TextSpan("cap", (10, 120, 90, 130))
    nobox = TextSpan("x")
    assert span_to_pixels(nobox, f) is None
    assert spans_inside([inside, caption, nobox], f, (0, 0, 100, 100)) == [inside]


def test_units_fold_refuses_unknown():
    assert normalise_unit("Å -1") == "a-1" and normalise_unit("nm⁻¹") == "nm-1"
    assert fold_inverse_length(1, "Å^-1") == 10.0
    assert fold_inverse_length(1, "furlong") is None and fold_length(1, "furlong") is None
    assert fold_length(2, "µm") == 2000.0 == fold_length(2, "μm")
    assert iter_lengths("bar is 200 nm and 2 um") == [200.0, 2000.0]


def test_caption_unit_ambiguity_is_withheld():
    assert caption_inverse_unit("q (nm^-1)") == "nm-1"
    assert caption_inverse_unit("q (Å^-1) and (nm^-1)") == ""
    assert caption_inverse_unit("q^2 (nm^-2)") == ""
    assert document_inverse_unit(["peaks at 0.3 Å^-1", "elsewhere nm^-1"]) == ""
    assert document_inverse_unit(["peaks at 0.3 Å^-1"]) == "a-1"


def test_panel_labels_and_scale_pairing():
    cap = "Figure 2. (a) TEM image, scale bar 200 nm. (b) SEM image, scale bar 2 um."
    assert panel_labels(cap) == {"a", "b"}
    assert [nm for nm, _ in caption_bar_lengths(cap)] == [200.0, 2000.0]
    assert pair_lengths_to_panels(cap) == {"a": 200.0, "b": 2000.0}
    # count mismatch -> undecidable, empty (not 'no scale')
    assert pair_lengths_to_panels("(a) x (b) y (c) z. Scale bar 5 nm.") == {}


def _two_panel_image():
    img = np.full((100, 210), 255, dtype=np.uint8)
    img[5:95, 5:100] = 60
    img[5:95, 110:205] = 90
    return img


def test_segment_panels_finds_gutter_and_reconcile_downgrades_on_mismatch():
    img = _two_panel_image()
    boxes = segment_panels(img)
    assert len(boxes) == 2
    lab = reconcile_panels(boxes, {"a", "b"}, img.shape)
    assert [b.label for b in lab] == ["a", "b"]
    whole = reconcile_panels(boxes, {"a", "b", "c"}, img.shape)
    assert len(whole) == 1 and whole[0].method == "whole_figure"


def test_label_panels_from_spans_by_position():
    f = FigureFrame((0, 0, 210, 100), 1.0, "TOPLEFT")
    boxes = [PanelBox(5, 5, 100, 95), PanelBox(110, 5, 205, 95)]
    spans = [TextSpan("b", (115, 8, 121, 18)), TextSpan("a", (10, 8, 16, 18))]
    out = label_panels_from_spans(boxes, spans, f, allowed="ab")
    assert [b.label for b in out] == ["a", "b"]


def _axis(values, y=90.0, x0=10.0, step=40.0, unit=None):
    spans = [TextSpan(str(v), (x0 + i * step, y, x0 + i * step + 8, y + 8)) for i, v in enumerate(values)]
    if unit:
        spans.append(TextSpan(unit, (60, y + 14, 120, y + 24)))
    return spans


def test_q_axis_reads_text_layer_unit_and_converts_angstrom():
    nm = read_q_axis(_axis([0.5, 1.0, 1.5, 2.0], unit="q (nm⁻¹)"))
    assert nm.determined and nm.q_min_nm_inv == pytest.approx(0.5) and nm.q_max_nm_inv == pytest.approx(2.0)
    ang = read_q_axis(_axis([0.05, 0.1, 0.15, 0.2], unit="q (Å^-1)"))
    assert ang.determined and ang.q_max_nm_inv == pytest.approx(2.0)


def test_q_axis_without_unit_is_not_determined():
    got = read_q_axis(_axis([0.5, 1.0, 1.5, 2.0]))
    assert not got.determined and got.reason


def test_q_axis_ocr_unit_never_believed():
    spans = [TextSpan(s.text, s.bbox, s.page, "ocr") for s in _axis([0.5, 1.0, 1.5, 2.0], unit="q (nm⁻¹)")]
    assert not read_q_axis(spans).determined


def test_bar_measure_and_scale_reading():
    img = np.zeros((100, 100), dtype=np.uint8)
    img[90, 60:90] = 255   # 30 px bar bottom-right
    got = measure_scale_bar_result(img)
    assert got.found and got.length_px == pytest.approx(30, abs=1.5)
    panel = PanelBox(0, 0, 100, 100)
    scale = read_panel_scale(img, panel, caption_length_nm=300.0)
    assert scale.determined and scale.nm_per_px == pytest.approx(10.0, rel=0.06)
    assert scale.length_source == "caption"
    assert read_panel_scale(img, panel).determined is False   # bar without a length: not a zero
    assert measure_scale_bar_result(np.full((40, 40), 255, dtype=np.uint8)).reason == "region_mostly_saturated"
    with pytest.raises(ValueError):
        measure_scale_bar_result(np.zeros((4, 4, 3)))


def test_ambiguous_bars_refused():
    img = np.zeros((100, 100), dtype=np.uint8)
    img[90, 55:85] = 255
    img[10, 10:38] = 255   # comparable bar, another quadrant
    assert not read_panel_scale(img, PanelBox(0, 0, 100, 100), caption_length_nm=100.0).determined


def test_kernel_import_closure_has_no_experimental_modules():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app/modules/vision/kernels"
    allowed = {"__future__", "re", "math", "dataclasses", "typing", "collections", "numpy"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert set(names) <= allowed, (path.name, names)
