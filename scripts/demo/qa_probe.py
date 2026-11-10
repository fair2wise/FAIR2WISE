#!/usr/bin/env python3
"""Run the demo QA question set against a running container's chat API and print PASS/WARN per question.

Standard library only (urllib), no API key. The only state it changes is the chat turns themselves (each
question uses its own fresh session id, so no turn sees another's memory); it never calls an admin, publish or
settings route. Each answered question costs real model calls on the server, so run it deliberately.

    python scripts/demo/qa_probe.py [--base http://127.0.0.1:8080/fair2wise/api] [--stream] [--only ID ...]

Verdicts. An answerable question names its expected ``corpus`` and PASSes when an expected node id **of that corpus** is
among the returned nodes (the chat names the corpus on every node: ``graph.nodes[].graph_id``, and prefixes an id that two
corpora share with ``<corpus>:``), or (questions without curated node ids) when an expected term occurs in the answer or in
a returned node name; it WARNs when the status is not an answer, no evidence of the expected corpus matches (the reason
says which corpora did answer), the answer opens with a hedge, or it was slower than ``--slow``.
An unanswerable question PASSes only when the answer opens with the sentence saying the knowledge graph has no information
(it must still be a normal answer, not an error).
Exit code: 0 normally, 1 with ``--strict`` and any WARN, 2 when the API is unreachable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "docs" / "migration" / "demo" / "qa_questions.json"
NO_INFO_RE = re.compile(r"knowledge graph has no information", re.I)
HEDGE_RE = re.compile(r"^\W*(the (knowledge )?graph (lacks|does not)|no .{0,40} graph\b|i could not)", re.I)


def _origin(base: str) -> str:
    parts = urllib.parse.urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


def post_chat(base: str, message: str, session: str, timeout: float, stream: bool) -> tuple[int, dict]:
    """POST one chat turn; returns ``(http_status, response_dict)``. With ``stream`` reads ``/chat/stream`` SSE."""
    body = json.dumps({"message": message, "session_id": session}).encode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}/chat{'/stream' if stream else ''}", data=body, method="POST",
        headers={"Content-Type": "application/json", "Origin": _origin(base)},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, {"answer": exc.read().decode("utf-8", "replace")[:300], "status": f"http_{exc.code}"}
    if not stream:
        return status, json.loads(raw)
    last: dict = {"status": "stream_incomplete", "answer": ""}
    for block in raw.split("\n\n"):
        event = next((ln[6:].strip() for ln in block.splitlines() if ln.startswith("event:")), "")
        data = "\n".join(ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:"))
        if event in {"complete", "error"} and data:
            last = json.loads(data)
    return status, last


def sources(resp: dict) -> tuple[list[str], list[str]]:
    """``(node ids, node names + publication titles)`` of a chat response."""
    ids = [str(n) for n in resp.get("node_ids") or []]
    names = [str(n.get("name") or "") for n in (resp.get("graph") or {}).get("nodes") or [] if isinstance(n, dict)]
    titles = [str(p.get("paper_title") or "") for p in resp.get("publications") or [] if isinstance(p, dict)]
    return ids, [*names, *titles]


def node_pairs(resp: dict) -> list[tuple[str | None, str]]:
    """``(corpus, node id)`` of the returned nodes: ``graph.nodes[]`` carry ``graph_id`` (the corpus) and an id that is
    ``<corpus>:<id>`` when two corpora share it; ids only in ``node_ids`` have no corpus (``None``)."""
    pairs: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for node in (resp.get("graph") or {}).get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        gid, nid = node.get("graph_id"), str(node["id"])
        seen.add(nid)
        if gid and nid.startswith(f"{gid}:"):
            nid = nid[len(gid) + 1:]
        pairs.append((str(gid) if gid else None, nid))
    pairs += [(None, str(n)) for n in resp.get("node_ids") or [] if str(n) not in seen]
    return pairs


def corpus_matches(graph_id: str | None, corpus: str) -> bool:
    """A node of ``graph_id`` belongs to ``corpus`` (an unknown corpus, or a JSON-mode graph id such as ``rsoxs_v4``)."""
    return graph_id is None or graph_id == corpus or graph_id.startswith(f"{corpus}_")


def judge(item: dict, resp: dict, latency: float, slow: float) -> tuple[str, list[str]]:
    """``("PASS"|"WARN", reasons)`` for one question."""
    answer = str(resp.get("answer") or "")
    status = str(resp.get("status") or "")
    ids, names = sources(resp)
    reasons: list[str] = []
    if not item.get("answerable", True):
        if not NO_INFO_RE.search(answer[:200]):
            return "WARN", [f"answer does not open with the 'no information' sentence (status={status})"]
        if status != "answered":
            reasons.append(f"status={status}")
        if latency > slow:
            reasons.append(f"slow {latency:.0f}s")
        return ("WARN" if reasons else "PASS"), reasons
    if status != "answered":
        reasons.append(f"status={status}")
    expected = set(item.get("expected_node_ids") or [])
    terms = [t.lower() for t in item.get("expected_terms") or []]
    haystack = " ".join([answer, *ids, *names]).lower()
    corpus = str(item.get("corpus") or "")
    if expected and corpus in {"", "none"}:
        found = bool(expected & {nid for _gid, nid in node_pairs(resp)})
        other: set[str] = set()
    elif expected:
        pairs = node_pairs(resp)
        found = any(nid in expected and corpus_matches(gid, corpus) for gid, nid in pairs)
        other = {str(gid) for gid, nid in pairs if nid in expected and gid and not corpus_matches(gid, corpus)}
    if expected and not found:
        where = f" (an expected node came only from {sorted(other)})" if other else ""
        reasons.append(f"no expected node of corpus {corpus or 'any'} among the returned nodes{where}")
    elif not expected and terms and not any(t in haystack for t in terms):
        reasons.append("no expected term in answer or nodes")
    if HEDGE_RE.search(answer):
        reasons.append("answer opens with a hedge")
    if latency > slow:
        reasons.append(f"slow {latency:.0f}s")
    return ("WARN" if reasons else "PASS"), reasons


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8080/fair2wise/api")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--only", nargs="*", default=[], help="question ids to run")
    ap.add_argument("--stream", action="store_true", help="use /chat/stream (what the UI uses)")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--slow", type=float, default=45.0, help="latency above this many seconds is a WARN")
    ap.add_argument("--width", type=int, default=420, help="characters of each answer to print")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    items = json.loads(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    if args.only:
        items = [i for i in items if i["id"] in args.only]
    run = uuid.uuid4().hex[:8]
    results: list[tuple[str, str, float]] = []
    for n, item in enumerate(items, 1):
        started = time.monotonic()
        try:
            http, resp = post_chat(args.base, item["question"], f"qaprobe-{run}-{n}", args.timeout, args.stream)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[{item['id']}] API unreachable at {args.base}: {exc}", file=sys.stderr)
            return 2
        latency = time.monotonic() - started
        verdict, reasons = judge(item, resp, latency, args.slow)
        if http != 200 and verdict == "PASS":
            verdict, reasons = "WARN", [f"http {http}"]
        ids, names = sources(resp)
        by_corpus: dict[str, int] = {}
        for gid, _nid in node_pairs(resp):
            by_corpus[gid or "?"] = by_corpus.get(gid or "?", 0) + 1
        print(f"\n[{item['id']}] ({item['corpus']}) {item['question']}")
        print(f"  {verdict}  http={http} status={resp.get('status')} sufficient={resp.get('sufficient')} "
              f"confidence={resp.get('confidence')} latency={latency:.1f}s turn={resp.get('turn_id')}")
        if reasons:
            print(f"  why: {'; '.join(reasons)}")
        answer = " ".join(str(resp.get("answer") or "").split())
        print(f"  answer: {answer[:args.width]}{'...' if len(answer) > args.width else ''}")
        print(f"  sources: {len(ids)} node(s) {ids[:5]}; by corpus {by_corpus}; publications {[t for t in names if t][:3]}")
        results.append((item["id"], verdict, latency))
    warns = [r for r in results if r[1] == "WARN"]
    print(f"\nSUMMARY: {len(results) - len(warns)} PASS, {len(warns)} WARN of {len(results)}; "
          f"latency min/median/max {min(r[2] for r in results):.1f}/"
          f"{sorted(r[2] for r in results)[len(results) // 2]:.1f}/{max(r[2] for r in results):.1f}s" if results else "no questions")
    return 1 if (args.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
