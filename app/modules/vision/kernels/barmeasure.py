"""Scale-bar length in pixels from one corner of a greyscale panel (pure numpy).

Rewritten for F2W: the donor's scalebar kernel imported ``forge.analysis.resolution``
(experimental analysis). Only the bar-length measurement is needed, so it is
reproduced here with the same rules: saturated-run search in one quadrant,
refusal on a mostly-saturated region, full-row measurement and half-maximum
sub-pixel refinement.

Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/resolution.py
(``measure_scale_bar_result``, ``ScaleBarReading``, ``MAX_SATURATED_FRACTION``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["MAX_SATURATED_FRACTION", "ScaleBarReading", "measure_scale_bar_result"]

#: A quadrant with more saturated pixels than this is background, not a bar.
MAX_SATURATED_FRACTION: float = 0.10
_QUADRANTS = ("bottom-right", "bottom-left", "top-right", "top-left")


@dataclass(frozen=True)
class ScaleBarReading:
    """A bar length in pixels, or the named reason there is none."""

    length_px: float | None
    reason: str = ""

    @property
    def found(self) -> bool:
        """True only when a length was measured."""
        return self.length_px is not None


def _longest_run(hot: np.ndarray) -> tuple[int, int, int]:
    """``(length, row, start_col)`` of the longest True run on any row of a 2-D mask."""
    best = (0, -1, 0)
    for r in range(hot.shape[0]):
        row = hot[r]
        if not row.any():
            continue
        padded = np.concatenate(([False], row, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        starts, ends = edges[0::2], edges[1::2]
        lengths = ends - starts
        i = int(lengths.argmax())
        if lengths[i] > best[0]:
            best = (int(lengths[i]), r, int(starts[i]))
    return best


def measure_scale_bar_result(
    image: np.ndarray,
    *,
    quadrant: str = "bottom-right",
    saturation: int = 250,
    max_saturated_fraction: float = MAX_SATURATED_FRACTION,
) -> ScaleBarReading:
    """Sub-pixel length of the saturated bar in one quadrant, or why not (never a zero)."""
    arr = np.asarray(image)
    if arr.ndim != 2:
        raise ValueError(f"image must be 2-D greyscale; got shape {arr.shape}")
    if quadrant not in _QUADRANTS:
        raise ValueError(f"unknown quadrant {quadrant!r}")
    rows, cols = arr.shape
    r0, r1 = (rows // 2, rows) if quadrant.startswith("bottom") else (0, rows // 2)
    c0, c1 = (cols // 2, cols) if quadrant.endswith("right") else (0, cols // 2)
    crop = arr[r0:r1, c0:c1]
    if crop.size == 0:
        return ScaleBarReading(None, "empty_region")
    hot = crop >= saturation
    if float(hot.mean()) > max_saturated_fraction:
        return ScaleBarReading(None, "region_mostly_saturated")
    best_len, best_row, best_start = _longest_run(hot)
    if best_len < 3:
        return ScaleBarReading(None, "no_bar")
    # Locate in the quadrant, measure on the full row so a bar crossing the quadrant edge is not cut.
    row_g, col_g = r0 + best_row, c0 + best_start
    row_hot = arr[row_g] >= saturation
    left, right = col_g, col_g + best_len - 1
    while left > 0 and row_hot[left - 1]:
        left -= 1
    while right < cols - 1 and row_hot[right + 1]:
        right += 1
    best_start, best_len = left, right - left + 1
    span = slice(best_start, best_start + best_len)
    band = np.flatnonzero((arr[:, span] >= saturation).all(axis=1))
    band_rows = band.tolist() or [row_g]
    lo, hi = max(0, best_start - 4), min(cols, best_start + best_len + 4)
    profile = arr[band_rows, lo:hi].astype(float).mean(axis=0)
    peak, floor = float(profile.max()), float(profile.min())
    if peak <= floor:
        return ScaleBarReading(float(best_len))
    half = floor + 0.5 * (peak - floor)
    above = np.flatnonzero(profile >= half)
    if above.size < 2:
        return ScaleBarReading(float(best_len))

    def edge(i_in: int, i_out: int) -> float:
        y0, y1 = profile[i_out], profile[i_in]
        return float(i_in) if y1 == y0 else float(i_out) + (half - y0) / (y1 - y0) * (i_in - i_out)

    left_f = edge(int(above[0]), max(int(above[0]) - 1, 0))
    right_f = edge(int(above[-1]), min(int(above[-1]) + 1, profile.size - 1))
    return ScaleBarReading(max(float(right_f - left_f), 1.0))
