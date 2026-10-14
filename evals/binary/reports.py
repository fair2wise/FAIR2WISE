"""JSON, JUnit-compatible and Markdown reports for a binary run.

All three keep the verdict (PASS/FAIL) and the execution state apart. JUnit mapping:
PASS -> passing testcase; FAIL -> ``failure``; timeout/error/judge_invalid/missing_fixture
-> ``error`` (an infrastructure or fixture problem, not a content failure); skipped and
blocked -> ``skipped``. A skipped or errored required case is never a pass; the suite
property ``gate_status`` carries the binary gate and CI must read it.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from evals.binary.model import CASES_DIR
from evals.binary.sanitize import sanitize

_ERROR_STATES = ("timeout", "error", "judge_invalid", "missing_fixture")


def load_rubrics(cases_dir: Path = CASES_DIR) -> Dict[str, Dict[str, Any]]:
    """Load the draft rubrics keyed by ``rubric_id`` (empty when the file is absent)."""
    path = cases_dir / "rubrics.json"
    if not path.is_file():
        return {}
    return {r["rubric_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["rubrics"]}


def to_json(report: Dict[str, Any]) -> str:
    """Deterministic JSON text (sorted keys, trailing newline)."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def to_junit(report: Dict[str, Any]) -> str:
    """JUnit XML with one testsuite per family and the gate status as a property."""
    root = ET.Element("testsuites", name="binary-evaluation")
    props = ET.SubElement(root, "properties")
    for key, value in (
        ("gate_status", report["gate"]["status"]),
        ("manifest_sha256", report["manifest_sha256"]),
        ("runner_revision", report["runner_revision"]),
    ):
        ET.SubElement(props, "property", name=key, value=str(value))
    families: Dict[str, List[Dict[str, Any]]] = {}
    for row in report["results"]:
        families.setdefault(row["family"], []).append(row)
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for family, rows in sorted(families.items()):
        suite = ET.SubElement(root, "testsuite", name=family)
        counts = {"tests": len(rows), "failures": 0, "errors": 0, "skipped": 0}
        for row in sorted(rows, key=lambda r: r["case_id"]):
            case = ET.SubElement(suite, "testcase", classname=f"binary.{family}", name=row["case_id"])
            detail = sanitize(f"{row['execution_state']}: {row['actual']}", 600)
            if row["execution_state"] == "completed" and row["verdict"] == "FAIL":
                ET.SubElement(case, "failure", message=detail, type="binary_verdict_fail")
                counts["failures"] += 1
            elif row["execution_state"] in _ERROR_STATES:
                ET.SubElement(case, "error", message=detail, type=row["execution_state"])
                counts["errors"] += 1
            elif row["execution_state"] != "completed":
                ET.SubElement(case, "skipped", message=detail)
                counts["skipped"] += 1
        for key, value in counts.items():
            suite.set(key, str(value))
            totals[key] += value
    for key, value in totals.items():
        root.set(key, str(value))
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=False) + "\n"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def to_markdown(report: Dict[str, Any]) -> str:
    """Human-readable report listing every case that is not a complete, approved PASS."""
    gate, counts = report["gate"], report["counts"]
    out = [
        "# Binary evaluation report",
        "",
        f"**Gate status: {gate['status']}**",
        "",
        f"- Required cases: {gate['required_case_count']}; blocking the gate: {gate['blocking_case_count']}",
        f"- Gate reasons: {', '.join(gate['gate_reasons']) or 'none'}; human gates: "
        + ", ".join(f"{k}={v}" for k, v in sorted(gate["human_gates"].items())),
        (
            f"- Manifest: `{report['manifest_version']}` sha256 `{report['manifest_sha256'][:16]}`; "
            f"runner `{report['runner_revision']}`"
        ),
        (
            f"- Source: commit `{report['source'].get('commit')}`, app tree `{report['source'].get('app_tree')}`, "
            f"dirty tracked files: {report['source'].get('dirty')}"
        ),
        f"- Model: {report['model']['mode']} (live model used: {report['model']['live_model_used']})",
        (
            f"- Repeat policy: {report['repeat_policy']['runs']} run(s), {report['repeat_policy']['aggregation']},"
            f" retry_on_failure={report['repeat_policy']['retry_on_failure']}"
        ),
        f"- Started {report['started_at']}, finished {report['finished_at']}",
        "",
        (
            "A verdict exists only for a completed case. Skipped, blocked, timed-out, errored, missing-fixture and"
            " judge-invalid cases have no verdict and are neither passes nor scientific failures. No score is"
            " computed."
        ),
        "",
        "## Counts by execution state",
        "",
        "| execution_state | cases |",
        "| --- | ---: |",
    ]
    out += [f"| {s} | {n} |" for s, n in counts["by_execution_state"].items()]
    out += [
        "",
        f"Verdicts: {counts['by_verdict']}",
        "",
        "## Counts by family",
        "",
        "| family | execution states |",
        "| --- | --- |",
    ]
    out += [f"| {f} | {', '.join(f'{k}={v}' for k, v in c.items())} |" for f, c in counts["by_family"].items()]
    out += [
        "",
        "## Every case",
        "",
        "| case | verdict | execution_state | readiness | coverage | approval | actual / reason |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["results"]:
        why = r["actual"]
        if r["blocked_by"] and r["execution_state"] == "blocked":
            why = "blocked by " + "; ".join(r["blocked_by"])
        out.append(
            f"| {r['case_id']} | {r['verdict'] or '-'} | {r['execution_state']} | {r['readiness']} | "
            f"{r['coverage']} | {r['approval_status']} | {_cell(sanitize(why, 220))} |"
        )
    out += ["", "## Gate blockers", ""]
    for row in gate["blocking_cases"]:
        out.append(f"- {row['case_id']}: {', '.join(row['reasons'])}")
    out.append("")
    return "\n".join(out)


def write_all(report: Dict[str, Any], out_dir: Path, basename: str = "binary_report") -> List[Path]:
    """Write ``<basename>.json``, ``.xml`` (JUnit) and ``.md`` and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix, text in ((".json", to_json(report)), (".xml", to_junit(report)), (".md", to_markdown(report))):
        path = out_dir / f"{basename}{suffix}"
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths
