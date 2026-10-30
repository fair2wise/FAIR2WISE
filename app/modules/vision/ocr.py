"""Explicit, optional OCR fallback for figures whose text layer is insufficient (V-05).

OCR is never silent: a caller opts in (``use_ocr``), a missing engine raises
:class:`~.results.VisionUnavailable` (``UNAVAILABLE``, retryable), and every span
produced is tagged ``source="ocr"`` so the kernels never believe a unit from it
(OCR mangles the superscript exponent). The default engine imports ``rapidocr_onnxruntime``
lazily; it is NOT a dependency of this package. Tests inject a fake engine.

Method and confidence are recorded in :class:`OcrReading`; no image leaves the process.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/figure_ocr.py
(``detect``, ``read_bar_labels``, ``read_figure_text``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .kernels.geometry import FigureFrame
from .kernels.types import TextSpan
from .kernels.units import iter_lengths
from .results import VisionUnavailable

#: Detections below this confidence are dropped before they can become spans.
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Detection:
    """One OCR detection in figure-render pixels (top-left origin)."""

    text: str
    box_px: tuple[float, float, float, float]
    confidence: float


class OcrEngine(Protocol):
    """Anything that turns a 2-D uint8 array into detections."""

    name: str

    def detect(self, image: Any) -> Sequence[Detection]:  # pragma: no cover - protocol
        """Detect text in ``image``."""


class RapidOcrEngine:
    """Lazy adapter over ``rapidocr_onnxruntime`` (optional dependency)."""

    name = "rapidocr"

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise VisionUnavailable("OCR engine", "install rapidocr-onnxruntime to enable use_ocr") from exc
        self._engine = RapidOCR()

    def detect(self, image: Any) -> Sequence[Detection]:
        """Run RapidOCR and convert its polygons to axis-aligned boxes."""
        result, _ = self._engine(image)
        out: list[Detection] = []
        for poly, text, conf in result or []:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            out.append(Detection(str(text), (min(xs), min(ys), max(xs), max(ys)), float(conf)))
        return out


def default_engine() -> OcrEngine:
    """The default engine, or :class:`VisionUnavailable` (retryable) when not installed."""
    return RapidOcrEngine()


@dataclass(frozen=True)
class OcrReading:
    """OCR output as page-point spans plus the method and confidences that produced them."""

    spans: tuple[TextSpan, ...]
    confidences: tuple[float, ...]
    engine: str
    method: str = "ocr"

    @property
    def min_confidence(self) -> float | None:
        """Lowest confidence kept, or ``None`` when nothing was read."""
        return min(self.confidences) if self.confidences else None

    def as_dict(self) -> dict[str, Any]:
        """Provenance summary (counts and confidences, not text or pixels)."""
        return {"engine": self.engine, "method": self.method, "n_spans": len(self.spans),
                "min_confidence": self.min_confidence}


def read_figure_text(image: Any, frame: FigureFrame, *, engine: OcrEngine, page: int = 0,
                     detections: Sequence[Detection] | None = None,
                     min_confidence: float = MIN_CONFIDENCE) -> OcrReading:
    """OCR the rendered figure and return spans in the figure's page frame (points, top-left)."""
    found = engine.detect(image) if detections is None else detections
    spans: list[TextSpan] = []
    confs: list[float] = []
    for det in found:
        if det.confidence < min_confidence or not det.text.strip():
            continue
        x0, y0 = frame.to_points(det.box_px[0], det.box_px[1])
        x1, y1 = frame.to_points(det.box_px[2], det.box_px[3])
        spans.append(TextSpan(det.text, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), page, "ocr"))
        confs.append(det.confidence)
    return OcrReading(tuple(spans), tuple(confs), getattr(engine, "name", type(engine).__name__))


def read_bar_labels(image: Any, frame: FigureFrame, *, engine: OcrEngine, page: int = 0,
                    detections: Sequence[Detection] | None = None) -> OcrReading:
    """The subset of OCR spans that read as a length (e.g. ``"200 nm"``): candidate bar labels."""
    full = read_figure_text(image, frame, engine=engine, page=page, detections=detections)
    keep = [(s, c) for s, c in zip(full.spans, full.confidences) if iter_lengths(s.text)]
    return OcrReading(tuple(s for s, _ in keep), tuple(c for _, c in keep), full.engine, "ocr-bar-labels")
