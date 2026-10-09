#!/usr/bin/env python3
"""POST each eval case to /chat and print a turn-id table.

YAML cases (evals/cases/batch1.yaml) all go to /chat. tuples.jsonl rows with
check_type U are left for pytest (-m eval). Aggregate rows with no questions
are skipped. A .txt source is expanded into one turn per line.

A case may set history_from to another case id. That case is posted first, in
the follow-up's session_id, and its question plus answer are sent as messages.

``--live-tiled`` and ``--source-rag`` override the file's settings block.
The runner scores YAML rows that carry must_include, allowed_entities,
must_not_match, assert_graph, or when_tiled_off.

Usage:
  python scripts/run_eval_batch.py --cases evals/cases/batch1.yaml --batch-id pilot
  python scripts/run_eval_batch.py --cases evals/cases/batch1.yaml --batch-id pilot --live-tiled on --source-rag off
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
_SESSION_RE = re.compile(r"[^A-Za-z0-9_-]+")
_SCAN_COUNT_RE = re.compile(r"^scan_count:\s*(\d+)$")
_UID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_CODE_RE = re.compile(r"\bP2026\d{5}(?:-\d{2})?(?:-S\d{2})?\b")
_SCORE_KEYS = (
    "expected_graph_ids",
    "assert_graph",
    "must_include",
    "allowed_entities",
    "must_not_match",
    "must_not_mention",
    "when_tiled_off",
    "expect_live_tiled_status",
    "tuple_ids",
    "notes",
    "prefilled",
)


def _session_id(batch_id: str, case_id: str) -> str:
    text = _SESSION_RE.sub("-", f"{batch_id}-{case_id}").strip("-")
    return (text or "eval")[:64]


def _read_questions(source: Optional[str]) -> List[str]:
    if not source or not str(source).endswith(".txt"):
        return []
    path = ROOT / source
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def load_yaml_cases(path: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = document.get("settings") or {}
    cases = []
    for raw in document.get("cases") or []:
        question = str(raw.get("question") or "").strip()
        if not question:
            continue
        case = {
            "id": str(raw.get("id") or ""),
            "question": question,
            "category": raw.get("category") or "",
            "check_type": "C",
            "history_from": raw.get("history_from") or None,
            "run": True,
        }
        for key in _SCORE_KEYS:
            if key in raw:
                case[key] = raw[key]
        cases.append(case)
    return settings, cases


def load_tuple_cases(path: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    settings: Dict[str, Any] = {}
    cases: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        check_type = str(row.get("check_type") or "")
        questions = [str(item).strip() for item in (row.get("questions") or []) if str(item).strip()]
        if not questions:
            questions = _read_questions(row.get("source"))
        if check_type == "U":
            cases.append({
                "id": row.get("id"),
                "question": "",
                "category": row.get("task_type") or "",
                "check_type": "U",
                "history_from": row.get("history_from") or None,
                "run": False,
                "skip_reason": "check_type U; run pytest -m eval",
            })
            continue
        if not questions:
            cases.append({
                "id": row.get("id"),
                "question": "",
                "category": row.get("task_type") or "",
                "check_type": check_type,
                "history_from": row.get("history_from") or None,
                "run": False,
                "skip_reason": "aggregate row; no question to send",
            })
            continue
        for index, question in enumerate(questions, start=1):
            case_id = row.get("id") if len(questions) == 1 else f"{row.get('id')}.{index}"
            cases.append({
                "id": case_id,
                "question": question,
                "category": row.get("task_type") or "",
                "check_type": check_type,
                "history_from": row.get("history_from") or None,
                "run": True,
            })
    return settings, cases


def load_cases(path: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if path.suffix == ".jsonl":
        return load_tuple_cases(path)
    return load_yaml_cases(path)


def _post_chat(base: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base.rstrip('/')}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _find_case(cases: List[Dict[str, Any]], case_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a history_from id to the single runnable case it names."""
    exact = [case for case in cases if case.get("run") and case.get("id") == case_id]
    if len(exact) == 1:
        return exact[0]
    prefixed = [
        case for case in cases
        if case.get("run") and str(case.get("id") or "").startswith(f"{case_id}.")
    ]
    if len(prefixed) == 1:
        return prefixed[0]
    return None


