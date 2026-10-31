"""Synthetic pilot paper, fake labelling client and fake model lister (no network, no key, no real paper).

The PDF is generated in ``tmp_path`` with reportlab from invented sentences; nothing from a
real paper is used or committed. Tests that do not need Docling's structure model parse through
:class:`StubDoclingParser`; tests that run the real parser carry :data:`needs_docling`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, List, Optional

import pytest

from app.modules.literature.parsed_document import BoundingBox, ParsedDocument, ParsedFigure
from app.modules.literature.pdf_reader import open_pdf
from app.modules.model_catalog import ModelListing
from evals.binary.labeling import parse as parse_module
from evals.binary.labeling import pilot as P
from evals.binary.labeling.parse import file_sha256

needs_docling = pytest.mark.skipif(
    not os.environ.get("F2W_DOCLING_ARTIFACTS_PATH"), reason="Docling model weights not available")

PAGE_1 = [
    "Synthetic Study of Imaginary Films",
    "The films were annealed at 150 C for 2 h.",
    "The lamellar spacing of film X is 21.0 nm.",
]
PAGE_2 = [
    "Results",
    "The peak position q* = 0.30 nm-1 was measured at 285.0 eV.",
    "A value of 9.9 nm was previously reported by others [7].",
]
LABELER = "fake-labeller"
JUDGE = "fake-judge"
MUT = "fake-model-under-test"


FIGURE_CAPTION = "Figure 1. Synthetic scattering profile of film X."


def _gradient_png() -> bytes:
    """A 200x200 grey gradient (a usable figure image for the parser)."""
    from io import BytesIO

    from PIL import Image

    img = Image.new("L", (200, 200))
    img.putdata([(x + y) % 256 for y in range(200) for x in range(200)])
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_pdf(path: Path, pages: Optional[List[List[str]]] = None, figure: bool = True) -> Path:
    """Write a small PDF with the synthetic sentences and, on page 2, one captioned figure."""
    from io import BytesIO

    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    height = 842  # A4
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, height), invariant=1)
    c.setTitle("")
    c.setAuthor("")
    c.setSubject("")
    c.setCreator("")
    for index, lines in enumerate(pages or [PAGE_1, PAGE_2]):
        y = 72
        c.setFont("Helvetica", 11)
        for line in lines:
            c.drawString(72, height - y, line)
            y += 18
        if figure and index == 1:
            c.drawImage(ImageReader(Image.open(BytesIO(_gradient_png()))), 72, height - 400, width=200, height=200)
            c.setFont("Helvetica", 9)
            c.drawString(72, height - 420, FIGURE_CAPTION)
        c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())
    return path


class StubDoclingParser:
    """Docling stand-in that needs no weights: one figure per raster image on the page, no tables.

    The figure box is the image's box converted to BOTTOMLEFT points, as Docling reports it, and the
    caption is the synthetic one, so everything downstream of the parser sees a realistic document.
    """

    name = "docling"

    def version(self) -> str:
        """Fixed stub version."""
        return "0.0-stub"

    def config(self) -> dict[str, Any]:
        """Fixed stub settings."""
        return {"stub": True}

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str = "") -> ParsedDocument:
        """Build the document from the PDF's page count and image boxes."""
        figures = []
        with open_pdf(pdf_path) as doc:
            count = len(doc)
            for page in doc:
                for index, (x0, y0, x1, y1) in enumerate(page.image_boxes()):
                    box = BoundingBox(x0, page.height - y0, x1, page.height - y1, "BOTTOMLEFT", "pt")
                    figures.append(ParsedFigure(caption=FIGURE_CAPTION, page=page.number + 1, index=index, bbox=box,
                                                figure_id=f"fig-p{page.number + 1:03d}-{index:02d}"))
        return ParsedDocument(source_ref=source_ref, markdown="", num_pages=count, parser=self.name,
                              parser_version=self.version(), parser_config=self.config(), figures=tuple(figures))


def synthetic_paper(pdf_rel: str, sha: str, pilot_id: str = "A") -> P.PilotPaper:
    """A pilot registry entry pointing at the synthetic PDF."""
    return P.PilotPaper(
        pilot_id=pilot_id, doi="10.0000/synthetic", short_title="Synthetic paper",
        versions=(P.PilotVersion(key="published", path=pdf_rel, sha256=sha),),
        cases=P.PILOT_PAPERS[pilot_id].cases, focus=("synthetic focus",), absent_checklist=("exposure time",),
    )


def install_synthetic(monkeypatch: Any, root: Path, pilot_id: str = "A") -> P.PilotPaper:
    """Generate the PDF under ``root/papers``, swap it into the pilot registry and stub the Docling parser."""
    monkeypatch.setattr(parse_module, "DoclingParser", StubDoclingParser)
    pdf = make_pdf(root / "papers" / "synthetic.pdf")
    paper = synthetic_paper("synthetic.pdf", file_sha256(pdf), pilot_id)
    monkeypatch.setitem(P.PILOT_PAPERS, pilot_id, paper)
    return paper


