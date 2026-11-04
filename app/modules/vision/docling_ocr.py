"""Figure OCR through Docling's built-in OCR stage (T30, V-05; owner decision 2026-11-03).

:class:`DoclingOcrEngine` implements :class:`~.ocr.OcrEngine` by running a Docling
``DocumentConverter`` on the rendered figure with RapidOCR in full-page mode. The RapidOCR
backend is ``torch`` (torch is already in the environment; ``onnxruntime`` is not), English
recogniser, and the checkpoints are read from ``<artifacts>/RapidOcr`` where
``scripts/fetch_docling_models.py`` puts them at image-build time, so nothing is downloaded
at run time. Docling's pipeline also runs its layout model, whose weights live in the same
artifacts directory.

Degradation is by availability, never by an import error: Docling is imported lazily, a
directory without the OCR weights (or a missing Docling) raises :class:`~.results.VisionUnavailable`
at construction, and any failure while reading raises it too, so ``analyze_figure`` reports
``ocr_unavailable`` exactly as it does with no engine installed. Nothing leaves the process.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO
import logging
import os
from pathlib import Path
from typing import Any, Literal

from ..literature.parsers import DOCLING_ARTIFACTS_ENV
from .ocr import Detection
from .results import VisionUnavailable

logger = logging.getLogger(__name__)

#: Folder under the Docling artifacts path that holds RapidOCR checkpoints (Docling's own layout).
OCR_WEIGHTS_FOLDER = "RapidOcr"
RAPIDOCR_BACKEND: Literal["torch"] = "torch"
RAPIDOCR_LANG = "en"
_CAPABILITY = "OCR engine"


def artifacts_dir(path: str | Path | None = None) -> Path | None:
    """The Docling artifacts directory: ``path``, else ``F2W_DOCLING_ARTIFACTS_PATH``, else ``None``."""
    raw = path or os.environ.get(DOCLING_ARTIFACTS_ENV) or None
    return Path(raw) if raw else None


def weights_available(path: str | Path | None = None) -> bool:
    """Whether the OCR checkpoints (detector, classifier, recogniser) are on disk. Imports nothing."""
    root = artifacts_dir(path)
    if root is None:
        return False
    folder = root / OCR_WEIGHTS_FOLDER
    try:
        names = [p.name.lower() for p in folder.iterdir() if p.is_file()]
    except OSError:  # weights folder unreadable: not installed
        return False
    return all(any(part in n and n.endswith(".pth") for n in names) for part in ("det", "cls", "rec"))


def _docling_stream(png: bytes) -> Any:
    from docling.datamodel.base_models import DocumentStream

    return DocumentStream(name="figure.png", stream=BytesIO(png))


def _build_converter(artifacts: Path) -> Any:
    """A Docling image converter with full-page RapidOCR, reading only from ``artifacts``."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    options = PdfPipelineOptions(
        artifacts_path=str(artifacts),
        do_ocr=True,
        ocr_options=RapidOcrOptions(backend=RAPIDOCR_BACKEND, lang=[RAPIDOCR_LANG], mode=OcrMode.FULL_PAGE),
        do_table_structure=False,
        generate_page_images=False,
        generate_picture_images=False,
    )
    converter = DocumentConverter(format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.IMAGE)   # loads the models now: missing weights fail here
    return converter


def _png_bytes(image: Any) -> bytes:
    import numpy as np
    from PIL import Image

    out = BytesIO()
    # dpi 72 makes Docling's page size (points) equal the pixel size; the mapping below does not rely on it.
    Image.fromarray(np.asarray(image, dtype=np.uint8)).save(out, format="PNG", dpi=(72, 72))
    return out.getvalue()


def _top_left_box(rect: Any, page_height: float) -> tuple[float, float, float, float]:
    xs = [rect.r_x0, rect.r_x1, rect.r_x2, rect.r_x3]
    ys = [rect.r_y0, rect.r_y1, rect.r_y2, rect.r_y3]
    if str(getattr(rect.coord_origin, "value", rect.coord_origin)).upper() == "BOTTOMLEFT":
        ys = [page_height - y for y in ys]
    return min(xs), min(ys), max(xs), max(ys)


class DoclingOcrEngine:
    """:class:`~.ocr.OcrEngine` over Docling's RapidOCR stage.

    Args:
        artifacts_path: Docling artifacts directory (default ``F2W_DOCLING_ARTIFACTS_PATH``).
        converter: An object with ``convert(source)`` returning a result with ``pages``; injected by
            tests so the glue runs without weights. Skips the weights check and the Docling import.
        source_factory: Turns PNG bytes into whatever ``converter.convert`` takes (Docling stream by default).

    Raises:
        VisionUnavailable: Docling is not importable, the OCR weights are absent, or the models fail to load.
    """

    name = "docling-rapidocr"

    def __init__(self, artifacts_path: str | Path | None = None, *, converter: Any = None,
                 source_factory: Callable[[bytes], Any] | None = None) -> None:
        self._source_factory = source_factory or _docling_stream
        if converter is not None:
            self._converter = converter
            return
        root = artifacts_dir(artifacts_path)
        if not weights_available(root):
            raise VisionUnavailable(
                _CAPABILITY, f"Docling OCR weights not found under {OCR_WEIGHTS_FOLDER}/ of "
                             f"${DOCLING_ARTIFACTS_ENV} (scripts/fetch_docling_models.py fetches them at image build)")
        assert root is not None
        try:
            self._converter = _build_converter(root)
        except Exception as exc:   # ImportError, FileNotFoundError, torch/rapidocr load errors
            raise VisionUnavailable(_CAPABILITY, f"Docling OCR could not start: {type(exc).__name__}: {exc}") from exc

    def detect(self, image: Any) -> Sequence[Detection]:
        """OCR a 2-D uint8 image; boxes are in the image's own pixels (top-left origin)."""
        height, width = int(image.shape[0]), int(image.shape[1])
        try:
            result = self._converter.convert(self._source_factory(_png_bytes(image)))
            pages = list(result.pages)
        except Exception as exc:
            raise VisionUnavailable(_CAPABILITY, f"Docling OCR failed: {type(exc).__name__}: {exc}") from exc
        out: list[Detection] = []
        for page in pages[:1]:
            size = getattr(page, "size", None)
            pw = float(size.width) if size is not None and size.width else float(width)
            ph = float(size.height) if size is not None and size.height else float(height)
            sx, sy = width / pw, height / ph
            for cell in page.cells:
                text = str(cell.text or "").strip()
                conf = getattr(cell, "confidence", None)
                if not text or conf is None:
                    continue
                x0, y0, x1, y1 = _top_left_box(cell.rect, ph)
                out.append(Detection(text, (x0 * sx, y0 * sy, x1 * sx, y1 * sy), float(conf)))
        return out


__all__ = ("DoclingOcrEngine", "OCR_WEIGHTS_FOLDER", "artifacts_dir", "weights_available")
