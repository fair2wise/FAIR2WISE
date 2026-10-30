"""Source-text-first figure pipeline (V-03): localize -> text layer -> panels -> classify -> readings -> evidence.

For each parsed figure: place it on its page (parser bbox in either origin, else raster-image
fallback), read the PDF text layer inside it, segment panels (gutters; letters from the text
layer, else the caption), classify from caption context, read per-panel scale bars and q axes,
and attach a contracts :class:`SourceLocator` (page, figure, panel, bbox in pt, origin top-left)
to every figure, panel and reported value. Ambiguity (rotated page, unlocated figure, panel
count mismatch, unlabelled/reading-order panels, conflicting units, unidentified axes) is
recorded as :class:`Uncertainty`; blocking ones withhold readings.

OCR runs only when the caller opts in and only when the text layer is insufficient; a missing
engine is a retryable capability error on the figure, never a silent text-only success.
Pixels exist only inside :func:`analyze_figure`; nothing returned holds image bytes, a store
path or an asset URL (D19).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_extraction.py
(``extract_figure`` order of operations, panel reconciliation and ``panel_path`` identity scheme)
(BSD-3-Clause). The donor wrote PNG assets to disk; this version keeps pixels in memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..contracts.evidence import EvidenceRef, SourceLocator
from ..literature.parsed_document import ParsedDocument, ParsedFigure
from .classification import ChatAdapter, classify_text_context
from .classification_units import Unit, resolve_units
from .kernels.geometry import spans_inside
from .kernels.panels import label_panels_from_spans, panel_labels, reconcile_panels, segment_panels
from .kernels.types import PanelBox, TextSpan
from .ocr import OcrEngine, OcrReading, default_engine, read_figure_text
from .qrange import QRangeFinding, read_qranges
from .results import BLOCKING, EXTRACTION_VERSION, Box, Uncertainty, VisionUnavailable, box4, evidence, locator
from .scale import ScaleFinding, read_scales
from .text_layer import FigureTextLayer, open_pdf, render_gray, text_layer_for

CAPTION_CHARS = 600
ALPHABET = "abcdefgh"


@dataclass(frozen=True)
class PanelResult:
    """One panel with explicit identity, geometry and its readings."""

    panel_id: str
    label: str
    ordinal: int
    label_source: str                # "text_layer" | "caption_reading_order" | "none"
    method: str
    pixel_box: tuple[int, int, int, int]   # figure render pixels, top-left origin
    bbox: Box                        # page points, top-left origin
    locator: SourceLocator
    classification: dict[str, Any] = field(default_factory=dict)
    scale: ScaleFinding | None = None
    qrange: QRangeFinding | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "panel_id": self.panel_id, "label": self.label or None, "ordinal": self.ordinal,
            "label_source": self.label_source, "method": self.method,
            "pixel_box": list(self.pixel_box), "bbox": list(self.bbox),
            "bbox_units": "pt", "bbox_origin": "top-left",
            "locator": self.locator.model_dump(exclude_none=True),
            "classification": self.classification,
            "scale": self.scale.as_dict() if self.scale else None,
            "q_range": self.qrange.as_dict() if self.qrange else None,
        }


@dataclass(frozen=True)
class FigureAnalysis:
    """Everything derived for one figure; contains no pixels, paths or URLs."""

    figure_id: str
    page: int
    caption: str
    locator: SourceLocator
    geometry: dict[str, Any]
    text_status: str                 # "ok" | "insufficient" | "ocr_fallback" | "ocr_unavailable" | "unlocated"
    n_text_spans: int
    ocr: dict[str, Any] | None
    panels: tuple[PanelResult, ...] = ()
    classification: dict[str, Any] = field(default_factory=dict)
    classification_units: tuple[dict[str, Any], ...] = ()
    uncertainties: tuple[Uncertainty, ...] = ()
    capability_error: dict[str, Any] | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def blocked(self) -> str:
        """The first blocking uncertainty code, or ``""``."""
        return next((u.code for u in self.uncertainties if u.blocking), "")

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {
            "figure_id": self.figure_id, "page": self.page, "caption": self.caption[:CAPTION_CHARS],
            "locator": self.locator.model_dump(exclude_none=True), "geometry": self.geometry,
            "text_layer": {"status": self.text_status, "n_spans": self.n_text_spans, "ocr": self.ocr},
            "panels": [p.as_dict() for p in self.panels], "classification": self.classification,
            "classification_units": list(self.classification_units),
            "uncertainties": [u.as_dict() for u in self.uncertainties],
            "capability_error": self.capability_error,
        }


def figure_id_of(figure: ParsedFigure) -> str:
    """The deterministic figure identifier (parser-stamped, else ``fig-p{page:03d}-{index:02d}``)."""
    return figure.figure_id or f"fig-p{figure.page:03d}-{figure.index:02d}"


def _assign_panels(
    image: Any, layer: FigureTextLayer, caption: str
) -> tuple[list[PanelBox], list[str], list[Uncertainty]]:
    """Segment panels and name them: text-layer letters first, then caption reading order, else unlabelled."""
    height, width = image.shape
    unc: list[Uncertainty] = []
    boxes = segment_panels(image) or [PanelBox(0, 0, width, height, method="whole_figure")]
    letters = {x for x in panel_labels(caption) if x in ALPHABET}
    allowed = ALPHABET if not letters else "".join(sorted(letters))
    if len(letters) >= 2 and len(letters) != len(boxes):
        unc.append(Uncertainty("panel_count_mismatch",
                               f"caption names {len(letters)} panels, geometry found {len(boxes)}; "
                               "figure kept whole and readings withheld", blocking=True))
        return [PanelBox(0, 0, width, height, method="whole_figure")], ["none"], unc
    by_text = label_panels_from_spans(boxes, [s for s in layer.spans if s.source == "text_layer"],
                                      layer.frame, allowed=allowed)
    if len(boxes) > 1 and all(b.label for b in by_text) and len({b.label for b in by_text}) == len(by_text):
        return by_text, ["text_layer"] * len(by_text), unc
    if len(letters) >= 2:
        settled = reconcile_panels(boxes, letters, (height, width))
        unc.append(Uncertainty("panel_labels_from_reading_order",
                               "panel letters assigned from caption order, not printed labels"))
        return settled, ["caption_reading_order"] * len(settled), unc
    if len(boxes) > 1:
        unc.append(Uncertainty("panels_unlabelled", f"{len(boxes)} panels found, none lettered"))
    return boxes, ["none"] * len(boxes), unc


def analyze_figure(
    doc: Any,
    figure: ParsedFigure,
    info: Any = None,
    *,
    work_id: str = "",
    source_sha256: str = "",
    use_ocr: bool = False,
    ocr_engine: OcrEngine | None = None,
    model: ChatAdapter | None = None,
    default_scale: float = 2.0,
) -> FigureAnalysis:
    """Run the full pipeline on one parsed figure (see the module docstring)."""
    fid = figure_id_of(figure)
    layer = text_layer_for(doc, figure, info, default_scale=default_scale)
    unc: list[Uncertainty] = list(layer.uncertainties)
    caption = figure.caption or ""
    fig_loc = locator(figure.page, fid, None, layer.region)
    if layer.region is None or layer.frame is None:
        return FigureAnalysis(fid, figure.page, caption, fig_loc, layer.geometry(), "unlocated", 0, None,
                              uncertainties=tuple(unc))
    image = render_gray(doc, figure.page, layer.region, layer.scale)
    spans: list[TextSpan] = list(layer.spans)
    status, ocr_info, cap_err = "ok", None, None
    if not layer.sufficient:
        status = "insufficient"
        cap_err = {"capability": "text_layer", "retryable": True,
                   "detail": "text layer has too few spans inside the figure; retry with use_ocr enabled"}
        if use_ocr:
            try:
                engine = ocr_engine or default_engine()
                reading: OcrReading = read_figure_text(image, layer.frame, engine=engine, page=figure.page)
                spans.extend(reading.spans)
                status, cap_err, ocr_info = "ocr_fallback", None, reading.as_dict()
            except VisionUnavailable as exc:
                status = "ocr_unavailable"
                cap_err = {"capability": exc.capability, "retryable": True, "detail": str(exc)}
        unc.append(Uncertainty("text_layer_insufficient",
                               f"{len(layer.spans)} text-layer span(s) inside the figure"))
    boxes, label_sources, panel_unc = _assign_panels(image, layer, caption)
    unc.extend(panel_unc)
    blocked = next((u.code for u in unc if u.blocking and u.code in BLOCKING), "")

    frame = layer.frame
    by_panel: dict[int, list[TextSpan]] = {
        i: spans_inside(spans, frame, (float(b.left), float(b.top), float(b.right), float(b.bottom)))
        for i, b in enumerate(boxes)}
    scales = read_scales(image, boxes, caption, by_panel, blocked=blocked)
    qranges = read_qranges(boxes, caption, by_panel, blocked=blocked)
    fig_class = classify_text_context(caption, model=model)

    panels: list[PanelResult] = []
    refs: list[EvidenceRef] = []
    if work_id and source_sha256:
        refs.append(evidence(work_id, source_sha256, fig_loc, "figure"))
    verdicts: dict[str, dict[str, Any]] = {fid: fig_class}
    parents: dict[str, str] = {}
    for i, box in enumerate(boxes):
        label = box.label
        x0, y0 = frame.to_points(box.left, box.top)
        x1, y1 = frame.to_points(box.right, box.bottom)
        bbox = box4((x0, y0, x1, y1))
        tag = label or f"#{i + 1}"
        pid = f"{fid}-panel-{label or i + 1}"
        loc = locator(figure.page, fid, tag, bbox)
        axis_text = " ".join(s.text for s in by_panel[i] if s.source == "text_layer")[:500]
        verdict = (classify_text_context(caption, label, extra_text=axis_text, model=model)
                   if len(boxes) > 1 else fig_class)
        panels.append(PanelResult(pid, label, i + 1, label_sources[i], box.method,
                                  (box.left, box.top, box.right, box.bottom), bbox, loc, verdict, scales[i],
                                  qranges[i]))
        if len(boxes) > 1:
            verdicts[pid], parents[pid] = verdict, fid
        if work_id and source_sha256:
            refs.append(evidence(work_id, source_sha256, loc, "panel", pid))
            if scales[i].reported:
                refs.append(evidence(work_id, source_sha256, loc, "scale", scales[i].reading.nm_per_px))
            if qranges[i].reported:
                refs.append(evidence(work_id, source_sha256, loc, "q_range", qranges[i].reading.q_max_nm_inv))
    units: list[Unit] = resolve_units(verdicts, parents, panel_order={p.panel_id: p.ordinal for p in panels})
    return FigureAnalysis(
        fid, figure.page, caption, fig_loc, layer.geometry(), status, len(layer.spans), ocr_info, tuple(panels),
        fig_class,
        tuple({"subject_id": u.subject_id, "subject_type": u.subject_type, "figure_id": u.figure_id,
               "class": u.verdict.get("class") or None} for u in units),
        tuple(unc), cap_err, tuple(refs))


@dataclass
class ExtractionOutcome:
    """The figures analysed for one paper plus aggregate capability state."""

    work_id: str
    source_sha256: str
    figures: list[FigureAnalysis] = field(default_factory=list)

    @property
    def capability_errors(self) -> list[dict[str, Any]]:
        """One entry per figure that could not be fully read for lack of a capability."""
        return [{"figure_id": f.figure_id, **f.capability_error} for f in self.figures if f.capability_error]

    @property
    def evidence(self) -> tuple[EvidenceRef, ...]:
        """All evidence references, in figure order."""
        return tuple(e for f in self.figures for e in f.evidence)


def extract_figures(
    document: ParsedDocument,
    pdf_bytes: bytes,
    *,
    work_id: str = "",
    figure_ids: Sequence[str] = (),
    page: int | None = None,
    use_ocr: bool = False,
    ocr_engine: OcrEngine | None = None,
    model: ChatAdapter | None = None,
) -> ExtractionOutcome:
    """Analyse the selected figures (all when no selector) of one parsed paper."""
    doc = open_pdf(pdf_bytes)
    try:
        infos = {p.number: p for p in document.pages}
        wanted = [f for f in document.figures
                  if (not figure_ids or figure_id_of(f) in figure_ids) and (page is None or f.page == page)]
        outcome = ExtractionOutcome(work_id, document.file_sha256)
        for fig in wanted:
            outcome.figures.append(analyze_figure(
                doc, fig, infos.get(fig.page), work_id=work_id, source_sha256=document.file_sha256,
                use_ocr=use_ocr, ocr_engine=ocr_engine, model=model))
        return outcome
    finally:
        doc.close()


__all__ = ("EXTRACTION_VERSION", "ExtractionOutcome", "FigureAnalysis", "PanelResult", "analyze_figure",
           "extract_figures", "figure_id_of")
