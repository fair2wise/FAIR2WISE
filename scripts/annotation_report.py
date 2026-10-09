#!/usr/bin/env python3
"""Join human labels with turn summaries for one eval batch.

Usage: python scripts/annotation_report.py --batch-id <name>

Reads ``annotations.jsonl`` and every ``turns.jsonl`` under ``F2W_TRACE_DIR``
(default ``runs/traces``). Pass rate uses the latest label per turn. Tag
counts and the fail listing use every fail annotation.

The disagreement table re-scores each labeled turn with the batch case file
and lists turns whose auto-score and human verdict differ.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from scripts.run_eval_batch import score_case

ROOT = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _turns(root: Path, batch_id: str) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.glob("*/turns.jsonl")):
        for row in _read_jsonl(path):
            if row.get("batch_id") != batch_id:
                continue
            turn_id = str(row.get("turn_id") or "")
            if turn_id:
                found[turn_id] = row
    return found


def _latest(annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(annotations, key=lambda row: str(row.get("ts") or ""))


def _detail(root: Path, turn_id: str) -> Optional[Dict[str, Any]]:
    matches = sorted(root.glob(f"*/turns/{turn_id}.json"))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_cases_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    found: Dict[str, Dict[str, Any]] = {}
    for raw in document.get("cases") or []:
        case_id = str(raw.get("id") or "")
        if case_id:
            found[case_id] = raw
    return found


def _case_for_turn(turn: Dict[str, Any], detail: Optional[Dict[str, Any]], cases_by_id: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    request = (detail or {}).get("request") if isinstance(detail, dict) else {}
    ui = request.get("ui_settings") if isinstance(request, dict) and isinstance(request.get("ui_settings"), dict) else {}
    case_id = str(ui.get("case_id") or "")
    if case_id and case_id in cases_by_id:
        return cases_by_id[case_id]
    question = str(turn.get("question") or "")
    matches = [case for case in cases_by_id.values() if str(case.get("question") or "") == question]
    if len(matches) == 1:
        return matches[0]
    return None


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def disagreement_rows(
    turns: Dict[str, Dict[str, Any]],
    labeled: Dict[str, Dict[str, Any]],
    details: Dict[str, Dict[str, Any]],
    cases_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Turns whose re-scored auto verdict and the latest human verdict differ."""
    rows: List[Dict[str, str]] = []
    for turn_id, label in labeled.items():
        human = str(label.get("verdict") or "")
        if human not in {"pass", "fail"}:
            continue
        detail = details.get(turn_id) or {}
        case = _case_for_turn(turns[turn_id], detail, cases_by_id)
        if case is None:
            continue
        request = detail.get("request") if isinstance(detail.get("request"), dict) else {}
        retrieval = detail.get("retrieval") if isinstance(detail.get("retrieval"), dict) else {}
        response = detail.get("response") if isinstance(detail.get("response"), dict) else {}
        graph_ids: List[str] = []
        for hit in retrieval.get("selected_hits") or []:
            if not isinstance(hit, dict):
                continue
            graph_id = str(hit.get("graph_id") or "").strip()
            if graph_id and graph_id not in graph_ids:
                graph_ids.append(graph_id)
        live = retrieval.get("live_tiled") if isinstance(retrieval.get("live_tiled"), dict) else {}
        problems = score_case(
            case,
            str(response.get("answer") or ""),
            graph_ids=graph_ids,
            live_tiled_on=bool(request.get("use_live_tiled")),
            live_tiled_status=str(live.get("status") or "") or None,
        )
        auto = "fail" if problems else "pass"
        if auto == human:
            continue
        rows.append({
            "turn_id": turn_id,
            "case_id": str(case.get("id") or ""),
            "question": str(turns[turn_id].get("question") or ""),
            "auto": auto,
            "label": human,
            "rule": "; ".join(problems) if problems else "(none)",
            "note": str(label.get("note") or ""),
            "tags": ", ".join(str(tag) for tag in (label.get("failure_tags") or []) if str(tag).strip()),
        })
    rows.sort(key=lambda row: row["case_id"])
    return rows


