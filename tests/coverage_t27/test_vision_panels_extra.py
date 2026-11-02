"""Panel segmentation edge cases on synthetic plates (grids, stacks, margins, abstaining siblings)."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.vision.kernels.geometry import FigureFrame
from app.modules.vision.kernels.panels import (
    _bands, _empty_runs, _line_is_empty, _reading_order, _trim, caption_names_multiple_panels,
    consecutive_from_a, ink_mask, label_panels_from_spans, panel_labels, reconcile_panels, segment_panels,
)
from app.modules.vision.kernels.types import PanelBox, TextSpan


def plate(height, width, rects, value=60):
    img = np.full((height, width), 255, dtype=np.uint8)
    for left, top, right, bottom in rects:
        img[top:bottom, left:right] = value
    return img


def test_panel_labels_distinct_letters_and_multi_panel_question():
    assert panel_labels("(a) first and (b) second, then (a) again") == {"a", "b"}
    assert panel_labels("no lettering here") == set()
    assert caption_names_multiple_panels("(a) x (b) y") is True
    assert caption_names_multiple_panels("(a) x and again (a)") is False


def test_ink_mask_uses_border_median_as_background_and_validates_shape():
    img = np.full((20, 20), 200, dtype=np.uint8)
    img[5:10, 5:10] = 50
    mask = ink_mask(img)
    assert mask[7, 7] and not mask[0, 0] and mask.sum() == 25
    assert ink_mask(np.zeros((0, 5))).shape == (0, 5)
    with pytest.raises(ValueError, match="2-D"):
        ink_mask(np.zeros((3, 3, 3)))


def test_empty_runs_and_bands_and_trim_helpers():
    profile = np.array([1, 0, 0, 1, 0, 1, 0, 0, 0], dtype=float)
    assert _empty_runs(profile) == [(1, 3), (6, 9)]  # the single empty line at 4 is below MIN_GUTTER_PX
    box = (0, 0, 100, 40)
    assert _bands(box, "x", [(30, 40), (70, 80)], 20) == [(0, 0, 30, 40), (40, 0, 70, 40), (80, 0, 100, 40)]
    assert _bands(box, "x", [(10, 20)], 30) == [(20, 0, 100, 40)]  # the 10px sliver is dropped
    assert _bands(box, "y", [(10, 20)], 10) == [(0, 0, 100, 10), (0, 20, 100, 40)]
    ink = np.zeros((40, 100), dtype=bool)
    assert _trim(ink, box) is None and _trim(ink, (5, 5, 5, 20)) is None
    ink[10:20, 30:60] = True
    assert _trim(ink, box) == (30, 10, 60, 20)


def test_line_is_empty_abstains_outside_the_band_and_reads_both_axes():
    ink = np.zeros((50, 50), dtype=bool)
    ink[10:20, 10:20] = True
    box = (10, 10, 30, 30)
    assert _line_is_empty(ink, box, "y", 15) is False and _line_is_empty(ink, box, "y", 25) is True
    assert _line_is_empty(ink, box, "y", 5) is None and _line_is_empty(ink, box, "y", 40) is None
    assert _line_is_empty(ink, box, "x", 15) is False and _line_is_empty(ink, box, "x", 25) is True
    assert _line_is_empty(ink, box, "x", 5) is None and _line_is_empty(ink, box, "x", 45) is None
    assert _line_is_empty(ink, (10, 10, 10, 10), "y", 10) is None  # an empty slice cannot testify
    assert _line_is_empty(ink, (10, 10, 10, 30), "x", 10) is None


def test_segment_grid_of_four_panels_in_reading_order():
    img = plate(260, 260, [(10, 10, 120, 120), (140, 10, 250, 120), (10, 140, 120, 250), (140, 140, 250, 250)])
    boxes = segment_panels(img)
    assert [(b.left, b.top) for b in boxes] == [(10, 10), (140, 10), (10, 140), (140, 140)]
    assert all(b.method == "gutter" for b in boxes)


def test_segment_stacked_panels_use_the_row_axis_when_columns_do_not_split():
    img = plate(260, 130, [(10, 10, 120, 120), (10, 140, 120, 250)])
    boxes = segment_panels(img)
    assert [(b.top, b.bottom) for b in boxes] == [(10, 120), (140, 250)]


def test_segment_returns_whole_figure_for_blank_single_or_oversplit_images():
    blank = segment_panels(np.full((80, 80), 255, dtype=np.uint8))
    assert len(blank) == 1 and blank[0].method == "whole_figure" and (blank[0].right, blank[0].bottom) == (80, 80)
    single = segment_panels(plate(100, 100, [(10, 10, 90, 90)]))
    assert len(single) == 1 and single[0].method == "whole_figure"
    # 36 tiny cells exceed MAX_PANELS: the geometry is distrusted and the figure stands whole
    rects = [(10 + 66 * c, 10 + 66 * r, 66 * c + 66, 66 * r + 66) for r in range(6) for c in range(6)]
    cells = [(l, t, r - 6, b - 6) for l, t, r, b in rects]
    many = segment_panels(plate(410, 410, cells))
    assert len(many) == 1 and many[0].method == "whole_figure"


def test_segment_keeps_a_short_band_as_a_panel_instead_of_dropping_it():
    img = plate(100, 660, [(10 + 220 * i, 20, 10 + 220 * i + 100, 80) for i in range(3)])
    assert len(segment_panels(img)) == 3


def test_reading_order_groups_rows_despite_one_pixel_top_jitter():
    boxes = [(130, 11, 200, 90), (10, 10, 100, 90), (10, 120, 100, 200)]
    assert _reading_order(boxes) == [(10, 10, 100, 90), (130, 11, 200, 90), (10, 120, 100, 200)]


def test_consecutive_from_a_and_reconcile_edge_cases():
    assert consecutive_from_a(["a", "B", "c", "j", "ab"]) == {"a", "b", "c"}
    assert consecutive_from_a(["b", "c"]) == set()
    boxes = [PanelBox(0, 0, 10, 10), PanelBox(20, 0, 30, 10)]
    assert reconcile_panels(boxes, {"a"}, (10, 30)) == boxes  # one letter challenges nothing
    assert reconcile_panels(boxes, [], (10, 30)) == boxes
    out = reconcile_panels([PanelBox(20, 0, 30, 10), PanelBox(0, 0, 10, 10)], ["B", "a"], (10, 30))
    assert [(b.label, b.left) for b in out] == [("a", 0), ("b", 20)]


def test_label_from_spans_refuses_ambiguous_contested_and_disallowed_markers():
    frame = FigureFrame((0, 0, 300, 100), 1.0, "TOPLEFT")
    boxes = [PanelBox(0, 0, 100, 100), PanelBox(100, 0, 200, 100), PanelBox(200, 0, 300, 100)]
    spans = [
        TextSpan("(a)", (5, 5, 15, 15)),
        TextSpan("b", (105, 5, 115, 15)), TextSpan("c", (150, 40, 158, 50)),   # two markers: ambiguous box
        TextSpan("a", (205, 5, 215, 15)),                                       # contests the letter "a"
        TextSpan("long text", (210, 40, 260, 50)),
    ]
    out = label_panels_from_spans(boxes, spans, frame)
    assert [b.label for b in out] == ["", "", ""]
    only_a = label_panels_from_spans(boxes[:2], spans[:1] + [TextSpan("zz", (105, 5, 115, 15))], frame)
    assert [b.label for b in only_a] == ["a", ""]
    restricted = label_panels_from_spans(boxes[:2], [TextSpan("a", (5, 5, 15, 15)), TextSpan("j", (105, 5, 115, 15))],
                                         frame, allowed="ab")
    assert [b.label for b in restricted] == ["a", ""]  # "j" is outside the believable alphabet
