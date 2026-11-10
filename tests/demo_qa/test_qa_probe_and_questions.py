"""The demo question set and the QA probe: set integrity, verdict logic, and a loopback run against a fake API."""
from __future__ import annotations

import http.server
import importlib.util
import json
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "docs" / "migration" / "demo" / "qa_questions.json"
spec = importlib.util.spec_from_file_location("qa_probe", ROOT / "scripts" / "demo" / "qa_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_question_set_shape_and_expected_nodes_exist_in_the_expected_corpus():
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    items = data["questions"]
    assert len(items) == 11 and len({i["id"] for i in items}) == 11
    assert sum(not i["answerable"] for i in items) == 3
    corpora = set(data["graph_files"])
    assert corpora == {"rsoxs", "qwen3_235b_580papers"}
    answerable = [i for i in items if i["answerable"]]
    assert {c: sum(i["corpus"] == c for i in answerable) for c in corpora} == {"rsoxs": 4, "qwen3_235b_580papers": 4}
    assert all(i["corpus"] == "none" for i in items if not i["answerable"])
    known = {c: {t["id"] for t in json.loads((ROOT / rel).read_text(encoding="utf-8"))["things"]}
             for c, rel in data["graph_files"].items()}
    for item in answerable:
        assert item["expected_node_ids"], item["id"]
        assert all(n in known[item["corpus"]] for n in item["expected_node_ids"]), item["id"]


def test_judge_answerable_and_unanswerable():
    item = {"answerable": True, "corpus": "rsoxs", "expected_node_ids": ["n1"], "expected_terms": []}
    ok = {"status": "answered", "answer": "Fine.", "node_ids": ["n1", "n2"],
          "graph": {"nodes": [{"id": "n1", "graph_id": "rsoxs"}, {"id": "n2", "graph_id": "rsoxs"}]}}
    assert probe.judge(item, ok, 5, 45) == ("PASS", [])
    assert probe.judge(item, {**ok, "node_ids": ["n9"], "graph": {"nodes": []}}, 5, 45)[0] == "WARN"
    assert "hedge" in " ".join(probe.judge(item, {**ok, "answer": "The knowledge graph lacks slots."}, 5, 45)[1])
    assert "slow" in " ".join(probe.judge(item, ok, 60, 45)[1])
    terms = {"answerable": True, "corpus": "x", "expected_node_ids": [], "expected_terms": ["cyclotron"]}
    assert probe.judge(terms, {"status": "answered", "answer": "A cyclotron line", "node_ids": []}, 1, 45)[0] == "PASS"
    refuse = {"answerable": False}
    said = "*The knowledge graph has no information on this question.*\n\nParis."
    assert probe.judge(refuse, {"status": "answered", "answer": said}, 1, 45)[0] == "PASS"
    assert probe.judge(refuse, {"status": "answered", "answer": "Paris."}, 1, 45)[0] == "WARN"
    assert probe.judge(refuse, {"status": "retrieval_error", "answer": said}, 1, 45)[0] == "WARN"


def test_judge_requires_the_expected_node_from_the_expected_corpus():
    item = {"answerable": True, "corpus": "opv", "expected_node_ids": ["matkg:P3HT"], "expected_terms": []}
    base = {"status": "answered", "answer": "Fine."}
    # The shared id comes back prefixed (two corpora share it): the opv copy counts, the rsoxs copy does not.
    both = {**base, "node_ids": ["rsoxs:matkg:P3HT", "opv:matkg:P3HT"],
            "graph": {"nodes": [{"id": "rsoxs:matkg:P3HT", "graph_id": "rsoxs"}, {"id": "opv:matkg:P3HT", "graph_id": "opv"}]}}
    assert probe.judge(item, both, 5, 45) == ("PASS", [])
    wrong = {**base, "node_ids": ["matkg:P3HT"], "graph": {"nodes": [{"id": "matkg:P3HT", "graph_id": "rsoxs"}]}}
    verdict, why = probe.judge(item, wrong, 5, 45)
    assert verdict == "WARN" and "corpus opv" in why[0] and "only from ['rsoxs']" in why[0]
    ids_only = {**base, "node_ids": ["matkg:P3HT"]}                       # an older server: no graph ids, ids still count
    assert probe.judge(item, ids_only, 5, 45)[0] == "PASS"
    json_mode = {**base, "node_ids": ["matkg:P3HT"], "graph": {"nodes": [{"id": "matkg:P3HT", "graph_id": "opv_2"}]}}
    assert probe.judge(item, json_mode, 5, 45)[0] == "PASS"               # a suffixed graph id of the same corpus


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append({"path": self.path, "origin": self.headers.get("Origin"), **body})
        refuse = "France" in body["message"]
        payload = {"status": "answered", "sufficient": not refuse,
                   "answer": "*The knowledge graph has no information on this question.*\n\nParis." if refuse else "CyRSoXS simulates RSoXS.",
                   "node_ids": [] if refuse else ["matkg:CyRSoXS"], "confidence": 0.5,
                   "graph": {"nodes": [] if refuse else [{"id": "matkg:CyRSoXS", "graph_id": "rsoxs"}]},
                   "turn_id": "t1"}
        if self.path.endswith("/stream"):
            raw = f"event: progress\ndata: {json.dumps({'phase': 'x'})}\n\nevent: complete\ndata: {json.dumps(payload)}\n\n"
        else:
            raw = json.dumps(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(raw.encode())

    def log_message(self, *args):  # silence
        pass


@pytest.mark.parametrize("stream", [False, True])
def test_probe_runs_against_a_loopback_api_and_only_posts_chat(capsys, stream):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Handler.seen.clear()
    try:
        base = f"http://127.0.0.1:{server.server_port}/fair2wise/api"
        argv = ["--base", base, "--only", "rsoxs-simulation", "ooc-capital"] + (["--stream"] if stream else [])
        import sys
        old = sys.argv
        sys.argv = ["qa_probe.py", *argv]
        try:
            code = probe.main()
        finally:
            sys.argv = old
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert code == 0 and "2 PASS, 0 WARN" in out and "latency=" in out
    assert {s["path"] for s in _Handler.seen} == {f"/fair2wise/api/chat{'/stream' if stream else ''}"}
    assert all(s["origin"].startswith("http://127.0.0.1:") and s["session_id"].startswith("qaprobe-") for s in _Handler.seen)
    assert len({s["session_id"] for s in _Handler.seen}) == 2


def test_probe_reports_an_unreachable_api(capsys):
    import sys
    old = sys.argv
    sys.argv = ["qa_probe.py", "--base", "http://127.0.0.1:9/x", "--only", "ooc-capital", "--timeout", "2"]
    try:
        assert probe.main() == 2
    finally:
        sys.argv = old
