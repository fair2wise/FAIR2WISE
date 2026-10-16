"""Human review sheet for provisional labels, and promotion of reviewed labels to gold.

``review_sheet.csv`` has one row per label. The reviewer fills ``decision`` with
``accept``, ``correct`` or ``reject`` (blank means unreviewed), puts a JSON object of
replacement fields in ``corrected_json`` for ``correct`` (``{"value": "34.3", "locator":
{"quote": "..."}}``: objects merge, other values replace), and may add ``reviewer_note``.
``review_sheet.md`` shows the same rows for reading.

:func:`promote` turns the reviewed rows into ``gold.json``:

* an unreviewed row is refused (never promoted);
* ``accept`` of a label whose quote was not located is refused: correct the quote (it is
  checked again against the parsed text) or reject the label (H3 F8: quote located);
* a corrected quote that still cannot be located is refused;
* the reviewer's name is recorded and must not be a labelling model id;
* the gold is ``complete`` only when every label was reviewed and promoted or rejected and
  every labelling call succeeded. Otherwise it is written as ``gold_incomplete``, which the
  runner does not treat as gold.
"""

from __future__ import annotations

import copy
import csv
import datetime as _dt
import io
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from evals.binary.labeling.labeler import LOCATED, summarize_quotes
from evals.binary.labeling.parse import ParsedPaper
from evals.binary.labeling.quotes import check_quote, value_flags

SHEET_COLUMNS = ("label_id", "kind", "page", "summary", "quote", "quote_status", "flags", "decision",
                 "corrected_json", "reviewer_note")
DECISIONS = ("accept", "correct", "reject")
GOLD_FORMAT = "f2w-pilot-gold/1"


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def summary(item: Dict[str, Any]) -> str:
    """One-line description of a label for the review sheet."""
    d, kind = item["data"], item["kind"]
    if kind == "value":
        unc = f" ± {d['uncertainty']}" if d.get("uncertainty") else ""
        return (f"{_s(d.get('quantity'))} = {_s(d.get('value'))}{unc} {_s(d.get('unit'))} [{_s(d.get('tag'))}]"
                f" ({_s(d.get('subject'))}; {_s(d.get('record_type'))})").strip()
    if kind == "figure_reading":
        flag = " AMBIGUOUS: " + _s(d.get("withhold_reason")) if d.get("ambiguous") else ""
        return (f"{_s(d.get('figure'))}{_s(d.get('panel'))} {_s(d.get('element'))}: {_s(d.get('quantity'))}"
                f" [{_s(d.get('unit'))}] {_s(d.get('scale'))} {_s(d.get('range'))} {_s(d.get('reading'))}{flag}")
    if kind == "absent":
        trap = f"; trap: {d['trap']}" if d.get("trap") else ""
        return f"ABSENT: {_s(d.get('quantity'))} ({_s(d.get('why_absent'))}){trap}"
    if kind == "question":
        return (f"{_s(d.get('question_id'))} {_s(d.get('case_ids'))}: {_s(d.get('question'))} -> "
                f"{_s(d.get('expected_answer'))}")
    if kind == "identity":
        return (f"{_s(d.get('title'))} | doi {_s(d.get('doi'))} | arXiv {_s(d.get('arxiv_id'))} | "
                f"{_s(d.get('version_kind'))} {_s(d.get('year'))} | {_s(d.get('venue'))}")
    if kind == "table_total":
        return f"{_s(d.get('table'))}: {_s(d.get('rows'))} rows x {_s(d.get('columns'))} columns"
    if kind == "figure_total":
        return f"{_s(d.get('figure'))}: {_s(d.get('panels'))} panels, {_s(d.get('curves_per_panel'))} curves/panel"
    return json.dumps(d, sort_keys=True)[:200]


