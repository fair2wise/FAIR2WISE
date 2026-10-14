"""Reading a scattering figure's q range off its axis ticks.

WHAT THE TRIGGER NEEDS AND WHY IT DOES NOT HAVE IT. The dual fit compares a
micrograph's spatial frequencies against a scattering pattern's q range, and
those bands must OVERLAP or each modality constrains only what it can see.
Measured on a 24-pair sample: selecting on figure CLASS picked 24 candidates
where a q criterion picks 1. The q range exists only in the figure's axis, and
nothing has ever read it.

THREE THINGS MEASURED ON REAL SPAN DATA, each of which shapes the code:

1. **Axis rows MIX PANELS.** At one page y this corpus yields
   ``['0.8','0.6','0.4','0.2','0.0','400','500','600','700','800']`` -- two
   side-by-side panels' axes at the same height, one running 0-0.8 and one
   300-800. Clustering by coordinate alone merges them into an axis that never
   existed and fits a slope through both. **This module therefore takes spans
   ALREADY RESTRICTED TO ONE PANEL**; the caller does that with
   `geometry.spans_inside`, and skipping it produces a confident wrong range.

2. **Log axes arrive as minor-tick runs.** ``['2','4','6','8']`` between decade
   labels like ``'10 -1'``. Fitting those linearly gives a plausible slope and
   a wrong range, so both fits are tried and the better one wins.

3. **Unit runs are separate from the numbers, and some are traps.** Real runs
   here include ``'(Å -1 )'``, ``'(nm -1 )'``, ``'2  (nm -2 )'`` and
   ``'-1  (nm)'``. The second-to-last is a q-SQUARED axis and the last is a
   length axis; both contain the substring ``nm``, and reading either as an
   inverse nanometre is a silent error. Only an exact inverse-length unit
   counts.

**THE UNIT NEVER COMES FROM OCR.** Measured: RapidOCR reads 6-11 px tick digits
at 0.95-1.00 and mangles the exponent on 100% of attempts -- ``q (nm⁻¹)`` comes
back as ``'q(nm^{1)'``. Digits from OCR are usable; the exponent is not, and it
is the factor of ten. A reading whose unit came from OCR is UNDETERMINABLE here
however clean its ticks looked.


Adapted from als-computing/forge_rsoxs_analysis src/forge/analysis/figure_extraction/qaxis.py (BSD-3-Clause).
"""

from __future__ import annotations

import math
import re

from .types import QAxisReading, TextSpan
from .units import fold_inverse_length, normalise_unit

__all__ = [
    "MIN_AXIS_TICKS",
    "MIN_FIT_R2",
    "AxisCandidate",
    "find_axis_candidates",
    "read_q_axis",
]

#: Fewest ticks that can define an axis. Two points fit any straight line
#: exactly, so a two-tick "axis" reports a perfect fit and proves nothing --
#: three is the fewest that can DISAGREE with its own model.
MIN_AXIS_TICKS: int = 3

#: How well the ticks must fit before a range is reported. Below this the
#: cluster is not an axis: it is numbers that happen to share a coordinate,
#: which is exactly what a two-panel row looks like.
MIN_FIT_R2: float = 0.995

_NUMBER = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
#: A unit inside brackets, which is how every axis title in this corpus writes
#: it. Deliberately NOT a bare search for "nm": '-1  (nm)' is a length axis and
#: '2  (nm -2 )' is a q-squared axis, and both contain that substring.
_BRACKETED_UNIT = re.compile(r"[\(\[]\s*([^)\]]{1,20})\s*[\)\]]")


class AxisCandidate:
    """A run of numeric ticks sharing one coordinate.

    Attributes:
        orientation: ``"x"`` (a row of ticks, value varying with pixel x) or
            ``"y"`` (a column).
        ticks: ``(value, position_px)`` pairs, position along the varying axis.
    """

    def __init__(self, orientation: str, ticks: list[tuple[float, float]]) -> None:
        self.orientation = orientation
        self.ticks = sorted(ticks, key=lambda t: t[1])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AxisCandidate({self.orientation!r}, {len(self.ticks)} ticks)"

    def fit(self) -> tuple[str, float]:
        """Best of a linear and a log fit, as ``(kind, r_squared)``.

        Returns:
            ``("linear" | "log", r2)``. A log fit is only attempted when every
            tick value is positive, since ``log10`` of a zero or negative tick
            is not a number -- and a linear axis through zero is extremely
            common here (``['1.0','0.8',...,'0.0']``).
        """
        values = [v for v, _ in self.ticks]
        best_kind, best_r2 = "linear", _r_squared([p for _, p in self.ticks], values)
        if all(v > 0 for v in values):
            log_r2 = _r_squared([p for _, p in self.ticks], [math.log10(v) for v in values])
            if log_r2 > best_r2:
                best_kind, best_r2 = "log", log_r2
        return best_kind, best_r2


