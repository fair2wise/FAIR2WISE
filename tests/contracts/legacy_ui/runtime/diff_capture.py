"""Compare a live-backend browser capture with the T18A recorded fixture shapes.

Input: ``capture.jsonl`` written by ``launch_live_backend.py`` (one sanitized
request/response per line). Each captured exchange (CORS preflights excluded)
is matched to the T18A fixtures with the same method, path and terminal
status (SSE terminal ``event:status`` or JSON ``status``/HTTP code). For the
best match it reports:

* response JSON shape differences (``_legacy_ui_harness.shape``, so values are
  ignored and only keys/types count), per deep path;
* SSE: framing validity (strict baseline parser), first event is
  ``turn_started`` with the request's ``client_turn_id``, exactly one terminal
  event, the progress-phase sequence vs the fixture, and the shape of every
  event's data for phases present in both;
* request shape differences (the frozen UI's real body vs the hand-written
  fixture request) - informational, the UI is the authority here;
* citations: every ``[KG: id]`` in an answer must appear in ``node_ids`` and
  in the ``graph`` payload;
* exchanges with no fixture counterpart at all.

Usage::

    python tests/contracts/legacy_ui/runtime/diff_capture.py <capture.jsonl> [--out report.json]

Exit status is 0; this is a report, the specs are the gate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import _legacy_ui_harness as H  # noqa: E402

KG_CITE = re.compile(r"\[KG:\s*([^\]]+)\]")


def load_fixtures() -> List[Dict[str, Any]]:
    out = []
    for path in sorted((H.FIXTURE_DIR / "http").glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("flow") == "model_api":
            continue
        out.append(rec)
    return out


def terminal_of_events(events: List[Dict[str, Any]]) -> Optional[str]:
    if not events:
        return None
    last = events[-1]
    return f"{last['event']}:{(last.get('data') or {}).get('status')}"


def fixture_terminal(rec: Dict[str, Any]) -> str:
    resp = rec["response"]
    if resp.get("sse_events"):
        return terminal_of_events(resp["sse_events"]) or ""
    body = resp.get("json")
    if isinstance(body, dict) and "status" in body:
        return f"{resp['status_code']}:{body['status']}"
    return str(resp["status_code"])


def capture_terminal(rec: Dict[str, Any], events: Optional[List[Dict[str, Any]]]) -> str:
    if events is not None:
        return terminal_of_events(events) or ""
    body = rec.get("response_json")
    if isinstance(body, dict) and "status" in body:
        return f"{rec['status']}:{body['status']}"
    return str(rec["status"])


def shape_diff(a: Any, b: Any, path: str = "$") -> List[str]:
    """Differences between two shape() skeletons (a = fixture, b = live)."""
    diffs: List[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in b:
                diffs.append(f"{path}.{key}: missing in live")
            elif key not in a:
                diffs.append(f"{path}.{key}: added in live ({json.dumps(b[key])[:80]})")
            else:
                diffs.extend(shape_diff(a[key], b[key], f"{path}.{key}"))
        if not diffs and list(a) != list(b):
            pass  # shape() sorts keys; order is checked separately for ChatResponse
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if not a or not b:
            return []  # an empty list on either side carries no item shape
        merged_a = _merge_items(a)
        merged_b = _merge_items(b)
        return shape_diff(merged_a, merged_b, f"{path}[]")
    if a != b:
        # null vs value is a data difference, not a schema one.
        if "null" in (a, b):
            return []
        if {a, b} == {"int", "number"}:
            return []
        diffs.append(f"{path}: type {a} -> {b}")
    return diffs


def _merge_items(items: List[Any]) -> Any:
    dicts = [i for i in items if isinstance(i, dict)]
    if dicts and len(dicts) == len(items):
        merged: Dict[str, Any] = {}
        for d in dicts:
            for k, v in d.items():
                if k not in merged or merged[k] in ("null", [], {}):
                    merged[k] = v
        return merged
    return items[0]


def parse_live_sse(raw: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    try:
        return H.parse_sse(raw.encode("utf-8")), None
    except AssertionError as exc:
        return None, str(exc)[:200]


def sse_checks(rec: Dict[str, Any], events: List[Dict[str, Any]], fixture: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    req = rec.get("request_json") or {}
    first = events[0] if events else {}
    terminals = [e for e in events if e["event"] in ("complete", "error")]
    out: Dict[str, Any] = {
        "first_is_turn_started": first.get("event") == "progress"
        and (first.get("data") or {}).get("phase") == "turn_started",
        "turn_id_echoed": (first.get("data") or {}).get("turn_id") == req.get("client_turn_id"),
        "single_terminal_last": len(terminals) == 1 and events[-1] in terminals,
        "phases": [e["data"].get("phase") for e in events if e["event"] == "progress"],
    }
    if fixture:
        fx_events = fixture["response"]["sse_events"]
        fx_phases = [e["data"].get("phase") for e in fx_events if e["event"] == "progress"]
        out["fixture_phases"] = fx_phases
        out["phase_sequence_equal"] = out["phases"] == fx_phases
        event_diffs: Dict[str, List[str]] = {}
        fx_by_phase = {}
        for e in fx_events:
            key = e["data"].get("phase") or e["event"]
            fx_by_phase.setdefault(key, e)
        for e in events:
            key = e["data"].get("phase") or e["event"]
            if key in fx_by_phase:
                d = shape_diff(H.shape(fx_by_phase[key]["data"]), H.shape(e["data"]), f"{key}")
                if d:
                    event_diffs.setdefault(key, [])
                    for item in d:
                        if item not in event_diffs[key]:
                            event_diffs[key].append(item)
        out["event_shape_diffs"] = event_diffs
        fx_term = fx_events[-1]["data"]
        live_term = events[-1]["data"]
        if isinstance(fx_term, dict) and isinstance(live_term, dict) and "answer" in fx_term:
            out["terminal_key_order_equal"] = list(fx_term) == list(live_term)
    return out


def citation_check(body: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("answer"), str):
        return None
    cited = [c.strip() for c in KG_CITE.findall(body["answer"])]
    if not cited:
        return None
    node_ids = set(body.get("node_ids") or [])
    graph_ids = {n.get("id") for n in ((body.get("graph") or {}).get("nodes") or [])}
    return {
        "cited": cited,
        "missing_from_node_ids": [c for c in cited if c not in node_ids],
        "missing_from_graph": [c for c in cited if c not in graph_ids],
        "graph_ids": sorted({str(n.get("graph_id")) for n in ((body.get("graph") or {}).get("nodes") or [])}),
    }


def best_fixture(cands: List[Dict[str, Any]], rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not cands:
        return None
    msg = str((rec.get("request_json") or {}).get("message") or "").lower()
    for c in cands:  # same question text first (e.g. the T18A answered question)
        if str(((c.get("request") or {}).get("json") or {}).get("message") or "").lower() in msg and msg:
            return c
    return cands[0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    fixtures = load_fixtures()
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for fx in fixtures:
        by_key.setdefault((fx["request"]["method"], fx["request"]["path"]), []).append(fx)

    rows = []
    for line in args.capture.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["method"] == "OPTIONS":
            continue
        events = None
        framing_error = None
        if rec.get("sse_raw") is not None:
            events, framing_error = parse_live_sse(rec["sse_raw"])
        term = capture_terminal(rec, events) if framing_error is None else "framing_error"
        same_route = by_key.get((rec["method"], rec["path"]), [])
        cands = [fx for fx in same_route if fixture_terminal(fx) == term]
        fixture = best_fixture(cands, rec)
        row: Dict[str, Any] = {
            "seq": rec["seq"], "route": f"{rec['method']} {rec['path']}", "status": rec["status"],
            "terminal": term, "fixture": fixture["id"] if fixture else None,
            "route_in_fixtures": bool(same_route), "disconnected": rec.get("disconnected", False),
            "message": (rec.get("request_json") or {}).get("message"),
        }
        if framing_error:
            row["framing_error"] = framing_error
        if fixture:
            fx_req = (fixture.get("request") or {}).get("json")
            if isinstance(fx_req, dict) and isinstance(rec.get("request_json"), dict):
                row["request_shape_diffs"] = shape_diff(H.shape(fx_req), H.shape(rec["request_json"]))
            if events is not None and fixture["response"].get("sse_events"):
                row["sse"] = sse_checks(rec, events, fixture)
            elif fixture["response"].get("json") is not None and rec.get("response_json") is not None:
                row["response_shape_diffs"] = shape_diff(H.shape(fixture["response"]["json"]),
                                                         H.shape(rec["response_json"]))
                if isinstance(rec["response_json"], dict) and isinstance(fixture["response"]["json"], dict) \
                        and "answer" in rec["response_json"]:
                    row["key_order_equal"] = list(rec["response_json"]) == list(fixture["response"]["json"])
            row["content_type_equal"] = (rec.get("content_type") or "").split(";")[0] == \
                (fixture["response"].get("content_type") or "").split(";")[0]
        elif events is not None:
            row["sse"] = sse_checks(rec, events, None)
        body = events[-1]["data"] if events else rec.get("response_json")
        cite = citation_check(body)
        if cite:
            row["citations"] = cite
        rows.append(row)

    summary = {
        "exchanges": len(rows),
        "matched": sum(1 for r in rows if r["fixture"]),
        "unmatched": [f"{r['seq']} {r['route']} {r['terminal']}" for r in rows if not r["fixture"]],
        "framing_errors": [r["seq"] for r in rows if r.get("framing_error")],
        "sse_invariant_failures": [r["seq"] for r in rows if r.get("sse") and not (
            r["sse"]["first_is_turn_started"] and r["sse"]["turn_id_echoed"] and r["sse"]["single_terminal_last"])
            and not r.get("disconnected")],
        "response_shape_diffs": sorted({d for r in rows for d in r.get("response_shape_diffs", [])}),
        "sse_event_shape_diffs": sorted({d for r in rows for v in (r.get("sse") or {}).get("event_shape_diffs", {}).values()
                                         for d in v}),
        "phase_sequence_differs": [f"{r['seq']} vs {r['fixture']}" for r in rows
                                   if (r.get("sse") or {}).get("phase_sequence_equal") is False],
        "terminal_key_order_differs": [r["seq"] for r in rows if (r.get("sse") or {}).get("terminal_key_order_equal") is False
                                       or r.get("key_order_equal") is False],
        "request_shape_diffs": sorted({d for r in rows for d in r.get("request_shape_diffs", [])}),
        "citation_failures": [r["seq"] for r in rows if r.get("citations") and (
            r["citations"]["missing_from_node_ids"] or r["citations"]["missing_from_graph"])],
    }
    report = {"summary": summary, "rows": rows}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
