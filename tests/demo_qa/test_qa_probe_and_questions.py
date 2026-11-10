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


def test_question_set_shape_and_expected_nodes_exist_in_tracked_graphs():
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    items = data["questions"]
    assert len(items) == 12 and len({i["id"] for i in items}) == 12
    assert sum(not i["answerable"] for i in items) == 3
    assert {i["corpus"] for i in items if i["answerable"]} == {"e2e_demo", "bl1101", "rsoxs_v1"}
    known: set[str] = set()
    for rel in data["graph_files"]:
        known |= {t["id"] for t in json.loads((ROOT / rel).read_text(encoding="utf-8"))["things"]}
    for item in items:
        assert all(n in known for n in item["expected_node_ids"]), item["id"]
        if item["answerable"]:
            assert item["expected_node_ids"] or item["expected_terms"], item["id"]


def test_judge_answerable_and_unanswerable():
    item = {"answerable": True, "expected_node_ids": ["n1"], "expected_terms": []}
    ok = {"status": "answered", "answer": "Fine.", "node_ids": ["n1", "n2"]}
    assert probe.judge(item, ok, 5, 45) == ("PASS", [])
    assert probe.judge(item, {**ok, "node_ids": ["n9"]}, 5, 45)[0] == "WARN"
    assert "hedge" in " ".join(probe.judge(item, {**ok, "answer": "The knowledge graph lacks slots."}, 5, 45)[1])
    assert "slow" in " ".join(probe.judge(item, ok, 60, 45)[1])
    terms = {"answerable": True, "expected_node_ids": [], "expected_terms": ["cyclotron"]}
    assert probe.judge(terms, {"status": "answered", "answer": "A cyclotron line", "node_ids": []}, 1, 45)[0] == "PASS"
    refuse = {"answerable": False}
    said = "*The knowledge graph has no information on this question.*\n\nParis."
    assert probe.judge(refuse, {"status": "answered", "answer": said}, 1, 45)[0] == "PASS"
    assert probe.judge(refuse, {"status": "answered", "answer": "Paris."}, 1, 45)[0] == "WARN"
    assert probe.judge(refuse, {"status": "retrieval_error", "answer": said}, 1, 45)[0] == "WARN"


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append({"path": self.path, "origin": self.headers.get("Origin"), **body})
        refuse = "France" in body["message"]
        payload = {"status": "answered", "sufficient": not refuse,
                   "answer": "*The knowledge graph has no information on this question.*\n\nParis." if refuse else "EPU polarization motor.",
                   "node_ids": [] if refuse else ["beamline:Motor-epu-polarization"], "confidence": 0.5,
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
        argv = ["--base", base, "--only", "bl-epu-polarization", "ooc-capital"] + (["--stream"] if stream else [])
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