def render(
    batch_id: str,
    turns: Dict[str, Dict[str, Any]],
    annotations: List[Dict[str, Any]],
    *,
    disagreements: Optional[List[Dict[str, str]]] = None,
) -> str:
    by_turn: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in annotations:
        turn_id = str(row.get("turn_id") or "")
        if turn_id in turns:
            by_turn[turn_id].append(row)

    labeled = {turn_id: _latest(rows) for turn_id, rows in by_turn.items()}
    passed = sum(1 for row in labeled.values() if row.get("verdict") == "pass")
    failed = sum(1 for row in labeled.values() if row.get("verdict") == "fail")
    unlabeled = len(turns) - len(labeled)
    rate = (passed / len(labeled)) if labeled else 0.0

    categories: Dict[str, List[str]] = defaultdict(list)
    for turn_id, turn in turns.items():
        categories[str(turn.get("category") or "uncategorized")].append(turn_id)

    lines = [
        f"# Annotation report: {batch_id}",
        "",
        f"Turns in batch: {len(turns)}",
        f"Labeled: {len(labeled)}  unlabeled: {unlabeled}",
        f"Overall pass rate (labeled): {passed}/{len(labeled)} ({rate:.0%})",
        "",
        "## By category",
        "",
        "| category | labeled | pass | fail | unlabeled | pass rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category in sorted(categories):
        ids = categories[category]
        cat_labeled = [labeled[turn_id] for turn_id in ids if turn_id in labeled]
        cat_pass = sum(1 for row in cat_labeled if row.get("verdict") == "pass")
        cat_fail = sum(1 for row in cat_labeled if row.get("verdict") == "fail")
        cat_unlabeled = len(ids) - len(cat_labeled)
        cat_rate = (cat_pass / len(cat_labeled)) if cat_labeled else 0.0
        lines.append(
            f"| {category} | {len(cat_labeled)} | {cat_pass} | {cat_fail} | {cat_unlabeled} | {cat_rate:.0%} |"
        )

    tag_counts: Counter[str] = Counter()
    fails_by_tag: Dict[str, List[str]] = defaultdict(list)
    for row in annotations:
        if row.get("verdict") != "fail":
            continue
        turn_id = str(row.get("turn_id") or "")
        if turn_id not in turns:
            continue
        tags = [str(tag) for tag in (row.get("failure_tags") or []) if str(tag).strip()]
        if not tags:
            tags = ["(untagged)"]
        question = str(turns[turn_id].get("question") or "")
        for tag in tags:
            tag_counts[tag] += 1
            fails_by_tag[tag].append(f"- `{turn_id}` — {question}")

    lines.extend(["", "## Tag counts", "", "| tag | count |", "| --- | ---: |"])
    if tag_counts:
        for tag, count in tag_counts.most_common():
            lines.append(f"| {tag} | {count} |")
    else:
        lines.append("| (none) | 0 |")

    lines.extend(["", "## Fails by tag", ""])
    if not fails_by_tag:
        lines.append("No fail labels.")
    for tag in sorted(fails_by_tag):
        lines.append(f"### {tag}")
        lines.append("")
        lines.extend(fails_by_tag[tag])
        lines.append("")

    lines.extend(["", "## Auto-score disagreements", ""])
    if disagreements is None:
        lines.append("Auto-scores were not joined.")
    elif not disagreements:
        lines.append("No disagreements. Every labeled turn matches the auto-score, or no labeled turn could be re-scored.")
    else:
        lines.append("| case | turn_id | auto | label | rule | note | question |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in disagreements:
            lines.append(
                "| {case_id} | `{turn_id}` | {auto} | {label} | {rule} | {note} | {question} |".format(
                    case_id=_cell(row.get("case_id")),
                    turn_id=_cell(row.get("turn_id")),
                    auto=_cell(row.get("auto")),
                    label=_cell(row.get("label")),
                    rule=_cell(row.get("rule")),
                    note=_cell(row.get("note")),
                    question=_cell(row.get("question")),
                )
            )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pass-rate report for one trace batch.")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--trace-dir", type=Path, default=None)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals/cases/batch1.yaml")
    args = parser.parse_args()
    root = args.trace_dir or Path(os.environ.get("F2W_TRACE_DIR") or "runs/traces")
    turns = _turns(root, args.batch_id)
    annotations = list(_read_jsonl(root / "annotations.jsonl"))
    by_turn: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in annotations:
        turn_id = str(row.get("turn_id") or "")
        if turn_id in turns:
            by_turn[turn_id].append(row)
    labeled = {turn_id: _latest(rows) for turn_id, rows in by_turn.items()}
    details = {turn_id: detail for turn_id in labeled if (detail := _detail(root, turn_id))}
    disagreements = disagreement_rows(turns, labeled, details, load_cases_by_id(args.cases))
    sys_out = render(args.batch_id, turns, annotations, disagreements=disagreements)
    print(sys_out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
