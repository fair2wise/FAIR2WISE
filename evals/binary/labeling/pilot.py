"""The owner-chosen pilot papers (H3 B4: "All three") and which cases rest on them.

Candidates A, B and C of ``docs/migration/reviews/H3/pilot_candidates.md``. Only
identifiers live here: relative file names under the private ``papers/`` root, DOIs, the
SHA-256 of the exact PDFs that were reviewed (so the tool refuses a different file under the
same name), and what to look for. No paper text, value or answer is stored in the repo.

``CASE_PAPERS`` names the pilot papers each of the 13 label-dependent cases needs. The
rule is ``all`` (every listed paper must be labelled) except LOCAL-02, which needs an
isolated end-to-end run on one real paper, so any one labelled paper serves it (``any``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Tuple

#: Value tags of H3 F2 ("capture all values and tag with reported, cited, derived, assumed").
VALUE_TAGS = ("reported", "cited", "derived", "assumed")

TASK_VALUES = "values"
TASK_FIGURES = "figures"
TASK_CONTEXT = "context"  # absent quantities, gold questions and Work/version identity
ALL_TASKS = (TASK_VALUES, TASK_FIGURES, TASK_CONTEXT)


@dataclass(frozen=True)
class PilotVersion:
    """One PDF of a pilot Work.

    Attributes:
        key: Version key used in label ids (``published``, ``preprint``, ``repository_copy``).
        path: File name relative to the papers root.
        sha256: SHA-256 of the reviewed PDF bytes.
        tasks: Labelling tasks run on this version.
        gold_questions: Whether the gold questions are asked against this version.
        page_note: Locator hint (cover pages, page offsets).
    """

    key: str
    path: str
    sha256: str
    tasks: Tuple[str, ...] = ALL_TASKS
    gold_questions: bool = True
    page_note: str = ""


@dataclass(frozen=True)
class PilotPaper:
    """One pilot Work and what its labels must cover."""

    pilot_id: str
    doi: str
    short_title: str
    versions: Tuple[PilotVersion, ...]
    cases: Tuple[str, ...]
    focus: Tuple[str, ...]
    absent_checklist: Tuple[str, ...]


PILOT_PAPERS: Dict[str, PilotPaper] = {
    "A": PilotPaper(
        pilot_id="A",
        doi="10.1021/acs.macromol.5b00950",
        short_title="Porous block copolymer electrolyte membranes (Macromolecules 2015)",
        versions=(
            PilotVersion(
                key="repository_copy",
                path="rsoxs/2015/10.1021_acs.macromol.5b00950.pdf",
                sha256="b366d8fd53e8f483a70e6c27e426a8225e31bbba957977e8e54f5953b94d6f72",
                page_note=(
                    "PDF page 1 is a repository cover page; PDF page N is journal page 5646+N. Give PDF page"
                    " numbers in locators."
                ),
            ),
        ),
        cases=("EXT-01", "EXT-02", "EXT-03", "EXT-04", "FIG-01", "FIG-02", "FIG-03", "QA-01", "QA-02", "QA-03",
               "QA-04", "OPS-04", "LOCAL-02"),
        focus=(
            "Sample records and the RSoXS measurement record (beamline, photon energies, geometry, analysis software)",
            "Every value in Table 1 and Table 2 (one label per cell)",
            "Scattering peak positions in q and the d-spacings derived from them, with the derivation",
            "Figures with q axes, energy axes, scale bars stated in images or captions, and two-y-axis plots",
            "Values taken from cited references and literature constants, tagged cited or assumed",
        ),
        absent_checklist=(
            "sample-to-detector distance", "X-ray polarization", "exposure time",
            "a d-spacing for the sulfonated samples", "pore size in the sulfonated samples",
            "any photon energy other than the ones the paper states",
        ),
    ),
    "B": PilotPaper(
        pilot_id="B",
        doi="10.1038/s41598-018-31024-1",
        short_title="Cellulose microfibril spacing by RSoXS (Scientific Reports 2018)",
        versions=(
            PilotVersion(
                key="published",
                path="rsoxs/2018/10.1038_s41598-018-31024-1.pdf",
                sha256="4579d62efb791f2760f02006e0935adafc777107ac0a9536f2de9b4e42c44ece",
            ),
        ),
        cases=("EXT-01", "EXT-02", "EXT-03", "EXT-04", "FIG-01", "FIG-02", "FIG-03", "QA-01", "QA-03", "QA-04",
               "OPS-04", "LOCAL-02"),
        focus=(
            "Sample treatments and the measurement records (RSoXS, NEXAFS, AFM) with instrument settings",
            "q values in inverse angstrom and the length scales derived from them",
            "Figure panels with q axes, AFM scale bars and colour bars",
            "Statements where the paper says its own data are not accurate or a feature is weak",
            "Values reported by other works, kept apart from this paper's own values",
        ),
        absent_checklist=(
            "absolute scattering intensity calibration", "X-ray polarization", "exposure time",
            "the publication date (check whether the PDF prints a real date)",
        ),
    ),
    "C": PilotPaper(
        pilot_id="C",
        doi="10.1107/S1600576723002790",
        short_title="CyRSoXS virtual instrument, published and preprint (J. Appl. Cryst. 2023 / arXiv 2209.13121)",
        versions=(
            PilotVersion(
                key="published",
                path="rsoxs/2023/10.1107_s1600576723002790.pdf",
                sha256="6c18aa2ad188bf2b49e8d3547d2262c0669f168498c145ae7a5408aa8cb05710",
            ),
            PilotVersion(
                key="preprint",
                path="rsoxs/2022/10.48550_arXiv.2209.13121v1.pdf",
                sha256="1307cec5a08f9171075e033947086d59d8416068d5677c09f2aea46359891efb",
                tasks=(TASK_VALUES, TASK_CONTEXT),
                gold_questions=False,
                page_note="Single-column arXiv preprint; table and figure numbers differ from the published version.",
            ),
        ),
        cases=("EXT-02", "EXT-03", "EXT-04", "FIG-01", "FIG-02", "FIG-03", "QA-01", "QA-03", "OPS-04", "LOCAL-02"),
        focus=(
            "Values that appear in both versions, each with its own locator (EXT-04)",
            "Values present in only one version",
            "Units that plain-text extraction may have corrupted (a micro sign read as m, lost minus signs)",
            "Simulation parameters, benchmark timings and memory figures, tagged reported, cited or derived",
            "Figure axes in q with and without stated units",
        ),
        absent_checklist=(
            "any benchmark timing that appears in only the other version",
            "physical sample or measurement records (this is a software paper)",
        ),
    ),
}

ALL_RULE = "all"
ANY_RULE = "any"


def _case_papers() -> Dict[str, Dict[str, object]]:
    out: Dict[str, List[str]] = {}
    for paper in PILOT_PAPERS.values():
        for cid in paper.cases:
            out.setdefault(cid, []).append(paper.pilot_id)
    return {cid: {"papers": sorted(ids), "rule": ANY_RULE if cid == "LOCAL-02" else ALL_RULE}
            for cid, ids in sorted(out.items())}


#: ``case_id -> {"papers": [...], "rule": "all"|"any"}`` for the 13 label-dependent cases.
CASE_PAPERS: Mapping[str, Mapping[str, object]] = _case_papers()

#: The 13 cases that need pilot labels (H3: "13 cases needing real-paper fixtures").
LABEL_CASES: Tuple[str, ...] = tuple(CASE_PAPERS)

assert len(LABEL_CASES) == 13, LABEL_CASES


def paper(pilot_id: str) -> PilotPaper:
    """The pilot paper ``A``, ``B`` or ``C`` (case-insensitive)."""
    key = pilot_id.strip().upper()
    if key not in PILOT_PAPERS:
        raise KeyError(f"unknown pilot paper {pilot_id!r}; expected one of {sorted(PILOT_PAPERS)}")
    return PILOT_PAPERS[key]


__all__ = ("ALL_RULE", "ALL_TASKS", "ANY_RULE", "CASE_PAPERS", "LABEL_CASES", "PILOT_PAPERS", "PilotPaper",
           "PilotVersion", "TASK_CONTEXT", "TASK_FIGURES", "TASK_VALUES", "VALUE_TAGS", "paper")
