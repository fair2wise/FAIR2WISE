"""T19B: the legacy UI paths that show the extracted-term summary, served through the Tool.

Before = the unchanged legacy server. After = the same server with
``api.summarize_extracted_terms`` (the name both call sites use) replaced by
``LegacySummaryPath.summarize_extracted_terms`` of a per-pipeline adapter, so
every summary goes through the executor and ``lit_summarize_extracted_terms``.
``api.py`` itself is not modified.

1. The frozen T18A fixtures (tests/contracts/legacy_ui) replay byte-identically
   with the adapter; ``chat_action_stream_extraction_yes`` reaches the
   post-extraction summary (``max_terms=6``, query-scoped) through the Tool.
2. A new synthetic before/after fixture (tests/operations/fixtures) covers the
   ``extraction_reported`` follow-up (``max_terms=16``) and a capped, partial
   summary, over ``/chat/stream`` (raw SSE bytes) and ``/chat`` (JSON). No real
   paper text is used. Re-record with ``T19B_RECORD=1`` (legacy mode only).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

LEGACY_UI = Path(__file__).resolve().parents[1] / "contracts" / "legacy_ui"
sys.path.insert(0, str(LEGACY_UI))

import _legacy_ui_harness as H  # noqa: E402

from tests.operations.summary_fakes import FILENAME, many_terms, term  # noqa: E402

REPO_ROOT = LEGACY_UI.parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CASES = {p.stem: json.loads(p.read_text(encoding="utf-8"))
         for p in sorted((H.FIXTURE_DIR / "http").glob("*.json"))}
UI_STEPS = [step for flow in H.flows().values() for step in flow["steps"]]
TITLE = "Synthetic study of resonant scattering contrast"
QUESTION = f"Summarize the terms you extracted from the paper: {TITLE}?"


def _install_adapter(mp, record: Dict[str, Any]) -> None:
    """Route both api.py call sites through a per-pipeline LegacySummaryPath."""
    from app.modules.f2w_agent import api as api_mod
    from app.modules.operations.papers import PaperSummaryTool
    from app.modules.operations.summary_compat import build_legacy_summary_path

    paths: Dict[str, Any] = {}
    original_init = api_mod.AgentPipelineService.__init__
    original_run = PaperSummaryTool.run

    def init(self, cfg):
        original_init(self, cfg)
        paths[str(self.coord.session_terms)] = build_legacy_summary_path(self, environ={})

    def run(self, inv):
        record["tool_runs"].append(sorted(inv.args))
        return original_run(self, inv)

    def summarize(terms_path, manifest_path, filename, **kw):
        record["calls"].append({"filename": filename, "max_terms": kw.get("max_terms", 16)})
        path = paths[str(terms_path)]
        runs = len(record["tool_runs"])
        result = path.summarize_extracted_terms(terms_path, manifest_path, filename, **kw)
        record["through_tool"].append(path.fallbacks == 0 and len(record["tool_runs"]) == runs + 1)
        return result

    mp.setattr(api_mod.AgentPipelineService, "__init__", init)
    mp.setattr(PaperSummaryTool, "run", run)
    mp.setattr(api_mod, "summarize_extracted_terms", summarize)


# -- 1. frozen T18A fixtures -------------------------------------------------------------

@pytest.fixture(scope="module")
def frozen_replay(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        ctx = H.install(mp, tmp_path_factory.mktemp("t19b-legacy-ui") / "ws", REPO_ROOT)
        record: Dict[str, Any] = {"calls": [], "through_tool": [], "tool_runs": [], "by_fid": {}}
        _install_adapter(mp, record)
        responses, raw = {}, {}
        for flow in H.flows().values():
            client = H.client_for(H.make_ui_app(ctx, kg_mode=flow["kg_mode"]))
            for fid, method, path, kwargs, _notes in flow["steps"]:
                start = len(record["calls"])
                resp = H.perform(client, ctx, method, path, kwargs)
                responses[fid] = H.response_record(resp, ctx)
                raw[fid] = resp.content
                if len(record["calls"]) > start:
                    record["by_fid"][fid] = record["calls"][start:]
        yield {"ctx": ctx, "responses": responses, "raw": raw, "record": record}
    finally:
        mp.undo()


def test_frozen_extraction_flow_reaches_the_summary_through_the_tool(frozen_replay):
    record = frozen_replay["record"]
    assert record["by_fid"] == {"chat_action_stream_extraction_yes": [{"filename": "candidate_one.pdf",
                                                                       "max_terms": 6}]}
    assert record["through_tool"] == [True]


@pytest.mark.parametrize("fid", [step[0] for step in UI_STEPS])
def test_frozen_fixture_is_byte_identical_with_the_adapter(frozen_replay, fid):
    expected = CASES[fid]["response"]
    actual = frozen_replay["responses"][fid]
    assert actual["status_code"] == expected["status_code"]
    if "sse_events" in expected:
        assert actual["sse_events"] == expected["sse_events"]
        raw = (H.FIXTURE_DIR / expected["sse_file"]).read_bytes()
        assert H.sanitize_text(frozen_replay["raw"][fid].decode("utf-8"), frozen_replay["ctx"]).encode() == raw
    elif "json" in expected:
        assert actual["json"] == expected["json"]
    else:
        assert actual.get("text") == expected.get("text")


# -- 2. synthetic before/after for extraction_reported ------------------------------------------

SYNTHETIC = {
    "report_small": ({"extraction_state": "partial", "selected_pages": [2, 3]},
                     [term("Resonant contrast", [2], definition="Synthetic contrast mechanism.",
                           category="Concept"),
                      term("Hidden early term", [1]),
                      term("Detector geometry", [3], definition="Synthetic setup.", category="Equipment")]),
    "report_capped": ({"extraction_state": "full"}, many_terms(21, [1, 2, 5])),
}


def _run_report(tmp_path: Path, name: str, *, adapter: bool) -> Dict[str, Any]:
    entry, terms = SYNTHETIC[name]
    mp = pytest.MonkeyPatch()
    try:
        ctx = H.install(mp, tmp_path / ("after" if adapter else "before") / name / "ws", REPO_ROOT)
        record: Dict[str, Any] = {"calls": [], "through_tool": [], "tool_runs": []}
        if adapter:
            _install_adapter(mp, record)
        from app.modules.f2w_agent import api as api_mod

        services: List[Any] = []
        init = api_mod.AgentPipelineService.__init__
        mp.setattr(api_mod.AgentPipelineService, "__init__",
                   lambda self, cfg: (init(self, cfg), services.append(self))[0])
        client = H.client_for(H.make_ui_app(ctx, kg_mode="json"))
        service = services[-1]
        service.coord.extraction_manifest.write_text(json.dumps({"papers": {FILENAME: entry}}), encoding="utf-8")
        service.coord.session_terms.write_text(json.dumps({"terms": terms}), encoding="utf-8")
        out: Dict[str, Any] = {"record": record}
        for route, body in (("/chat/stream", {"message": QUESTION, "client_turn_id": "t19b-turn-stream"}),
                            ("/chat", {"message": QUESTION, "client_turn_id": "t19b-turn-json"})):
            service.workflow.update(current_topic="resonant scattering", phase="paper_insufficient", active_paper={
                "paper_id": "synthetic", "filename": FILENAME, "path": str(ctx.workspace / "pdfs" / FILENAME),
                "title": TITLE, "topic": "resonant scattering", "status": "extracted"})
            resp = client.post(route, json=body)
            rec = H.response_record(resp, ctx)
            if route == "/chat/stream":
                out["sse_raw"] = H.sanitize_text(resp.content.decode("utf-8"), ctx)
                out["sse_events"] = rec["sse_events"]
            else:
                out["json"] = rec["json"]
        return out
    finally:
        mp.undo()


def _fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(SYNTHETIC))
def test_synthetic_report_before_matches_recorded_fixture(tmp_path, name):
    before = _run_report(tmp_path, name, adapter=False)
    body = {"question": QUESTION, "sse_raw": before["sse_raw"], "json": before["json"]}
    if os.environ.get("T19B_RECORD") == "1":
        FIXTURES.mkdir(exist_ok=True)
        (FIXTURES / f"{name}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
    assert body == _fixture(name)
    assert before["json"]["status"] == "extraction_reported"


@pytest.mark.parametrize("name", sorted(SYNTHETIC))
def test_synthetic_report_after_is_byte_identical(tmp_path, name):
    after = _run_report(tmp_path, name, adapter=True)
    expected = _fixture(name)
    assert after["sse_raw"].encode("utf-8") == expected["sse_raw"].encode("utf-8")
    assert json.dumps(after["json"], sort_keys=True) == json.dumps(expected["json"], sort_keys=True)
    assert [c["max_terms"] for c in after["record"]["calls"]] == [16, 16]
    assert after["record"]["through_tool"] == [True, True]
    assert after["record"]["tool_runs"] == [["max_terms"], ["max_terms"]]  # no path or filename argument


def test_capped_fixture_shows_the_partial_summary_as_the_legacy_text(tmp_path):
    expected = _fixture("report_capped")
    answer = expected["json"]["answer"]
    assert answer.startswith("Extracted 21 page-grounded terms; key terms:")
    assert answer.endswith("- 5 additional terms omitted from this compact summary.")
    assert answer.count("[PDF: synthetic_paper.pdf p.") == 16
