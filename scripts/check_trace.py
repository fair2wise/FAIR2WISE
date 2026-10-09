#!/usr/bin/env python3
"""Fail if a saved turn trace is missing the fields binary evals read.

Usage: python scripts/check_trace.py <turn_id>

Looks up ``runs/traces/*/turns/<turn_id>.json`` (or ``F2W_TRACE_DIR``).
A retrieval turn must carry the classifier call, the judge call, the judge's
raw output, the merged context, route flags, graph sha256s, and ``leeway_used``.
Any string of length 1,200 is treated as a session-memory clip and fails.
"""
from __future__ import annotations

import re
import sys
from typing import Any, List, Tuple

from app.modules.f2w_agent.trace import ROUTE_FLAG_KEYS, load_detail

CLIP_LEN = 1200
CLASSIFIER_LABEL = "orchestrator-classify"
JUDGE_LABEL = "KG-RAG-judge"


def _walk_strings(value: Any, path: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    if isinstance(value, str):
        found.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk_strings(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{path}[{index}]"))
    return found


def _llm_problems(detail: dict, label: str) -> List[str]:
    calls = [
        call for call in (detail.get("llm_calls") or [])
        if isinstance(call, dict) and call.get("label") == label
    ]
    if not calls:
        return [f"llm_calls has no entry labeled {label!r}"]
    problems: List[str] = []
    for index, call in enumerate(calls):
        messages = call.get("messages")
        where = f"llm_calls[{label}#{index}]"
        if not isinstance(messages, list) or not messages:
            problems.append(f"{where}.messages is missing or empty")
            continue
        for msg_index, message in enumerate(messages):
            if not isinstance(message, dict):
                problems.append(f"{where}.messages[{msg_index}] is not an object")
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                problems.append(f"{where}.messages[{msg_index}].content is missing or empty")
        response = call.get("response")
        if not isinstance(response, str) or not response.strip():
            problems.append(f"{where}.response is missing or empty")
    return problems


def trace_problems(detail: dict) -> List[str]:
    """Return human-readable failures. An empty list means the trace is complete."""
    problems: List[str] = []
    problems.extend(_llm_problems(detail, CLASSIFIER_LABEL))
    problems.extend(_llm_problems(detail, JUDGE_LABEL))

    retrieval = detail.get("retrieval") if isinstance(detail.get("retrieval"), dict) else {}
    if "leeway_used" not in retrieval or not isinstance(retrieval.get("leeway_used"), bool):
        problems.append("retrieval.leeway_used is missing or not a bool")
    raw_judge = retrieval.get("raw_judge_output")
    if not isinstance(raw_judge, str) or not raw_judge.strip():
        problems.append("retrieval.raw_judge_output is missing or empty")
    merged = retrieval.get("merged_context")
    if not isinstance(merged, str) or not merged.strip():
        problems.append("retrieval.merged_context is missing or empty")
    flags = retrieval.get("route_flags") if isinstance(retrieval.get("route_flags"), dict) else {}
    for name in ROUTE_FLAG_KEYS:
        if not isinstance(flags.get(name), bool):
            problems.append(f"retrieval.route_flags.{name} is missing or not a bool")
    if not isinstance(retrieval.get("layout_ops_only_filter"), bool):
        problems.append("retrieval.layout_ops_only_filter is missing or not a bool")

    env = detail.get("env") if isinstance(detail.get("env"), dict) else {}
    graphs = env.get("graphs")
    if not isinstance(graphs, list) or not graphs:
        problems.append("env.graphs is missing or empty")
    else:
        for index, graph in enumerate(graphs):
            digest = graph.get("sha256") if isinstance(graph, dict) else None
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                path = graph.get("path") if isinstance(graph, dict) else graph
                problems.append(f"env.graphs[{index}] sha256 missing for {path}")

    for path, text in _walk_strings(detail, ""):
        # session_memory._clip keeps ``limit`` characters and ends with "…".
        if len(text) == CLIP_LEN and text.endswith("…"):
            problems.append(f"{path} is clipped at {CLIP_LEN} characters")
    return problems


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/check_trace.py <turn_id>", file=sys.stderr)
        return 2
    turn_id = argv[1]
    detail = load_detail(turn_id)
    if detail is None:
        print(f"FAIL trace not found: {turn_id}", file=sys.stderr)
        return 1
    problems = trace_problems(detail)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"{len(problems)} problem(s) in {turn_id}", file=sys.stderr)
        return 1
    print(f"ok {turn_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
