"""A small library of generated PDFs and a KG bundle with known terms, for the ``library_passages`` tests."""
from __future__ import annotations

import json
from pathlib import Path
import textwrap
from typing import Any

from app.modules.jobs import JobService, JobStore
from app.modules.library import library_inputs, register_library_jobs
from app.modules.library import bundles as B
from app.modules.library.handlers import library_handlers
from app.modules.library.passages_job import LibraryPassages
from app.modules.library.probe import ProbeResult
from app.modules.library.schemas import list_schemas
from app.modules.library.service import LibraryService
from tests.security.security_fakes import local_settings

SCHEMA = {"id": sorted(c["name"] for c in list_schemas())[0], "version": "0", "sha256": "abc", "path": "x.yaml"}

#: Known sentences: each is at least 120 characters, so it stands alone as a passage.
S_PURITY = ("Resonant soft X-ray scattering measures the average domain size and the domain purity of a polymer blend "
            "film at the carbon edge of the sample.")
S_ORIENT = ("The grazing incidence wide angle X-ray scattering patterns show that the polymer chains adopt a face-on "
            "orientation with respect to the silicon substrate.")
S_PURITY_B = ("In the second paper the domain purity was obtained from the integrated scattering intensity of the "
              "same blend after thermal annealing at 150 degrees.")
S_DUP = ("The duplicated file reports a calibration of the detector distance that is quoted here only to give the "
         "locator a sentence of sufficient length to find.")

#: Two adjacent sentences that name one endpoint each (relation ellipsometer -> silicon reference).
S_ELLIPSO = ("The film thickness was measured with an ellipsometer at several positions across every sample that was "
             "prepared for this study.")
S_REFERENCE = ("A calibration of the instrument was carried out against a silicon reference before every measurement "
               "session of the campaign.")

PAPERS = {
    "a.pdf": [[S_PURITY, "Filler words follow the sentence on the first page of this generated paper."], [S_ORIENT]],
    "b.pdf": [[S_PURITY_B, S_ELLIPSO + " " + S_REFERENCE]],
    "blank.pdf": [[]],  # no text layer
}


def make_pdf(path: Path, pages: list[list[str]]) -> None:
    """Write a PDF whose pages hold the given paragraphs (wrapped over lines, as a real PDF breaks them)."""
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for paragraphs in pages:
        y = 740
        for paragraph in paragraphs:
            for line in textwrap.wrap(paragraph, 80):
                c.setFont("Helvetica", 10)
                c.drawString(50, y, line)
                y -= 14
            y -= 8
        c.showPage()
    c.save()


def term(name: str, *papers: str, snippets: list[dict[str, Any]] | None = None,
         relations: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    return {"term": name, "definition": f"{name} definition", "category": "Thing", "pages": [], "source_papers": list(papers),
            "context_snippets": list(snippets or []),
            "relations": [{"relation": r, "related_term": o, "verified": True} for r, o in relations or []]}


class World:
    """A library folder with generated PDFs, a bundle ``kg1`` over them and the job service with the recovery handler."""

    def __init__(self, tmp: Path, *, extra_terms: list[dict[str, Any]] | None = None, clock: Any = None) -> None:
        self.tmp = tmp
        self.lib = tmp / "papers"
        self.lib.mkdir()
        for name, pages in PAPERS.items():
            make_pdf(self.lib / name, pages)
        self.service = LibraryService(self.lib, tmp / "state" / "library" / "catalog.sqlite", settings=local_settings(),
                                      probe=lambda p: ProbeResult(status="ok", pages=1, page_text="x"))
        self.service.refresh(max_probe=0)
        self.store = B.BundleStore(tmp / "state" / "kgs")
        self.recovery = LibraryPassages(self.service, self.store)
        settings = local_settings()
        kwargs = {"lease_seconds": 60, "clock": clock} if clock else {}
        self.jobs = JobService(JobStore(tmp / "jobs.sqlite3", **kwargs), settings=settings)
        register_library_jobs(self.jobs, settings, handlers=library_handlers(self.service, passages=self.recovery))
        terms = [term("domain purity", "a.pdf", "b.pdf", relations=[
                     ("measured_by", "resonant soft X-ray scattering"), ("derived_from", "integrated scattering intensity"),
                     ("related_to", "unfindable partner")]),
                 term("face-on orientation", "a.pdf", relations=[
                     ("measured_by", "grazing incidence wide angle X-ray scattering"), ("related_to", "carbon edge")]),
                 term("unfindable notion", "a.pdf"), term("blank page term", "blank.pdf"),
                 term("gone term", "gone.pdf"),
                 term("ellipsometer", "b.pdf", relations=[("calibrated_with", "silicon reference")]),
                 *(extra_terms or [])]
        self.store.create("kg1", schema=SCHEMA, model="m", backend="cborg",
                          papers=[B.PaperRef(n, self.sha(n), 1) for n in ("a.pdf", "b.pdf", "blank.pdf")],
                          terms={"metadata": {"processed_files": 3}, "terms": terms, "code_snippets": []})

    def sha(self, name: str) -> str:
        return next(r["sha256"] for r in self.service.catalog.rows() if r["relpath"].endswith(name))

    def bundle(self) -> B.Bundle:
        return self.store.get("kg1")

    def terms(self) -> dict[str, dict[str, Any]]:
        data = json.loads(self.bundle().file(B.TERMS_FILE).read_text())
        return {t["term"]: t for t in data["terms"]}

    def submit(self, nonce: str = "1") -> Any:
        return self.jobs.submit(self.service.worker_context("s"), "library_passages",
                                library_inputs({"bundle": "kg1"}, nonce=nonce, corpus_scope=("kg1",)))

    def run(self, nonce: str = "1") -> tuple[Any, Any]:
        handle = self.submit(nonce)
        job_id, state = self.jobs.work_one("w")
        assert job_id == handle.job_id
        return handle, state

    def units(self, handle: Any) -> dict[str, Any]:
        rows = self.jobs.store.unit_rows(handle.job_id, "run", limit=5000)
        return {r["unit_id"]: r for r in rows}