VALUES_ANSWER = {
    "values": [
        {"quantity": "lamellar spacing", "subject": "film X", "record_type": "SampleRecord", "value": "21.0",
         "value_numeric": 21.0, "uncertainty": None, "unit": "nm", "normalized_unit": "nm", "tag": "reported",
         "derivation": None, "citation": None,
         "locator": {"page": 1, "section": None, "figure": None, "table": None,
                     "quote": "The lamellar spacing of film X is 21.0 nm."}, "notes": None},
        {"quantity": "peak position", "subject": "film X", "record_type": "MeasurementRecord", "value": "0.30",
         "value_numeric": 0.3, "uncertainty": None, "unit": "nm-1", "normalized_unit": "nm^-1", "tag": "reported",
         "derivation": None, "citation": None,
         # stated on the wrong page on purpose: must be flagged wrong_page, not dropped
         "locator": {"page": 1, "section": "Results", "figure": None, "table": None,
                     "quote": "The peak position q* = 0.30 nm-1 was measured at 285.0 eV."}, "notes": None},
        {"quantity": "spacing", "subject": "other work", "record_type": "Other", "value": "9.9", "value_numeric": 9.9,
         "uncertainty": None, "unit": "nm", "normalized_unit": "nm", "tag": "cited", "derivation": None,
         "citation": "[7]",
         "locator": {"page": 2, "section": "Results", "figure": None, "table": None,
                     "quote": "A value of 9.9 nm was previously reported by others [7]."}, "notes": None},
        {"quantity": "film thickness", "subject": "film X", "record_type": "SampleRecord", "value": "40",
         "value_numeric": 40, "uncertainty": None, "unit": "um", "normalized_unit": None, "tag": "reported",
         "derivation": None, "citation": None,
         # a quote that is not in the text: kept and flagged
         "locator": {"page": 1, "section": None, "figure": None, "table": None,
                     "quote": "The films were 40 um thick."}, "notes": None},
    ],
    "table_totals": [],
}
FIGURES_ANSWER = {
    "figure_readings": [{"figure": "Fig. 1", "panel": None, "page": 2, "element": "x_axis", "quantity": "q",
                         "unit": None, "scale": "unknown", "range": None, "reading": None, "ambiguous": True,
                         "withhold_reason": "axis unit not stated", "needs_visual_check": True,
                         "quote": FIGURE_CAPTION}],
    "figure_totals": [{"figure": "Fig. 1", "panels": 1, "curves_per_panel": None, "page": 2,
                       "quote": FIGURE_CAPTION}],
}
CONTEXT_ANSWER = {
    "absent_quantities": [{"quantity": "exposure time", "why_absent": "not stated", "trap": None,
                           "nearest_quote": {"page": None, "quote": None}}],
    "gold_questions": [
        {"question_id": f"Q{i}", "case_ids": ["QA-01"], "question": f"Synthetic question {i}?",
         "answer_type": "numeric", "expected_answer": "21.0 nm", "acceptable_variants": [], "forbidden_answers": [],
         "evidence": [{"page": 1, "quote": "The lamellar spacing of film X is 21.0 nm."}]}
        for i in range(1, 6)
    ],
    "work_identity": {"title": "Synthetic Study of Imaginary Films", "authors_first": None, "doi": None,
                      "arxiv_id": None, "venue": None, "year": None, "version_kind": "unknown",
                      "version_date": None, "page_offset_note": None,
                      "evidence": [{"page": 1, "quote": "Synthetic Study of Imaginary Films"}]},
}


class FakeClient:
    """Answers by task; ``fail`` names tasks that raise, ``garbage`` tasks that return non-JSON."""

    def __init__(self, model: str = LABELER, fail: tuple = (), garbage: tuple = (), malformed: bool = False) -> None:
        self.model = model
        self.fail, self.garbage, self.malformed = fail, garbage, malformed
        self.prompts: List[str] = []
        self.image_calls: List[int] = []  # number of images attached per chat_with_images call

    def chat_with_images(self, prompt: str, images) -> str:
        self.image_calls.append(len(list(images)))
        return self.chat(prompt)

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Task: list EVERY value" in prompt:
            task, answer = "values", VALUES_ANSWER
        elif "Task: for every figure" in prompt:
            task, answer = "figures", FIGURES_ANSWER
        else:
            task, answer = "context", CONTEXT_ANSWER
        if task in self.fail:
            raise TimeoutError("provider timed out (Bearer sk-shouldneverleak123)")
        if task in self.garbage:
            return "I could not do this."
        if task == "values" and self.malformed:
            answer = dict(answer, values=answer["values"] + ["not an object"])
        return "```json\n" + json.dumps(answer) + "\n```"


def fake_factory(**kw: Any) -> Callable[[str], FakeClient]:
    clients: List[FakeClient] = []

    def make(model: str) -> FakeClient:
        client = FakeClient(model, **kw)
        clients.append(client)
        return client

    make.clients = clients  # type: ignore[attr-defined]
    return make


def lister(*models: str) -> Callable[[], ModelListing]:
    return lambda: ModelListing("cborg", "live", tuple(sorted(models or (LABELER, JUDGE, MUT))))


def unavailable_lister(error: str = "no CBORG API key configured") -> Callable[[], ModelListing]:
    return lambda: ModelListing("cborg", "unavailable", error=error)