def _r_squared(xs: list[float], ys: list[float]) -> float:
    """Coefficient of determination of a straight-line fit of *ys* on *xs*.

    Returns 0.0 rather than raising when the fit is undefined -- fewer than two
    points, or no spread in either axis. An undefined fit is not a good one,
    and returning a number keeps the caller's comparison total.
    """
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return (sxy * sxy) / (sxx * syy)


def _axis_unit(spans: list[TextSpan]) -> tuple[str, str] | None:
    """The first inverse-length unit any span states, as ``(key, source)``.

    Args:
        spans: Candidate runs, typically a panel's.

    Returns:
        ``(normalised_unit, "text_layer")``, or ``None`` when no span states an
        inverse length. **An OCR-sourced span is skipped entirely** -- the
        exponent it carries cannot be believed, and a unit is exactly the
        exponent.
    """
    for span in spans:
        if span.is_ocr:
            continue
        for match in _BRACKETED_UNIT.finditer(span.text):
            key = normalise_unit(match.group(1))
            if fold_inverse_length(1.0, key) is not None:
                return key, "text_layer"
    return None


def find_axis_candidates(
    spans: list[TextSpan], *, tolerance_px: float = 4.0
) -> list[AxisCandidate]:
    """Group a panel's numeric spans into rows and columns.

    Args:
        spans: Runs from ONE panel. Passing a whole figure's spans merges the
            axes of side-by-side panels -- measured, and it produces a slope
            through two unrelated ranges.
        tolerance_px: How far apart two ticks' shared coordinate may be and
            still count as one axis.

    Returns:
        Every candidate with at least :data:`MIN_AXIS_TICKS` ticks, largest
        first. Spans with no bbox are skipped: an axis is a geometric claim.
    """
    numeric: list[tuple[float, float, float]] = []
    for span in spans:
        text = span.text.strip().replace(",", ".")
        if not _NUMBER.match(text) or span.bbox is None:
            continue
        left, top, right, bottom = span.bbox
        numeric.append((float(text), (left + right) / 2.0, (top + bottom) / 2.0))

    out: list[AxisCandidate] = []
    for orientation, shared, varying in (("x", 2, 1), ("y", 1, 2)):
        buckets: list[list[tuple[float, float, float]]] = []
        for item in sorted(numeric, key=lambda t: t[shared]):
            if buckets and abs(item[shared] - buckets[-1][-1][shared]) <= tolerance_px:
                buckets[-1].append(item)
            else:
                buckets.append([item])
        for bucket in buckets:
            if len(bucket) >= MIN_AXIS_TICKS:
                out.append(AxisCandidate(orientation, [(b[0], b[varying]) for b in bucket]))
    out.sort(key=lambda a: len(a.ticks), reverse=True)
    return out


