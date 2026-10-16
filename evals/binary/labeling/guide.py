"""Versioned labelling guide and prompts for the pilot papers (H3 B4).

The guide is derived from the 13 label-dependent cases (EXT-01..04, FIG-01..03,
QA-01..04, OPS-04, LOCAL-02), the H3 decisions F2 (every value captured and tagged
reported/cited/derived/assumed) and F8 (quote located, number and unit in the quote, unit
consistent with the quantity), and the draft rubrics RUB-EXT, RUB-FIG and RUB-QA. Any edit
to the text below must bump :data:`GUIDE_VERSION`; every label records the version and the
SHA-256 of the prompt template it was produced with.

The prompts carry the parsed paper text to the labelling model. The rendered prompts are
never written to disk or to a report; only their SHA-256 is recorded.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Sequence

from evals.binary.model import sha256_text
from evals.binary.labeling.pilot import TASK_CONTEXT, TASK_FIGURES, TASK_VALUES, VALUE_TAGS, PilotPaper, PilotVersion

GUIDE_VERSION = "f2w-pilot-label-guide/1"
#: SHA-256 of :func:`template_text` for this version. A test pins it: editing the guide, the
#: schemas or the task text without bumping :data:`GUIDE_VERSION` and this value fails.
GUIDE_TEMPLATE_SHA256 = "f400575465334ba3d5771da2999e7c4863b07169413fd1b916cf9fba6bc06d07"

GUIDE = """\
# FAIR2WISE pilot labelling guide (version 1)

You are labelling one scientific paper so that later tests can check an extraction and
chat system against your labels. A person will review every label you produce before it
is used as gold. Be complete and literal. Never guess.

## Rules for every label
1. Every label carries a `quote`: an exact, contiguous span copied from the page text you
   were given (same words, same numbers, same symbols). Do not paraphrase, do not join text
   from two places, do not fix typos or extraction errors. If the only support is a figure
   image you cannot see, say so with `needs_visual_check: true` and quote the caption.
2. `page` is the PDF page number given in the `=== PAGE n ===` markers, not the printed
   journal page number.
3. Copy the value and unit exactly as printed (`value`, `unit`). Put a normalized unit in
   `normalized_unit` only when the conversion is unambiguous; otherwise null.
4. If the extracted text looks corrupted (for example a micro sign turned into "m", a lost
   minus sign or superscript), copy it as printed and add a note under `notes`.
5. Do not invent anything. If something is not stated, it belongs in `absent_quantities`,
   not in `values`.

## Value tags (H3 decision F2: capture every value and tag it)
- `reported`: this paper's own measurement or result.
- `cited`: a value the paper attributes to another work (a reference marker, "previously
  reported", "taken from"). A value in an attributing sentence is `cited` even if it
  reads like a result.
- `derived`: computed in this paper from other values (for example d = 2*pi/q, a ratio).
  Give the inputs and the formula in `derivation`.
- `assumed`: an assumption, literature constant, tabulated optical constant, nominal
  instrument value or modelling choice. Never present an assumed value as the paper's
  own measurement.

## What to label (the cases these labels serve)
- EXT-01 records: samples (SampleRecord), measurements (MeasurementRecord: technique,
  instrument, beamline, energies, geometry, conditions), instruments, materials.
- EXT-02 quantities: every number with a unit, with its raw unit and normalized unit.
  One label per table cell.
- EXT-03 absent information: quantities a reader would expect but the paper does not
  state, or states only in supplementary material that is not part of this PDF.
- EXT-04 Work/version/locator: the paper's identity (title, DOI, arXiv id, venue, year,
  version kind) and page locators for its values.
- FIG-01 figure, panel and coordinates: which figure and panel a reading comes from.
- FIG-02 axes and scale: axis quantity, unit and scale (linear/log), q-axis units
  (nm^-1 or angstrom^-1), scale bars printed in images or stated in captions.