def _rows(label_docs: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    rows = []
    for doc in label_docs:
        for item in doc["items"]:
            first = item["quotes"][0] if item["quotes"] else {}
            rows.append({
                "label_id": item["label_id"], "kind": item["kind"], "page": _s(first.get("page")),
                "summary": summary(item), "quote": _s(first.get("quote")), "quote_status": item["quote_status"],
                "flags": " ".join(item["flags"]), "decision": "", "corrected_json": "", "reviewer_note": "",
            })
    return rows


def sheet_csv(label_docs: Sequence[Dict[str, Any]]) -> str:
    """CSV text of the review sheet (decision columns blank)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SHEET_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_rows(label_docs))
    return buf.getvalue()


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def sheet_markdown(label_docs: Sequence[Dict[str, Any]]) -> str:
    """Read-only markdown view of the review sheet (decisions go in the CSV)."""
    out = ["# Pilot label review sheet (private)", "",
           "Model labels are PROVISIONAL until a person reviews them (H3 B4). Record decisions in"
           " `review_sheet.csv` (accept / correct / reject), then run `python -m evals.binary.label promote`.", ""]
    for doc in label_docs:
        prov = doc["provenance"]
        out += [f"## Pilot {doc['pilot_id']} ({doc['version']})", "",
                f"- Labeler: `{prov['labeler_model']}`; guide `{prov['guide_version']}`; parser"
                f" `{prov['parser_revision']}`; source sha256 `{prov['source_sha256'][:16]}`",
                f"- Calls: {len(doc['calls'])}; missing: {len(doc['missing'])}"
                + "".join(f"\n  - MISSING {m['task']} {m['scope']}: {m['reason']}" for m in doc["missing"]),
                f"- Labels whose quote was not located: {doc['counts'].get('quote_not_located', 0)}", ""]
        out += ["| label | kind | page | label | quote check | flags |", "| --- | --- | --- | --- | --- | --- |"]
        for row in _rows([doc]):
            out.append(f"| {row['label_id']} | {row['kind']} | {row['page']} | {_md_cell(row['summary'])} |"
                       f" {row['quote_status']} | {row['flags']} |")
        out.append("")
    return "\n".join(out)


def read_sheet(path: Path) -> Dict[str, Dict[str, str]]:
    """Rows of a filled review sheet by ``label_id``."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return {row["label_id"]: row for row in csv.DictReader(handle) if row.get("label_id")}


def sheet_has_decisions(path: Path) -> bool:
    """Whether an existing sheet already carries any reviewer decision (so it must not be overwritten)."""
    if not Path(path).is_file():
        return False
    return any((r.get("decision") or "").strip() for r in read_sheet(path).values())


def _merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _quotes_of(kind: str, data: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    if kind == "value":
        loc = data.get("locator") if isinstance(data.get("locator"), dict) else {}
        return [(loc.get("page"), loc.get("quote"))]
    if kind in ("figure_reading", "figure_total", "table_total"):
        return [(data.get("page"), data.get("quote"))]
    if kind == "absent":
        near = data.get("nearest_quote") if isinstance(data.get("nearest_quote"), dict) else {}
        return [(near.get("page"), near.get("quote"))] if near.get("quote") else []
    rows = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    return [(r.get("page"), r.get("quote")) for r in rows if isinstance(r, dict)]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PromotionError(ValueError):
    """Promotion was refused as a whole (no reviewer, nothing reviewed)."""


def promote(label_docs: Sequence[Dict[str, Any]], sheet: Dict[str, Dict[str, str]], *, reviewer: str,
            parsed: Dict[str, ParsedPaper], now: Callable[[], str] = _now) -> Dict[str, Any]:
    """Build the gold document for one paper from its label documents and filled sheet.

    Args:
        label_docs: The paper's provisional label documents.
        sheet: Filled review rows by label id (:func:`read_sheet`).
        reviewer: The person who reviewed (recorded; must not be a labelling model id).
        parsed: Parsed papers by source sha256 (to re-check corrected quotes).

    Raises:
        PromotionError: No reviewer, a model id as reviewer, or no reviewed row at all.
    """
    name = (reviewer or "").strip()
    if not name:
        raise PromotionError("a reviewer name is required")
    models = {doc["provenance"]["labeler_model"] for doc in label_docs}
    if name in models:
        raise PromotionError("the reviewer must be a person, not the labelling model")
    if not label_docs:
        raise PromotionError("no provisional labels to promote")
    pilot = label_docs[0]["pilot_id"]
    promoted: List[Dict[str, Any]] = []
    rejected: List[str] = []
    refused: List[Dict[str, str]] = []
    reviewed_any = False
    for doc in label_docs:
        paper = parsed.get(doc["provenance"]["source_sha256"])
        for item in doc["items"]:
            row = sheet.get(item["label_id"]) or {}
            decision = (row.get("decision") or "").strip().lower()
            note = (row.get("reviewer_note") or "").strip()
            if not decision:
                refused.append({"label_id": item["label_id"], "reason": "unreviewed"})
                continue
            if decision not in DECISIONS:
                refused.append({"label_id": item["label_id"], "reason": f"unknown decision {decision!r}"})
                continue
            reviewed_any = True
            if decision == "reject":
                rejected.append(item["label_id"])
                continue
            data, quotes, status = item["data"], item["quotes"], item["quote_status"]
            if decision == "correct":
                try:
                    patch = json.loads(row.get("corrected_json") or "")
                except ValueError:
                    patch = None
                if not isinstance(patch, dict) or not patch:
                    refused.append({"label_id": item["label_id"], "reason": "correct needs a JSON object in"
                                    " corrected_json"})
                    continue
                data = _merge(data, patch)
                if paper is None:
                    refused.append({"label_id": item["label_id"], "reason": "parsed text unavailable to re-check"
                                    " the corrected label"})
                    continue
                quotes = [{"page": p, "quote": q, **check_quote(paper, q, p)} for p, q in _quotes_of(item["kind"],
                                                                                                     data)]
                status = summarize_quotes(quotes)
            if status not in LOCATED and status != "not_applicable":
                refused.append({"label_id": item["label_id"],
                                "reason": f"quote not located ({status}); correct the quote or reject the label"})
                continue
            flags = value_flags(data, quotes[0]["quote"] if quotes else None) if item["kind"] == "value" else []
            promoted.append({
                "label_id": item["label_id"], "kind": item["kind"], "data": data, "quotes": quotes,
                "quote_status": status, "flags": sorted(set(flags)), "source_sha256":
                doc["provenance"]["source_sha256"], "version": doc["version"],
                "review": {"decision": "accepted" if decision == "accept" else "corrected", "reviewer": name,
                           "note": note},
            })
    if not reviewed_any:
        raise PromotionError("no label has a review decision; nothing to promote")
    calls_ok = all(doc["complete"] for doc in label_docs)
    complete = calls_ok and not refused
    return {
        "label_format": GOLD_FORMAT,
        "status": "gold" if complete else "gold_incomplete",
        "complete": complete,
        "pilot_id": pilot,
        "doi": label_docs[0]["doi"],
        "reviewer": name,
        "reviewed_at": now(),
        "labeling_calls_complete": calls_ok,
        "provenance": [doc["provenance"] for doc in label_docs],
        "items": promoted,
        "rejected": rejected,
        "refused": refused,
        "counts": {"promoted": len(promoted), "rejected": len(rejected), "refused": len(refused)},
    }


__all__ = ("DECISIONS", "GOLD_FORMAT", "PromotionError", "SHEET_COLUMNS", "promote", "read_sheet",
           "sheet_csv", "sheet_has_decisions", "sheet_markdown", "summary")