def history_chain(case: Dict[str, Any], cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prior cases to post first, oldest first, in this follow-up's session."""
    chain: List[Dict[str, Any]] = []
    seen = {str(case.get("id") or "")}
    current = case
    while current.get("history_from"):
        prior_id = str(current["history_from"])
        if prior_id in seen:
            raise ValueError(f"history_from cycle at {prior_id}")
        seen.add(prior_id)
        prior = _find_case(cases, prior_id)
        if prior is None:
            raise KeyError(prior_id)
        chain.append(prior)
        current = prior
    chain.reverse()
    return chain


def _body(
    case: Dict[str, Any],
    settings: Dict[str, Any],
    batch_id: str,
    live_tiled: Optional[bool],
    *,
    session_id: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    ui = dict(settings.get("ui_settings") or {})
    ui["category"] = case.get("category") or ""
    ui["case_id"] = case["id"]
    use_live = settings.get("use_live_tiled")
    if live_tiled is not None:
        use_live = live_tiled
        ui["useLiveTiled"] = live_tiled
    body: Dict[str, Any] = {
        "message": case["question"],
        "client_turn_id": str(uuid.uuid4()),
        "session_id": session_id or _session_id(batch_id, str(case["id"])),
        "batch_id": batch_id,
        "ui_settings": ui,
    }
    if messages:
        body["messages"] = messages
    if settings.get("graph_source"):
        body["graph_source"] = settings["graph_source"]
    paths = settings.get("json_graph_paths") or []
    if paths:
        body["json_graph_paths"] = list(paths)
    if settings.get("source_rag") is not None:
        body["source_rag"] = bool(settings["source_rag"])
    if use_live is not None:
        body["use_live_tiled"] = bool(use_live)
    return body


def _print_table(rows: List[Dict[str, str]]) -> None:
    headers = ("id", "turn_id", "status", "score", "note")
    widths = {name: len(name) for name in headers}
    for row in rows:
        for name in headers:
            widths[name] = max(widths[name], len(row.get(name) or ""))
    line = "  ".join(name.ljust(widths[name]) for name in headers)
    print(line)
    print("  ".join("-" * widths[name] for name in headers))
    for row in rows:
        print("  ".join((row.get(name) or "").ljust(widths[name]) for name in headers))


def _history_messages(turns: List[tuple[str, str]]) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    for question, answer in turns:
        if question:
            messages.append({"role": "user", "content": question})
        if answer:
            messages.append({"role": "assistant", "content": answer})
    return messages


def effective_settings(
    settings: Dict[str, Any],
    *,
    live_tiled: Optional[bool] = None,
    source_rag: Optional[bool] = None,
) -> Dict[str, Any]:
    """Copy settings and apply CLI overrides onto the file block and ui_settings."""
    merged = copy.deepcopy(settings or {})
    ui = dict(merged.get("ui_settings") or {})
    if live_tiled is not None:
        merged["use_live_tiled"] = live_tiled
        ui["useLiveTiled"] = live_tiled
    if source_rag is not None:
        merged["source_rag"] = source_rag
        ui["sourceRag"] = source_rag
    merged["ui_settings"] = ui
    return merged


def format_effective_settings(settings: Dict[str, Any]) -> str:
    paths = settings.get("json_graph_paths") or []
    path_text = ", ".join(str(path) for path in paths) or "(none)"
    return "\n".join([
        "effective settings:",
        f"  graph_source: {settings.get('graph_source')}",
        f"  json_graph_paths: {path_text}",
        f"  source_rag: {settings.get('source_rag')}",
        f"  use_live_tiled: {settings.get('use_live_tiled')}",
    ])


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [str(item) for item in value if str(item).strip()]


def _included(answer: str, item: str) -> bool:
    count = _SCAN_COUNT_RE.match(item.strip())
    if count:
        return re.search(rf"\b{count.group(1)}\b", answer) is not None
    return item.casefold() in answer.casefold()


def _identity_tokens(text: str) -> List[str]:
    return [match.group(0) for match in _UID_RE.finditer(text)] + [
        match.group(0) for match in _CODE_RE.finditer(text)
    ]


def _token_allowed(token: str, allow: Sequence[str]) -> bool:
    folded = token.casefold()
    return any(folded in item.casefold() for item in allow)


def _graph_ids(payload: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        graph_id = str(node.get("graph_id") or "").strip()
        if graph_id and graph_id not in found:
            found.append(graph_id)
    return found


def _live_tiled_status(payload: Dict[str, Any]) -> Optional[str]:
    for block in (payload.get("live_tiled"), (payload.get("retrieval") or {}).get("live_tiled") if isinstance(payload.get("retrieval"), dict) else None):
        if isinstance(block, dict) and block.get("status"):
            return str(block["status"])
    turn_id = str(payload.get("turn_id") or "").strip()
    if not turn_id:
        return None
    try:
        from app.modules.f2w_agent.trace import load_detail
    except Exception:
        return None
    detail = load_detail(turn_id)
    if not isinstance(detail, dict):
        return None
    live = (detail.get("retrieval") or {}).get("live_tiled")
    if isinstance(live, dict) and live.get("status"):
        return str(live["status"])
    return None


def score_case(
    case: Dict[str, Any],
    answer: str,
    *,
    graph_ids: Optional[Sequence[str]] = None,
    live_tiled_on: bool = False,
    live_tiled_status: Optional[str] = None,
) -> List[str]:
    """Return score problems. An empty list is a pass.

    When live Tiled is off and the case has when_tiled_off, that block replaces
    must_include, allowed_entities, and must_not_match. expect_live_tiled_status
    is checked only while live Tiled is on. assert_graph: false skips the graph check.
    """
    text = answer or ""
    problems: List[str] = []
    overlay = case.get("when_tiled_off") if not live_tiled_on else None
    if isinstance(overlay, dict):
        required = _as_list(overlay.get("must_include"))
        banned = _as_list(overlay.get("must_not_match"))
        allow: List[str] = []
        check_tokens = False
    else:
        required = _as_list(case.get("must_include"))
        banned = _as_list(case.get("must_not_match"))
        allow = _as_list(case.get("must_include")) + _as_list(case.get("allowed_entities"))
        check_tokens = "allowed_entities" in case or "must_include" in case
    for item in required:
        if not _included(text, item):
            problems.append(f"missing must_include {item}")
    if check_tokens:
        for token in _identity_tokens(text):
            if not _token_allowed(token, allow):
                problems.append(f"identity token not allowed: {token}")
    for pattern in banned:
        try:
            if re.search(pattern, text):
                problems.append(f"must_not_match {pattern}")
        except re.error as exc:
            problems.append(f"bad must_not_match {pattern}: {exc}")
    for item in _as_list(case.get("must_not_mention")):
        if item.casefold() in text.casefold():
            problems.append(f"must_not_mention {item}")
    if case.get("assert_graph", True) is not False:
        expected = _as_list(case.get("expected_graph_ids"))
        observed = list(graph_ids or [])
        missing = [graph_id for graph_id in expected if graph_id not in observed]
        if missing:
            problems.append("graph ids missing: " + ", ".join(missing))
    expected_status = case.get("expect_live_tiled_status")
    if live_tiled_on and expected_status:
        if live_tiled_status != expected_status:
            problems.append(
                f"live_tiled status {live_tiled_status!r} != {expected_status!r}"
            )
    return problems


def _scorable(case: Dict[str, Any]) -> bool:
    return any(key in case for key in ("must_include", "allowed_entities", "must_not_match", "assert_graph", "when_tiled_off", "expect_live_tiled_status"))


def execute_cases(
    cases: List[Dict[str, Any]],
    settings: Dict[str, Any],
    *,
    batch_id: str,
    live_tiled: Optional[bool],
    base_url: str,
    timeout: float,
    post=_post_chat,
) -> tuple[List[Dict[str, str]], int]:
    """POST runnable cases. A history_from case is sent first in the same session."""
    rows: List[Dict[str, str]] = []
    failures = 0
    for case in cases:
        if not case.get("run"):
            rows.append({
                "id": str(case.get("id") or ""),
                "turn_id": "",
                "status": "skipped",
                "note": str(case.get("skip_reason") or ""),
            })
            continue
        session_id = _session_id(batch_id, str(case["id"]))
        prior_turns: List[tuple[str, str]] = []
        try:
            priors = history_chain(case, cases)
        except KeyError as exc:
            failures += 1
            rows.append({
                "id": str(case["id"]),
                "turn_id": "",
                "status": "error",
                "note": f"history_from {exc} is not a runnable case",
            })
            continue
        except ValueError as exc:
            failures += 1
            rows.append({
                "id": str(case["id"]),
                "turn_id": "",
                "status": "error",
                "note": str(exc),
            })
            continue
        history_failed = False
        for prior in priors:
            body = _body(prior, settings, batch_id, live_tiled, session_id=session_id, messages=_history_messages(prior_turns))
            try:
                payload = post(base_url, body, timeout)
            except Exception as exc:
                failures += 1
                history_failed = True
                rows.append({
                    "id": f"{case['id']}←{prior['id']}",
                    "turn_id": body["client_turn_id"],
                    "status": "error",
                    "note": f"history for {case['id']}: {exc}",
                })
                break
            answer = payload.get("answer")
            answer_text = answer.strip() if isinstance(answer, str) else ""
            prior_turns.append((prior["question"], answer_text))
            rows.append({
                "id": f"{case['id']}←{prior['id']}",
                "turn_id": str(payload.get("turn_id") or body["client_turn_id"]),
                "status": str(payload.get("status") or "ok"),
                "note": f"history for {case['id']}",
            })
        if history_failed:
            rows.append({
                "id": str(case["id"]),
                "turn_id": "",
                "status": "error",
                "note": "not sent; history turn failed",
            })
            failures += 1
            continue
        body = _body(
            case,
            settings,
            batch_id,
            live_tiled,
            session_id=session_id,
            messages=_history_messages(prior_turns),
        )
        try:
            payload = post(base_url, body, timeout)
        except urllib.error.HTTPError as exc:
            failures += 1
            detail = exc.read().decode("utf-8", errors="replace")[:160]
            rows.append({
                "id": str(case["id"]),
                "turn_id": body["client_turn_id"],
                "status": f"http {exc.code}",
                "note": detail,
            })
            continue
        except Exception as exc:
            failures += 1
            rows.append({
                "id": str(case["id"]),
                "turn_id": body["client_turn_id"],
                "status": "error",
                "note": str(exc),
            })
            continue
        answer = payload.get("answer")
        answer_text = answer if isinstance(answer, str) else ""
        score = ""
        note = ""
        if _scorable(case):
            problems = score_case(
                case,
                answer_text,
                graph_ids=_graph_ids(payload),
                live_tiled_on=bool(live_tiled) if live_tiled is not None else bool(settings.get("use_live_tiled")),
                live_tiled_status=_live_tiled_status(payload),
            )
            score = "fail" if problems else "pass"
            note = "; ".join(problems)
            if problems:
                failures += 1
        rows.append({
            "id": str(case["id"]),
            "turn_id": str(payload.get("turn_id") or body["client_turn_id"]),
            "status": str(payload.get("status") or "ok"),
            "score": score,
            "note": note[:240],
        })
    return rows, failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a batch of /chat eval cases.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--live-tiled", choices=("on", "off"), default=None)
    parser.add_argument("--source-rag", choices=("on", "off"), default=None)
    parser.add_argument("--base-url", default=os.environ.get("F2W_AGENT_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    live = None if args.live_tiled is None else args.live_tiled == "on"
    source_rag = None if args.source_rag is None else args.source_rag == "on"
    settings, cases = load_cases(args.cases)
    settings = effective_settings(settings, live_tiled=live, source_rag=source_rag)
    print(format_effective_settings(settings))
    print()
    live_flag = settings["use_live_tiled"] if "use_live_tiled" in settings else None
    rows, failures = execute_cases(
        cases,
        settings,
        batch_id=args.batch_id,
        live_tiled=live_flag,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    _print_table(rows)
    skipped = sum(1 for row in rows if row["status"] == "skipped")
    posted = len(rows) - failures - skipped
    print(f"\n{posted} posted, {skipped} skipped, {failures} failed, batch_id={args.batch_id}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