- FIG-03 ambiguity: readings that are uncertain (overlapping peaks, shifted or
  arbitrary-unit curves, axes without units, regions the paper calls unreliable) are
  marked `ambiguous: true` with a `withhold_reason`. Do not give a number for them.
- QA-01 / QA-03 numeric questions, QA-02 active-paper questions whose answer must come
  from this paper and not from other sources, QA-04 conceptual questions answered by the
  paper's reasoning rather than a number.
- OPS-04 totals: the size of closed lists (rows x columns of a table, panels x curves).

## Output
Answer with one JSON object only, no prose before or after it, matching the schema in the
task. Use null for unknown fields. Never omit a required key.
"""

VALUES_SCHEMA: Dict[str, Any] = {
    "values": [{
        "quantity": "name of the quantity, e.g. lamellar spacing",
        "subject": "what it describes: sample, material, instrument or record",
        "record_type": "SampleRecord|MeasurementRecord|Instrument|Material|Other",
        "value": "as printed",
        "value_numeric": "number or null",
        "uncertainty": "as printed or null",
        "unit": "as printed or null",
        "normalized_unit": "string or null",
        "tag": "|".join(VALUE_TAGS),
        "derivation": "formula and inputs, for derived values, else null",
        "citation": "reference marker or cited work, for cited values, else null",
        "locator": {"page": "int", "section": "string or null", "figure": "e.g. Fig. 6b or null",
                    "table": "e.g. Table 2 or null", "quote": "exact span from the page text"},
        "notes": "string or null",
    }],
    "table_totals": [{"table": "Table n", "rows": "int", "columns": "int", "page": "int", "quote": "caption"}],
}

FIGURES_SCHEMA: Dict[str, Any] = {
    "figure_readings": [{
        "figure": "Fig. n", "panel": "a|b|...|null", "page": "int",
        "element": "x_axis|y_axis|second_y_axis|colour_bar|scale_bar|curve|peak|inset",
        "quantity": "string or null", "unit": "as printed or null", "scale": "linear|log|unknown",
        "range": "[low, high] as printed, or null", "reading": "as printed or read, or null",
        "ambiguous": "true|false", "withhold_reason": "string or null",
        "needs_visual_check": "true when the reading needs the image itself",
        "quote": "exact span from the caption or the figure's text layer",
    }],
    "figure_totals": [{"figure": "Fig. n", "panels": "int or null", "curves_per_panel": "int or null",
                       "page": "int", "quote": "caption span"}],
}

CONTEXT_SCHEMA: Dict[str, Any] = {
    "absent_quantities": [{
        "quantity": "what is not stated", "why_absent": "not stated | only in supplementary material | other",
        "trap": "a plausible but wrong value a system might invent, or null",
        "nearest_quote": {"page": "int or null", "quote": "closest exact span, or null"},
    }],
    "gold_questions": [{
        "question_id": "Q1..Q8", "case_ids": ["QA-01|QA-02|QA-03|QA-04"],
        "question": "string", "answer_type": "numeric|conceptual|scope",
        "expected_answer": "string", "acceptable_variants": ["string"], "forbidden_answers": ["string"],
        "evidence": [{"page": "int", "quote": "exact span"}],
    }],
    "work_identity": {
        "title": "string", "authors_first": "first author surname or null", "doi": "string or null",
        "arxiv_id": "string or null", "venue": "string or null", "year": "int or null",
        "version_kind": "published|preprint|repository_copy|unknown", "version_date": "as printed or null",
        "page_offset_note": "how PDF pages map to printed pages, or null",
        "evidence": [{"page": "int", "quote": "exact span"}],
    },
}

SCHEMAS = {TASK_VALUES: VALUES_SCHEMA, TASK_FIGURES: FIGURES_SCHEMA, TASK_CONTEXT: CONTEXT_SCHEMA}

TASK_INSTRUCTIONS = {
    TASK_VALUES: (
        "Task: list EVERY value with a unit, every dimensionless number that matters to the results, and every"
        " table cell on the pages below (pages {pages}). Tag each one. Also give the size of each table you see."
    ),
    TASK_FIGURES: (
        "Task: for every figure below, read the caption and the text found inside the figure area and list the"
        " panels, axes (quantity, unit, scale), scale bars, colour bars and any readable peak or curve positions."
        " Mark uncertain readings ambiguous with a reason instead of giving a number. Give the panel and curve"
        " counts per figure."
    ),
    TASK_CONTEXT: (
        "Task: (1) list quantities a reader would expect that this PDF does not state (check the list below and"
        " add others you notice); (2) {questions}; (3) give the Work/version identity of this PDF."
    ),
}

QUESTIONS_ON = (
    "write 5 to 8 gold questions with expected answers and exact evidence quotes: at least two numeric (QA-01,"
    " QA-03), one about this paper's own value where other papers report a different one (QA-02), and one"
    " conceptual 'why' question the paper answers without a number (QA-04)"
)
QUESTIONS_OFF = "return an empty gold_questions list for this version"


def template_text() -> str:
    """Everything in a prompt except the paper: guide, schemas and task instructions."""
    return GUIDE + "\n" + json.dumps({"schemas": SCHEMAS, "tasks": TASK_INSTRUCTIONS,
                                      "questions": [QUESTIONS_ON, QUESTIONS_OFF]}, sort_keys=True)


def template_sha256() -> str:
    """SHA-256 of :func:`template_text` (recorded with every label)."""
    return sha256_text(template_text())


def _paper_header(paper: PilotPaper, version: PilotVersion) -> str:
    lines = [f"Paper: pilot {paper.pilot_id}, {paper.short_title}", f"Version in this PDF: {version.key}"]
    if version.page_note:
        lines.append(f"Locator note: {version.page_note}")
    lines.append("Focus for this paper:")
    lines += [f"- {item}" for item in paper.focus]
    return "\n".join(lines)


def render_pages(pages: Sequence[Dict[str, Any]]) -> str:
    """``=== PAGE n ===`` blocks of page text."""
    return "\n\n".join(f"=== PAGE {p['page']} ===\n{p['text']}" for p in pages)


def render_figures(figures: Sequence[Dict[str, Any]]) -> str:
    """Figure blocks: id, page, caption and the text layer inside the figure."""
    blocks = []
    for fig in figures:
        spans = " | ".join(fig.get("text_layer") or []) or "(no text layer)"
        blocks.append(f"=== FIGURE {fig['figure_id']} (page {fig['page']}) ===\nCaption: {fig.get('caption') or ''}"
                      f"\nText inside the figure: {spans}")
    return "\n\n".join(blocks)


def build_prompt(task: str, paper: PilotPaper, version: PilotVersion, *, pages: Sequence[Dict[str, Any]] = (),
                 figures: Sequence[Dict[str, Any]] = (), page_range: str = "") -> str:
    """The full prompt for one call (contains private paper text: never persisted)."""
    instruction = TASK_INSTRUCTIONS[task]
    if task == TASK_VALUES:
        instruction = instruction.format(pages=page_range)
        body = render_pages(pages)
    elif task == TASK_FIGURES:
        body = render_figures(figures)
    else:
        instruction = instruction.format(questions=QUESTIONS_ON if version.gold_questions else QUESTIONS_OFF)
        checklist = "\n".join(f"- {item}" for item in paper.absent_checklist)
        body = f"Absent-quantity checklist (verify each):\n{checklist}\n\n" + render_pages(pages)
    return "\n\n".join([
        GUIDE,
        _paper_header(paper, version),
        instruction,
        "JSON schema of your answer:\n" + json.dumps(SCHEMAS[task], indent=1),
        body,
        "Answer with the JSON object only.",
    ])


__all__ = ("CONTEXT_SCHEMA", "FIGURES_SCHEMA", "GUIDE", "GUIDE_TEMPLATE_SHA256", "GUIDE_VERSION", "SCHEMAS",
           "VALUES_SCHEMA", "build_prompt", "render_figures", "render_pages", "template_sha256", "template_text")
