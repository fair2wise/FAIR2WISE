"""Characterization of the exact bytes the Coordinator writes to stdout/stderr.

Every user-facing line of ``Coordinator.run`` / ``_answer`` / ``_answer_agentic`` is pinned,
so the print -> ``f2w.output`` logging conversion cannot change what a user sees.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.f2w_agent import coordinator as coord_mod
from app.modules.f2w_agent.coordinator import Coordinator, CoordinatorConfig

Q = "\n=== Question: q ===\n"
OK = {"status": "success", "sufficient": False, "missing_topics": ["t1", "t2"],
      "graph_source_requested": "json", "graph_source_used": "json"}


class Retrieval:
    def __init__(self, verdicts, reload_res=None, reload_exc=None):
        self.verdicts = list(verdicts)
        self.reload_res = reload_res or {"graph_source_requested": "json", "graph_source_used": "json"}
        self.reload_exc = reload_exc

    async def query(self, question):
        v = self.verdicts.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    async def reload_kg(self, graph_file):
        if self.reload_exc:
            raise self.reload_exc
        return self.reload_res


class Download:
    def __init__(self, coord, result=None, exc=None, create=True, search=None, search_exc=None, search2=None):
        self.coord, self.result, self.exc, self.create = coord, result, exc, create
        self.search = search if search is not None else {"candidates": []}
        self.search_exc, self.search2 = search_exc, search2
        self.searches = 0

    def _go(self):
        if self.exc:
            raise self.exc
        if self.create:
            self.coord.pdf_dir.mkdir(parents=True, exist_ok=True)
            (self.coord.pdf_dir / "p.pdf").write_bytes(b"%PDF")
        return self.result if self.result is not None else {
            "count": 2, "candidates": 5, "failed": 1, "semantic_rejected": 3}

    async def find_and_download(self, *a, **k):
        return self._go()

    async def download_selected(self, *a, **k):
        return self._go()

    async def search_candidates(self, *a, **k):
        self.searches += 1
        if self.search_exc:
            raise self.search_exc
        if self.searches > 1 and self.search2 is not None:
            return self.search2
        return self.search


class Debate:
    def __init__(self, *summaries):
        self.summaries = list(summaries)

    async def decide(self, *a, **k):
        s = self.summaries.pop(0)
        if isinstance(s, Exception):
            raise s
        return s


class Extractor:
    def __init__(self, result=None, exc=None):
        self.result = {"status": "success", "unique_terms": 7} if result is None else result
        self.exc = exc

    async def extract(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.result

    extract_targeted = extract


def _coord(tmp_path, **cfg):
    cfg.setdefault("max_rounds", 1)
    return Coordinator(CoordinatorConfig(workdir=Path(tmp_path), **cfg))


@pytest.fixture(autouse=True)
def _kg(monkeypatch):
    monkeypatch.setattr(coord_mod.kg_update, "rebuild_kg", lambda t, k: {"nodes": 4, "edges": 5})
    monkeypatch.setattr(coord_mod.kg_update, "splash_reimport",
                        lambda *a, **k: {"status": "ok", "db_path": "/x/db"})


def _run(coord, fn, retrieval, download, extractor, debate=None):
    asyncio.run(getattr(coord, fn)("q", retrieval, download, extractor, *([debate] if debate else [])))


def _check(capsys, expected):
    cap = capsys.readouterr()
    assert cap.out == expected
    assert cap.err == ""


# ---------------------------------------------------------------- run()

class _Svc:
    def __init__(self, cfg):
        pass

    async def ask(self, question, auto_approve=False):
        return _Svc.resp


@pytest.mark.parametrize("resp,expected", [
    (SimpleNamespace(answer="", pending=None), Q.replace("q", "Q1")),
    (SimpleNamespace(answer="A1", pending=None), "\n=== Question: Q1 ===\n\n[Answer]\nA1\n"),
    (SimpleNamespace(answer="A1", pending={"kind": "download"}),
     "\n=== Question: Q1 ===\n\n[Answer]\nA1\n"
     "[approval required] Choose a candidate in a later ask/chat turn.\n"),
    (SimpleNamespace(answer="", pending={"kind": "extract"}),
     "\n=== Question: Q1 ===\n[approval required] Approve extraction in a later ask/chat turn.\n"),
])
def test_run_output(tmp_path, monkeypatch, capsys, resp, expected):
    import app.modules.f2w_agent.api as api
    _Svc.resp = resp
    monkeypatch.setattr(api, "AgentPipelineService", _Svc)
    asyncio.run(_coord(tmp_path).run(["Q1"]))
    _check(capsys, expected)


# ---------------------------------------------------------------- legacy _answer

def _legacy(tmp_path, capsys, expected, verdicts, *, dl=None, ext=None, reload=None, **cfg):
    c = _coord(tmp_path, workflow_mode="deterministic", **cfg)
    _run(c, "_answer", reload or Retrieval(verdicts), dl(c) if callable(dl) else Download(c), ext or Extractor())
    _check(capsys, expected)


def test_legacy_retrieval_exception(tmp_path, capsys):
    _legacy(tmp_path, capsys, Q + "[stop] retrieval failed: boom\n", [RuntimeError("boom")])


def test_legacy_retrieval_error_status(tmp_path, capsys):
    _legacy(tmp_path, capsys, Q + "[stop] retrieval failed: bad\n", [{"status": "x_error", "error": "bad"}])
    _legacy(tmp_path / "b", capsys, Q + "[stop] retrieval failed: x_error\n", [{"status": "x_error"}])


def test_legacy_fallback_and_answer(tmp_path, capsys):
    v = {"sufficient": True, "answer": "ans", "graph_source_requested": "splash", "graph_source_used": "json"}
    _legacy(tmp_path, capsys,
            Q + "[info] graph source fallback: requested=splash used=json\n\n[Answer]\nans\n", [v])


def test_legacy_download_exception(tmp_path, capsys):
    _legacy(tmp_path, capsys,
            Q + "[round 1] insufficient evidence; missing: ['t1', 't2']\n[stop] download failed: dl\n",
            [OK], dl=lambda c: Download(c, exc=RuntimeError("dl")))


def test_legacy_missing_defaults_to_question_and_no_papers(tmp_path, capsys):
    _legacy(tmp_path, capsys,
            Q + "[round 1] insufficient evidence; missing: ['q']\n"
            "[round 1] downloaded 0 paper(s) (from 0 candidates; failed=0, semantic_rejected=0)\n"
            "[stop] no new relevant papers found; cannot gather more evidence.\n",
            [{"status": "ok"}], dl=lambda c: Download(c, result={"count": 0}))


HEAD = (Q + "[round 1] insufficient evidence; missing: ['t1', 't2']\n"
        "[round 1] downloaded 2 paper(s) (from 5 candidates; failed=1, semantic_rejected=3)\n")


def test_legacy_no_unprocessed_pdfs(tmp_path, capsys):
    _legacy(tmp_path, capsys, HEAD + "[stop] no unprocessed PDFs available after download.\n",
            [OK], dl=lambda c: Download(c, create=False))


def test_legacy_extraction_failures(tmp_path, capsys):
    ex = "[round 1] extracting 1 new PDF(s)\n"
    _legacy(tmp_path / "a", capsys, HEAD + ex + "[stop] extraction failed: xx\n", [OK],
            ext=Extractor(exc=RuntimeError("xx")))
    _legacy(tmp_path / "b", capsys, HEAD + ex + "[stop] extraction failed: msg\n", [OK],
            ext=Extractor({"status": "error", "message": "msg"}))


def test_legacy_rebuild_exception(tmp_path, capsys, monkeypatch):
    def boom(t, k):
        raise RuntimeError("rb")
    monkeypatch.setattr(coord_mod.kg_update, "rebuild_kg", boom)
    _legacy(tmp_path, capsys, HEAD + "[round 1] extracting 1 new PDF(s)\n"
            "[round 1] extracted -> 7 unique terms\n[stop] KG rebuild failed: rb\n", [OK])


TAIL = ("[round 1] extracting 1 new PDF(s)\n[round 1] extracted -> 7 unique terms\n"
        "[round 1] KG rebuilt: 4 nodes, 5 edges\n")
MAXR = "[stop] reached max rounds (1) without sufficient evidence.\n"


def test_legacy_full_round_max_rounds(tmp_path, capsys):
    _legacy(tmp_path, capsys, HEAD + TAIL + MAXR, [OK])


def test_legacy_reload_fallback_and_splash(tmp_path, capsys):
    r = Retrieval([OK], reload_res={"graph_source_requested": "splash", "graph_source_used": "json"})
    _legacy(tmp_path, capsys, HEAD + TAIL + "[round 1] splash reimport: ok db=/x/db\n"
            "[info] graph source fallback after reload: requested=splash used=json\n" + MAXR,
            None, reload=r, kg_mode="splash")


def test_legacy_splash_no_db_note_and_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(coord_mod.kg_update, "splash_reimport", lambda *a, **k: {"status": "error"})
    _legacy(tmp_path, capsys, HEAD + TAIL + "[round 1] splash reimport: error\n"
            "[stop] splash reimport failed: {'status': 'error'}\n", [OK], kg_mode="splash")


def test_legacy_splash_exception(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sp")
    monkeypatch.setattr(coord_mod.kg_update, "splash_reimport", boom)
    _legacy(tmp_path, capsys, HEAD + TAIL + "[stop] splash reimport failed: sp\n", [OK], kg_mode="splash")


def test_legacy_reload_exception(tmp_path, capsys):
    _legacy(tmp_path, capsys, HEAD + TAIL + "[stop] KG reload failed: rl\n", None,
            reload=Retrieval([OK], reload_exc=RuntimeError("rl")))


# ---------------------------------------------------------------- agentic

def _ag(tmp_path, capsys, expected, verdicts, debate, *, dl=None, ext=None, reload=None, **cfg):
    c = _coord(tmp_path, workflow_mode="agentic", **cfg)
    d = dl(c) if callable(dl) else Download(c)
    _run(c, "_answer_agentic", reload or Retrieval(verdicts), d, ext or Extractor(), debate or Debate())
    _check(capsys, expected)


def test_agentic_dispatch_from_answer(tmp_path, capsys):
    c = _coord(tmp_path, workflow_mode="agentic")
    v = {"sufficient": True, "answer": "ans", "selected": [1, 2]}
    _run(c, "_answer", Retrieval([v]), Download(c), Extractor(), Debate({"selected_action": "answer_from_kg", "reason": "r"}))
    _check(capsys, Q + "[round 1] evidence probe: selected=2 sufficient=True\n"
           "[round 1] debate: answer_from_kg - r\n\n[Answer]\nans\n")


def test_agentic_retrieval_failures(tmp_path, capsys):
    _ag(tmp_path / "a", capsys, Q + "[stop] retrieval failed: boom\n", [RuntimeError("boom")], None)
    _ag(tmp_path / "b", capsys, Q + "[stop] retrieval failed: s_error\n", [{"status": "s_error"}], None)


PROBE = ("[round 1] evidence probe: selected=0 sufficient=False\n")
FB = Q + "[info] graph source fallback: requested=splash used=json\n"


def test_agentic_fallback_then_candidate_search_failure(tmp_path, capsys):
    v = {**OK, "graph_source_requested": "splash"}
    _ag(tmp_path, capsys, FB + PROBE + "[stop] candidate search failed: cs\n", [v], None,
        dl=lambda c: Download(c, search_exc=RuntimeError("cs")))


def test_agentic_candidate_titles_and_debate_failure(tmp_path, capsys):
    cands = [{"title": "T1"}, {"doi": "D2"}, {"id": "I3"}, {"title": "T4"}]
    _ag(tmp_path, capsys, Q + PROBE + "[round 1] candidate search: 4 candidate(s) - T1; D2; I3\n"
        "[stop] evidence debate failed: dbg\n", [OK], Debate(RuntimeError("dbg")),
        dl=lambda c: Download(c, search={"candidates": cands}))


def test_agentic_untitled_candidate_and_insufficient(tmp_path, capsys):
    _ag(tmp_path, capsys, Q + PROBE + "[round 1] candidate search: 1 candidate(s) - Untitled\n"
        "[round 1] debate: stop - nothing\n[stop] insufficient evidence: nothing\n",
        [OK], Debate({"selected_action": "stop", "reason": "nothing"}),
        dl=lambda c: Download(c, search={"candidates": [{}]}))


def test_agentic_no_candidates_answer_from_kg_without_sufficient(tmp_path, capsys):
    _ag(tmp_path, capsys, Q + PROBE + "[round 1] candidate search: 0 candidate(s)\n"
        "[round 1] debate: answer_from_kg - None\n[stop] insufficient evidence: None\n",
        [OK], Debate({"selected_action": "answer_from_kg"}))


def test_agentic_refine_search_paths(tmp_path, capsys):
    refine = {"selected_action": "refine_search", "refined_query": "rq"}
    base = Q + PROBE + "[round 1] candidate search: 0 candidate(s)\n[round 1] debate requested refined search: rq\n"
    _ag(tmp_path / "a", capsys, base + "[round 1] debate: stop - why\n[stop] insufficient evidence: why\n",
        [OK], Debate(refine, {"selected_action": "stop", "reason": "why"}))
    _ag(tmp_path / "b", capsys, base + "[stop] refined candidate search failed: rf\n",
        [OK], Debate(refine, RuntimeError("rf")))


def test_agentic_download_selected_no_candidate(tmp_path, capsys):
    _ag(tmp_path, capsys, Q + PROBE + "[round 1] candidate search: 0 candidate(s)\n"
        "[round 1] debate: download_selected - go\n"
        "[stop] debate approved download but no candidate was selected.\n",
        [OK], Debate({"selected_action": "download_selected", "reason": "go"}))


C1 = [{"title": "T1"}]
SEL = {"selected_action": "download_selected", "reason": "go", "candidate_indices": [0]}
AGHEAD = (Q + PROBE + "[round 1] candidate search: 1 candidate(s) - T1\n"
          "[round 1] debate: download_selected - go\n")
DLLINE = "[round 1] approved download 2 paper(s) (failed=1, semantic_rejected=3)\n"


def _agd(c):
    return Download(c, search={"candidates": C1})


def test_agentic_download_failures(tmp_path, capsys):
    _ag(tmp_path / "a", capsys, AGHEAD + "[stop] download failed: dl\n", [OK], Debate(SEL),
        dl=lambda c: Download(c, search={"candidates": C1}, exc=RuntimeError("dl")))
    _ag(tmp_path / "b", capsys, AGHEAD + "[round 1] approved download 0 paper(s) (failed=0, semantic_rejected=0)\n"
        "[stop] approved paper could not be downloaded; cannot gather more evidence.\n", [OK], Debate(SEL),
        dl=lambda c: Download(c, search={"candidates": C1}, result={"count": 0}))
    _ag(tmp_path / "c", capsys, AGHEAD + DLLINE + "[stop] no unprocessed PDFs available after download.\n",
        [OK], Debate(SEL), dl=lambda c: Download(c, search={"candidates": C1}, create=False))


TARG = "[round 1] targeted extraction from 1 approved PDF(s) (max_pages=6)\n"


def test_agentic_targeted_extraction_failures(tmp_path, capsys):
    _ag(tmp_path / "a", capsys, AGHEAD + DLLINE + TARG + "[stop] targeted extraction failed: te\n", [OK],
        Debate(SEL), dl=_agd, ext=Extractor(exc=RuntimeError("te")))
    _ag(tmp_path / "b", capsys, AGHEAD + DLLINE + TARG + "[stop] extraction failed: notadict\n", [OK],
        Debate(SEL), dl=_agd, ext=Extractor("notadict"))
    _ag(tmp_path / "c", capsys, AGHEAD + DLLINE + TARG + "[stop] extraction failed: m\n", [OK],
        Debate(SEL), dl=_agd, ext=Extractor({"status": "error", "message": "m"}))


FULL = "[round 1] extracting 1 approved PDF(s)\n"


def test_agentic_full_extraction_exception(tmp_path, capsys):
    _ag(tmp_path, capsys, AGHEAD + DLLINE + FULL + "[stop] extraction failed: fe\n", [OK], Debate(SEL),
        dl=_agd, ext=Extractor(exc=RuntimeError("fe")), extraction_mode="full")


def test_agentic_targeted_full_round_to_max_rounds(tmp_path, capsys):
    c = _coord(tmp_path, workflow_mode="agentic", extraction_mode="targeted")
    c._record_partial_extraction = lambda **k: None
    _run(c, "_answer_agentic", Retrieval([OK]), _agd(c), Extractor(), Debate(SEL))
    _check(capsys, AGHEAD + DLLINE + TARG + "[round 1] extracted -> 7 unique terms\n"
           "[round 1] KG rebuilt: 4 nodes, 5 edges\n" + MAXR)


def test_agentic_full_mode_splash_reload_fallback(tmp_path, capsys):
    r = Retrieval([OK], reload_res={"graph_source_requested": "a", "graph_source_used": "b"})
    _ag(tmp_path, capsys, AGHEAD + DLLINE + FULL + "[round 1] extracted -> 7 unique terms\n"
        "[round 1] KG rebuilt: 4 nodes, 5 edges\n[round 1] splash reimport: ok db=/x/db\n"
        "[info] graph source fallback after reload: requested=a used=b\n" + MAXR,
        None, Debate(SEL), dl=_agd, reload=r, extraction_mode="full", kg_mode="splash")


def test_agentic_rebuild_splash_reload_failures(tmp_path, capsys, monkeypatch):
    pre = AGHEAD + DLLINE + FULL + "[round 1] extracted -> 7 unique terms\n"
    kw = {"extraction_mode": "full", "dl": _agd}

    def boom(*a, **k):
        raise RuntimeError("bb")
    monkeypatch.setattr(coord_mod.kg_update, "rebuild_kg", boom)
    _ag(tmp_path / "a", capsys, pre + "[stop] KG rebuild failed: bb\n", [OK], Debate(SEL), **kw)
    monkeypatch.setattr(coord_mod.kg_update, "rebuild_kg", lambda t, k: {"nodes": 4, "edges": 5})
    kb = pre + "[round 1] KG rebuilt: 4 nodes, 5 edges\n"
    monkeypatch.setattr(coord_mod.kg_update, "splash_reimport", boom)
    _ag(tmp_path / "b", capsys, kb + "[stop] splash reimport failed: bb\n", [OK], Debate(SEL),
        kg_mode="splash", **kw)
    monkeypatch.setattr(coord_mod.kg_update, "splash_reimport", lambda *a, **k: {"status": "error", "message": "em"})
    _ag(tmp_path / "c", capsys, kb + "[round 1] splash reimport: error\n[stop] splash reimport failed: em\n",
        [OK], Debate(SEL), kg_mode="splash", **kw)
    _ag(tmp_path / "d", capsys, kb + "[stop] KG reload failed: rl\n", None, Debate(SEL),
        reload=Retrieval([OK], reload_exc=RuntimeError("rl")), **kw)
