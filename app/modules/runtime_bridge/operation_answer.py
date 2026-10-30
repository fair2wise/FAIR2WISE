"""Render a knowledge-operation result as a legacy UI answer (owner decision D3.16, 2026-10-30).

The UI QA turn (``F2WApplication.answer_via_supervisor``) now offers the planner the five T19C
knowledge operations beside ``answer_from_knowledge``. When the planner picks an operation,
its bounded ``ToolResult`` becomes the turn's answer: a deterministic text rendering of the
report (operation, scope, coverage, metrics, warnings) placed in the ``answer`` field of a
legacy retrieval verdict, with no selected nodes and no model call. The frozen UI renders it
through the existing answer DTO; ``operation`` on the verdict tells the legacy API that the
turn was a figure, not a retrieval, so it never offers paper ingest for it.

Reports carry counts, ids, labels and bibliographic keys only (D19 is enforced inside the
operations), so the rendering adds no source material.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from ..contracts import ToolResult, ToolStatus

#: Metrics lines rendered before the text is cut (previews are never rendered in full).
MAX_METRIC_LINES = 40
MAX_PREVIEW_ITEMS = 10


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value[:MAX_PREVIEW_ITEMS]) + ("...]" if len(value) > MAX_PREVIEW_ITEMS else "]")
    return str(value)


def _metric_lines(metrics: Mapping[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    for key, value in metrics.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            if len(value) <= 12 and all(not isinstance(v, (Mapping, list, tuple)) for v in value.values()):
                lines.append(f"- {name}: " + ", ".join(f"{k}={_fmt(v)}" for k, v in value.items()))
            else:
                lines.extend(_metric_lines(value, prefix=name + "."))
        else:
            lines.append(f"- {name}: {_fmt(value)}")
    return lines


def render_operation_answer(result: ToolResult) -> str:
    """Markdown text for ``result`` (an operation ``ToolResult``), deterministic and model-free."""
    data: Dict[str, Any] = dict(result.data or {})
    operation = data.get("operation") or result.tool
    scope = data.get("scope") or {}
    coverage = data.get("coverage") or {}
    head = f"**{operation}**"
    if scope:
        head += " for " + ", ".join(f"{k} {_fmt(v)}" for k, v in scope.items())
    lines = [head]
    if result.status is not ToolStatus.OK:
        lines.append(f"Status: {result.status.value}.")
    if coverage:
        done = coverage.get("completeness") or ("complete" if coverage.get("scan_complete") else "partial")
        scanned = coverage.get("records_scanned")
        text = f"Coverage: {done}"
        if scanned is not None:
            text += f" ({scanned} records scanned)"
        lines.append(text + ".")
    method = data.get("method")
    if method:
        lines.append(f"Method: {method}.")
    metrics = data.get("metrics") or {}
    if metrics:
        rows = _metric_lines(metrics)
        lines.append("Figures:")
        lines.extend(rows[:MAX_METRIC_LINES])
        if len(rows) > MAX_METRIC_LINES:
            lines.append(f"- ... {len(rows) - MAX_METRIC_LINES} more figures in the full report")
    units = data.get("counting_units") or {}
    if units:
        lines.append("Counting units: " + "; ".join(f"{k}: {v}" for k, v in list(units.items())[:8]) + ".")
    excluded = data.get("excluded") or {}
    if excluded:
        lines.append("Not counted: " + "; ".join(f"{k} ({v})" for k, v in excluded.items()) + ".")
    for warning in result.warnings:
        lines.append(f"Note: {warning}")
    if result.artifact_refs:
        lines.append(f"Full report: {', '.join(result.artifact_refs)}")
    return "\n".join(lines)


def operation_verdict(result: ToolResult) -> Dict[str, Any]:
    """A legacy retrieval verdict carrying ``result`` as the turn's answer.

    ``status`` is ``success`` and ``sufficient`` is true (the figure answers the question);
    nothing is selected, so the UI maps no nodes; ``operation`` marks the verdict for the
    legacy API (no paper-ingest offer, no evidence judgement).
    """
    answer = render_operation_answer(result)
    return {
        "status": "success",
        "sufficient": result.status in (ToolStatus.OK, ToolStatus.PARTIAL),
        "answer": answer,
        "selected": [],
        "selected_hits": [],
        "graph_ids": list((result.data or {}).get("scope", {}).values()) if isinstance((result.data or {}).get("scope"), dict) else [],
        "missing_topics": [] if result.status in (ToolStatus.OK, ToolStatus.PARTIAL) else [f"{result.tool} did not run"],
        "direct_evidence_count": 0,
        "no_evidence": False,
        "operation": {"tool": result.tool, "status": result.status.value, "provenance_id": result.provenance_id,
                      "artifact_refs": list(result.artifact_refs)},
    }


__all__ = ("MAX_METRIC_LINES", "MAX_PREVIEW_ITEMS", "operation_verdict", "render_operation_answer")
