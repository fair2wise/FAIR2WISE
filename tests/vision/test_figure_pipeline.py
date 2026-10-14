"""T09 source-text-first figure pipeline on synthetic PDFs (multi-panel, origins, units, scale, no text layer)."""

import json

import pytest
from vision_fakes import (
    CAPTION, FIG, PAGE_H, PANEL_A, PANEL_B, SHA, bl_bbox, multi_panel_pdf, parsed, raster_figure_pdf, tl_bbox,
)

from app.modules.vision.figure_extraction import extract_figures
from app.modules.vision.kernels.types import TextSpan
from app.modules.vision.ocr import Detection, OcrEngine
from app.modules.vision.results import VisionUnavailable


def run(pdf, doc, **kw):
    return extract_figures(doc, pdf, work_id="10.1000/test", **kw).figures[0]


def panel(fig, label):
    return next(p for p in fig.panels if p.label == label)


@pytest.mark.parametrize("make_bbox", [bl_bbox, tl_bbox], ids=["bottomleft", "topleft"])
def test_multi_panel_geometry_is_origin_independent(make_bbox):
    fig = run(multi_panel_pdf(CAPTION), parsed(CAPTION, bbox=make_bbox(FIG)))
    assert [p.label for p in fig.panels] == ["a", "b"]
    assert {p.label_source for p in fig.panels} == {"text_layer"}
    a, b = panel(fig, "a"), panel(fig, "b")
    for got, want in ((a.bbox, PANEL_A), (b.bbox, PANEL_B)):
        assert got == pytest.approx(want, abs=1.5)
    g = fig.geometry
    assert g["page"]["height"] == PAGE_H and g["bbox_origin"] == "top-left" and g["bbox_units"] == "pt"
    assert g["source_bbox_origin"] == make_bbox(FIG).coord_origin
    assert g["render_frame"]["scale_px_per_pt"] == 2.0 and g["render_frame"]["scale_source"] == "parser"
    assert fig.locator.bbox == pytest.approx(FIG, abs=0.1)
    assert fig.locator.figure == fig.figure_id and fig.locator.page == 1
    assert a.locator.panel == "a" and a.locator.bbox_origin == "top-left" and a.locator.bbox_units == "pt"


def test_per_panel_q_ranges_do_not_mix_and_angstrom_is_converted():
    fig = run(multi_panel_pdf(CAPTION), parsed(CAPTION, bbox=bl_bbox(FIG)))
    qa, qb = panel(fig, "a").qrange, panel(fig, "b").qrange
    assert qa.reported and qa.reading.q_min_nm_inv == pytest.approx(0.5)
    assert qa.reading.q_max_nm_inv == pytest.approx(2.0)
    assert qa.reading.unit_source == "text_layer" and qa.axis_identified_by == "axis_title"
    assert qb.reported and qb.reading.q_max_nm_inv == pytest.approx(2.0)   # 0.20 A^-1 -> 2.0 nm^-1
    assert qb.reading.q_min_nm_inv == pytest.approx(0.5)


def test_scale_bar_reported_only_where_measured_and_lengths_assigned_by_panel():
    fig = run(multi_panel_pdf(CAPTION), parsed(CAPTION, bbox=bl_bbox(FIG)))
    sa, sb = panel(fig, "a").scale, panel(fig, "b").scale
    assert sa.reported and sa.reading.length_source == "caption"
    assert sa.reading.nm_per_px == pytest.approx(200.0 / 60.0, rel=0.08)   # 30 pt bar at 2 px/pt, 200 nm
    assert not sb.reported and sb.withheld_reason == "bar_too_short_for_panel"   # white panel letter, not a bar


def test_caption_unit_used_when_panel_states_none_and_ambiguous_units_withheld():
    cap = "Figure 1. (a) 2D GIWAXS pattern, q in A^-1. (b) 2D GIWAXS pattern."
    pdf = multi_panel_pdf(cap, title_a="q", title_b="q", ticks_a=("0.05", "0.10", "0.15", "0.20"))
    fig = run(pdf, parsed(cap, bbox=bl_bbox(FIG)))
    qa = panel(fig, "a").qrange
    assert qa.reported and qa.reading.unit_source == "caption" and qa.reading.q_max_nm_inv == pytest.approx(2.0)

    both = "Figure 1. (a) 2D GIWAXS pattern, q in A^-1. (b) same data, q in nm^-1."
    fig = run(multi_panel_pdf(both, title_a="q", title_b="q"), parsed(both, bbox=bl_bbox(FIG)))
    for p in fig.panels:
        assert not p.qrange.reported and p.qrange.withheld_reason == "caption_states_conflicting_inverse_units"


def test_unit_missing_everywhere_is_withheld_not_defaulted():
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    fig = run(multi_panel_pdf(cap, title_a="q", title_b="q"), parsed(cap, bbox=bl_bbox(FIG)))
    assert all(not p.qrange.reported and p.qrange.withheld_reason == "no_unit" for p in fig.panels)


def test_axis_not_identified_as_q_is_withheld():
    cap = "Figure 1. (a) Temperature series. (b) Temperature series."
    fig = run(multi_panel_pdf(cap, title_a="T (nm^-1)", title_b="T (nm^-1)"), parsed(cap, bbox=bl_bbox(FIG)))
    assert all(p.qrange.withheld_reason == "axis_not_identified_as_q" for p in fig.panels)


def test_panel_count_mismatch_blocks_all_values_and_is_surfaced():
    cap = "Figure 1. (a) pattern, scale bar 200 nm. (b) pattern. (c) pattern."
    fig = run(multi_panel_pdf(cap), parsed(cap, bbox=bl_bbox(FIG)))
    assert [u.code for u in fig.uncertainties if u.blocking] == ["panel_count_mismatch"]
    assert len(fig.panels) == 1 and fig.panels[0].method == "whole_figure"
    p = fig.panels[0]
    assert not p.scale.reported and not p.qrange.reported
    assert p.scale.withheld_reason == "panel_count_mismatch" == p.qrange.withheld_reason
    assert p.locator.figure == fig.figure_id and p.locator.bbox is not None


def test_rotated_page_withholds_everything():
    fig = run(multi_panel_pdf(CAPTION, rotation=90), parsed(CAPTION, bbox=bl_bbox(FIG)))
    assert fig.blocked == "page_rotated"
    assert fig.panels == () or all(not p.scale.reported and not p.qrange.reported for p in fig.panels)


def test_unlocated_figure_is_explicit():
    fig = run(multi_panel_pdf(CAPTION), parsed(CAPTION, bbox=None))   # vector figure: no raster to localize
    assert fig.text_status == "unlocated" and fig.blocked == "figure_region_unlocated"
    assert fig.panels == () and fig.locator.page == 1 and fig.locator.bbox is None


def test_missing_text_layer_is_a_retryable_capability_error_not_text_only_success():
    pdf = raster_figure_pdf("Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern.")
    doc = parsed("Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern.", bbox=None)   # located from the raster
    fig = run(pdf, doc)
    assert fig.text_status == "insufficient"
    assert fig.capability_error and fig.capability_error["retryable"] is True
    assert fig.capability_error["capability"] == "text_layer"
    assert fig.locator.bbox == pytest.approx(FIG, abs=0.5) and fig.geometry["region_source"] == "page_images"
    assert all(not p.qrange.reported for p in fig.panels)


def test_ocr_requested_but_engine_missing_is_retryable_unavailable(monkeypatch):
    import app.modules.vision.figure_extraction as fx

    def boom():
        raise VisionUnavailable("OCR engine", "not installed")

    monkeypatch.setattr(fx, "default_engine", boom)
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    fig = run(raster_figure_pdf(cap), parsed(cap), use_ocr=True)
    assert fig.text_status == "ocr_unavailable"
    assert fig.capability_error["retryable"] is True and fig.capability_error["capability"] == "OCR engine"


class FakeOcr(OcrEngine):
    name = "fake-ocr"

    def __init__(self, dets):
        self.dets = dets

    def detect(self, image):
        assert image.ndim == 2
        return self.dets


def test_ocr_fallback_spans_are_tagged_and_their_units_never_believed():
    cap = "Figure 1. (a) 2D GIWAXS pattern. (b) 2D GIWAXS pattern."
    # figure render is 1000x480 px; panel a spans x 20..480. OCR reads ticks AND a unit title.
    ticks = ["0.5", "1.0", "1.5", "2.0"]
    dets = [Detection(t, (40 + i * 90, 400, 60 + i * 90, 415), 0.97) for i, t in enumerate(ticks)]
    dets.append(Detection("q (nm^-1)", (100, 370, 200, 385), 0.95))
    dets.append(Detection("noise", (5, 5, 20, 20), 0.2))
    fig = run(raster_figure_pdf(cap), parsed(cap), use_ocr=True, ocr_engine=FakeOcr(dets))
    assert fig.text_status == "ocr_fallback" and fig.capability_error is None
    assert fig.ocr == {"engine": "fake-ocr", "method": "ocr", "n_spans": 5, "min_confidence": 0.95}
    a = fig.panels[0]
    assert not a.qrange.reported and a.qrange.withheld_reason == "no_unit"   # OCR unit refused
    assert TextSpan("x", None, 1, "ocr").is_ocr


def test_results_carry_no_image_bytes_paths_or_urls():
    out = extract_figures(parsed(CAPTION, bbox=bl_bbox(FIG)), multi_panel_pdf(CAPTION), work_id="10.1000/test")
    blob = json.dumps([f.as_dict() for f in out.figures])
    for needle in ("data:image", "base64", "http://", "https://", "/papers/", "/figures/", ".png", "image_bytes"):
        assert needle not in blob
    assert all(not isinstance(v, (bytes, bytearray)) for f in out.figures for v in f.as_dict().values())
    for ev in out.evidence:
        assert ev.locator.page == 1 and ev.work_id == "10.1000/test" and ev.source_sha256 == SHA


def test_evidence_covers_figure_panels_and_reported_values():
    out = extract_figures(parsed(CAPTION, bbox=bl_bbox(FIG)), multi_panel_pdf(CAPTION), work_id="10.1000/test")
    kinds = [(e.locator.panel, e.locator.figure) for e in out.evidence]
    assert (None, out.figures[0].figure_id) in kinds
    assert {k[0] for k in kinds} >= {None, "a", "b"}
    assert len(out.evidence) == len({e.evidence_id for e in out.evidence})


def test_classification_units_prefer_panels_and_record_class_basis():
    fig = run(multi_panel_pdf(CAPTION), parsed(CAPTION, bbox=bl_bbox(FIG)))
    assert fig.classification["class"] == "experimental_scattering" and fig.classification["source_supported"]
    assert [u["subject_type"] for u in fig.classification_units] == ["figure_panel", "figure_panel"]
    assert all(p.classification["class"] == "experimental_scattering" for p in fig.panels)
