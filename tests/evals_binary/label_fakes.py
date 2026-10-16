"""Synthetic pilot paper, fake labelling client and fake model lister (no network, no key, no real paper).

The PDF is generated in ``tmp_path`` with PyMuPDF from invented sentences; nothing from a
real paper is used or committed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, List, Optional

from app.modules.model_catalog import ModelListing
from evals.binary.labeling import pilot as P
from evals.binary.labeling.parse import file_sha256

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


def make_pdf(path: Path, pages: Optional[List[List[str]]] = None) -> Path:
    """Write a small text-only PDF with the synthetic sentences."""
    import fitz

    doc = fitz.open()
    for lines in pages or [PAGE_1, PAGE_2]:
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 18
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def synthetic_paper(pdf_rel: str, sha: str, pilot_id: str = "A") -> P.PilotPaper:
    """A pilot registry entry pointing at the synthetic PDF."""
    return P.PilotPaper(
        pilot_id=pilot_id, doi="10.0000/synthetic", short_title="Synthetic paper",
        versions=(P.PilotVersion(key="published", path=pdf_rel, sha256=sha),),
        cases=P.PILOT_PAPERS[pilot_id].cases, focus=("synthetic focus",), absent_checklist=("exposure time",),
    )


def install_synthetic(monkeypatch: Any, root: Path, pilot_id: str = "A") -> P.PilotPaper:
    """Generate the PDF under ``root/papers`` and swap it into the pilot registry."""
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
FIGURES_ANSWER = {"figure_readings": [], "figure_totals": []}
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