def read_q_axis(
    spans: list[TextSpan], *, caption_unit: str = "", document_unit: str = ""
) -> QAxisReading:
    """The q range this panel's axis states, in inverse nanometres.

    THE UNIT IS THE BOTTLENECK, MEASURED. Over 60 sampled scattering figures
    (236 panels): `no_unit` 221, `no_axis` 12, `no_positive_ticks` 2, determined
    1. The ticks read fine; what is missing is what they MEAN. Only 2 of those
    60 captions stated a unit.

    So three sources are tried, MOST SPECIFIC FIRST -- this panel's own axis
    text, then this figure's caption, then the paper's prose. Each is recorded
    in `unit_source`, because they are not equally strong evidence and a
    reviewer ranking two readings needs to know which was used.

    Args:
        spans: Runs from ONE panel (see :func:`find_axis_candidates`).
        caption_unit: A unit taken from the caption, used only when the text
            layer states none. Both are acceptable provenance; OCR is not.
        document_unit: A unit taken from the PAPER's section text. **NOT USED
            BY THE EXTRACTION PIPELINE, and a reading that rests on it CANNOT
            BE STORED** -- `record_figure_qrange` refuses `unit_source
            ="sections"`, which is not in `QRANGE_UNIT_SOURCES`.

            It exists because the coverage is real (4% of scattering figures
            get a unit from a caption; 44% would get one from their paper) and
            because enabling it revealed the actual blocker: nothing here checks
            that the axis `find_axis_candidates` found IS the q axis. It groups
            numeric spans geometrically. With a unit available 11x more often,
            one 60-figure sample produced a Solar Physics figure at "50-80
            nm^-1", panels at "100-105" and "100-500", and a log axis at
            "0.1-1000" -- intensity and temperature axes fitted and labelled q,
            every one at r^2 > 0.9999 with full provenance.

            Q-AXIS IDENTIFICATION IS THE MISSING PIECE, not the unit. Wire this
            up once an axis can be shown to be q -- by its title, by a physical
            bound, or both -- and admit "sections" at the storage boundary then.

    Returns:
        A :class:`QAxisReading`. ``q_min``/``q_max`` are the SMALLEST and
        LARGEST LABELLED TICK, not an extrapolation to the panel edge -- the
        axis may run past its last tick, and inventing that margin would report
        a range the figure never claims. The result is therefore a
        conservative inner bound, which is the safe direction for an overlap
        test.

        Every refusal is named: ``"no_axis"``, ``"poor_fit"``, ``"no_unit"``.
    """
    unit = _axis_unit(spans)
    unit_source = ""
    if unit is not None:
        unit_key, unit_source = unit
    elif caption_unit and fold_inverse_length(1.0, normalise_unit(caption_unit)):
        unit_key, unit_source = normalise_unit(caption_unit), "caption"
    elif document_unit and fold_inverse_length(1.0, normalise_unit(document_unit)):
        unit_key, unit_source = normalise_unit(document_unit), "sections"
    else:
        return QAxisReading(
            reason="no_unit",
            needs_review=True,
            review_reasons=["no_inverse_length_unit_from_text_layer_caption_or_sections"],
        )

    candidates = find_axis_candidates(spans)
    if not candidates:
        return QAxisReading(unit_source=unit_source, reason="no_axis")

    best = None
    for candidate in candidates:
        kind, r2 = candidate.fit()
        if r2 >= MIN_FIT_R2 and (best is None or r2 > best[2]):
            best = (candidate, kind, r2)
    if best is None:
        kind, r2 = candidates[0].fit()
        return QAxisReading(
            unit_source=unit_source,
            axis_kind=kind,
            n_ticks=len(candidates[0].ticks),
            confidence=r2,
            reason="poor_fit",
            needs_review=True,
            review_reasons=[f"best_r2_{r2:.3f}_below_{MIN_FIT_R2}"],
        )

    candidate, kind, r2 = best
    values = [v for v, _ in candidate.ticks]
    lo = fold_inverse_length(min(values), unit_key)
    hi = fold_inverse_length(max(values), unit_key)
    if lo is None or hi is None:
        # A zero or negative tick cannot be a q value; the axis is real but the
        # end it names is not usable as a bound.
        positive = [v for v in values if v > 0]
        if len(positive) < MIN_AXIS_TICKS:
            return QAxisReading(
                unit_source=unit_source,
                axis_kind=kind,
                n_ticks=len(values),
                reason="no_positive_ticks",
                needs_review=True,
                review_reasons=["axis_ticks_not_positive"],
            )
        lo = fold_inverse_length(min(positive), unit_key)
        hi = fold_inverse_length(max(positive), unit_key)

    # A UNIT BORROWED FROM THE PAPER IS NOT A UNIT READ OFF THE AXIS. The
    # numbers are this panel's; what they mean came from a document-level
    # convention. That is right far more often than not -- which is why it is
    # allowed at all -- and it is not something to store silently, because the
    # failure it can produce is a factor of ten that lands on a plausible
    # number. Flagged, so a reviewer sees it and `unit_source` says why.
    from_document = unit_source == "sections"
    return QAxisReading(
        q_min_nm_inv=lo,
        q_max_nm_inv=hi,
        unit_source=unit_source,
        axis_kind=kind,
        n_ticks=len(candidate.ticks),
        confidence=r2,
        needs_review=from_document,
        review_reasons=(
            ["unit_taken_from_the_paper_prose_not_this_figure"] if from_document else []
        ),
    )
