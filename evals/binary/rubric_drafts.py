"""DRAFT binary rubrics for H3 (not approved; ``approval_status`` stays ``draft``).

A rubric is a list of yes/no criteria. The judge answers each criterion and the verdict is
PASS only when every criterion is met (see ``assertions/rubric.py``). Criteria avoid
numeric thresholds on purpose: the owner sets any threshold at H3, and agents may not
introduce a score that substitutes for PASS/FAIL.
"""

from __future__ import annotations

from typing import Any, Dict, List

COMMON_RULES = [
    (
        "The judge sees only the case's frozen fixture, the system output and the cited source excerpts the case"
        " authorizes."
    ),
    "The judge may only answer the listed criteria; free-form grading is not accepted.",
    "A malformed, partial or UNRESOLVED judgment is judge_invalid and blocks the case; it is never PASS.",
    (
        "The judge never sees or reproduces full source papers or original crops; the report carries IDs and"
        " locators only."
    ),
    "A human owner resolves any disputed label; evaluation agents cannot approve their own gold.",
]


def _rubric(
    rubric_id: str, applies_to: List[str], title: str, criteria: List[Dict[str, str]], open_questions: List[str]
) -> Dict[str, Any]:
    return {
        "rubric_id": rubric_id,
        "title": title,
        "applies_to": applies_to,
        "approval_status": "draft",
        "approval_ref": None,
        "judge_output_schema": {
            "verdict": "PASS|FAIL",
            "criteria": [
                {"id": "<criterion id>", "met": "true|false", "evidence": "<locator or id, no source body>"}
            ],
        },
        "verdict_rule": "PASS iff every criterion is met; otherwise FAIL",
        "common_rules": COMMON_RULES,
        "criteria": criteria,
        "open_questions_for_h3": open_questions,
    }


RUBRICS: List[Dict[str, Any]] = [
    _rubric(
        "RUB-EXT",
        ["EXT-01", "EXT-02", "EXT-03"],
        "Extraction against an owner-approved gold record",
        [
            {
                "id": "EXT-R1",
                "text": (
                    "Every record the gold marks as expected is present with the approved schema class and"
                    " required fields."
                ),
            },
            {
                "id": "EXT-R2",
                "text": (
                    "Each reported value equals the gold value after the owner-approved normalization (raw value,"
                    " raw unit and normalized unit all agree)."
                ),
            },
            {
                "id": "EXT-R3",
                "text": (
                    "Where the gold marks a value as absent from the source, the output leaves it absent and does"
                    " not infer, default or fabricate it."
                ),
            },
            {
                "id": "EXT-R4",
                "text": (
                    "Every retained value cites a source Work/version and a locator that resolves to the quoted"
                    " evidence."
                ),
            },
            {
                "id": "EXT-R5",
                "text": "No record or relation outside the approved classes/relations is emitted as accepted.",
            },
        ],
        [
            (
                "Which Works and which gold records form the representative fixture set (the plan proposes about"
                " 12 Work families)?"
            ),
            (
                "Are acceptable alternative unit spellings and equivalent numeric forms listed per field, or does"
                " the deterministic normalizer decide?"
            ),
            (
                "Is a record-level miss a case FAIL, or are partially complete records allowed for a named subset"
                " of fields?"
            ),
            (
                "Which model/settings revision is evaluated, and what is the repeat policy for live extraction"
                " (declared before any run)?"
            ),
        ],
    ),
    _rubric(
        "RUB-QA",
        ["QA-01", "QA-03", "QA-04"],
        "Chat answers against the existing F2W evidence rules",
        [
            {
                "id": "QA-R1",
                "text": (
                    "Every factual claim in the answer is supported by a cited evidence item or KG node that was"
                    " retrieved for the turn."
                ),
            },
            {
                "id": "QA-R2",
                "text": (
                    "Each citation token resolves to retrieved evidence within the eligible sources and"
                    " active-paper scope."
                ),
            },
            {
                "id": "QA-R3",
                "text": (
                    "Every numerical claim (value with unit or quantity) is entailed by a cited source excerpt;"
                    " none is invented or rounded beyond the source."
                ),
            },
            {
                "id": "QA-R4",
                "text": (
                    "A conceptual question with insufficient direct evidence follows the existing leeway/fallback"
                    " behaviour and labels general explanation as such."
                ),
            },
            {
                "id": "QA-R5",
                "text": (
                    "When evidence is absent the answer states that explicitly or offers the existing"
                    " ingest/approval path; it does not forbid or force a refusal beyond the existing rules."
                ),
            },
            {
                "id": "QA-R6",
                "text": (
                    "Exact quotations in the answer are verbatim and relevant, and are labelled as quotations only"
                    " when verbatim."
                ),
            },
        ],
        [
            (
                "Which gold questions (conceptual, numeric, off-topic, weak-retrieval) and which graphs/papers"
                " form the fixture set?"
            ),
            (
                "Is lexical source support (a screen already implemented) enough for numerical claims, or must"
                " entailment always be judged?"
            ),
            "How are acceptable alternative phrasings and unit forms frozen per question?",
            "Which provider/model judges, and is a second reviewer required for disputed verdicts?",
        ],
    ),
    _rubric(
        "RUB-FIG",
        ["FIG-01", "FIG-02"],
        "Figure, panel and axis reading against owner-labelled gold",
        [
            {
                "id": "FIG-R1",
                "text": "The reported figure and panel match the gold figure/panel for the cited reading.",
            },
            {
                "id": "FIG-R2",
                "text": (
                    "Reported coordinates or panel geometry fall inside the gold region (frame convention stated)."
                ),
            },
            {
                "id": "FIG-R3",
                "text": (
                    "The scale bar, q-axis range and unit equal the source-supported gold, with any Angstrom/nm"
                    " conversion done by the deterministic helper."
                ),
            },
            {"id": "FIG-R4", "text": "A reading the gold marks as ambiguous is withheld rather than reported."},
            {"id": "FIG-R5", "text": "The result carries panel locators and no image bytes, paths or URLs."},
        ],
        [
            "Which authorized papers and which figures/panels are in the gold set, and who labels them?",
            "What coordinate tolerance (if any) defines 'inside the gold region'? The agent proposes none.",
            (
                "Are OCR-derived units ever acceptable, or only text-layer units (current implementation never"
                " believes OCR units)?"
            ),
        ],
    ),
]
