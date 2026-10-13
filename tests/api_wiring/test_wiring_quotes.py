"""H2: quote verification on the legacy UI chat routes (O-D2: relevant quotes shown, page dumps blocked).

The real legacy UI app (T18A harness fakes; no network, no model) answers from a
retrieval agent whose merged context holds synthetic ``[PDF: f p.N]`` source-RAG
chunks, or from a paper-evidence agent over a synthetic one-page PDF. Every case
runs in the default runtime and with ``F2W_RUNTIME=langgraph`` (the T03 read path),
over ``/chat`` (JSON) and ``/chat/stream`` (SSE ``complete`` event).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from _wiring_support import H, REPO, make_cfg

from app.api.source_text import TurnSources, capture_turn, note_paper_evidence, note_retrieval, pdf_blocks
from app.modules.security.disclosure import QUOTE_OMITTED, SOURCE_TEXT_OMITTED

PDF = "synthetic_contrast.pdf"
CITE = f"[PDF: {PDF} p.3]"
CHUNK = ("Resonant soft X-ray scattering enhances the contrast between chemically similar polymer domains "
         "when the photon energy is tuned to an absorption edge of one component. The measured scattering "
         "intensity then reflects differences in the complex refractive index rather than electron density "
         "alone, which allows domain spacing to be resolved in blends that are invisible to hard X-rays. "
         "Sample thickness and beam damage limit the usable exposure time for each energy.")
QUOTE = "the measured scattering intensity then reflects differences in the complex refractive index"
MERGED = ("### Knowledge-graph candidates\nStructured KG hits (not PDF text). Cite as [KG:graph_id: name].\n"
          "- [KG: matkg:TermA] (Technique) score=1.000\n\n"
          "### Literature source evidence\nCite passages as [PDF: filename p.N]. These are journal/preprint "
          f"chunks, not beamline docs.\n{CITE}\n{CHUNK}\n\n"
          "### Beamline ops-doc evidence (bl1101)\nCite passages as [OPS: file §heading]. These are 11.0.1.2 / "
          "ALS ops docs, not papers.\n[OPS: ops_manual.md §Alignment]\nAlign the sample stage before every "
          "energy scan and record the reference intensity for normalization at each edge.")

ANSWERS = {
    "verbatim": f'The paper states that "{QUOTE[0].upper() + QUOTE[1:]}" {CITE}.',
    "paraphrase": f'The paper states that "the contrast comes from refractive index differences, not density" '
                  f'{CITE}.',
    "dump": f"Here is the page: {CHUNK} {CITE}",
    "dump_quoted": f'Here is the page: "{CHUNK}" {CITE}',
    "kg_only": 'Term A is "a synthetic contract node used for resonant scattering tests" [KG: matkg:TermA].',
    "ops_only": 'Per the manual, "align the sample stage before every energy scan and record the reference '
                'intensity" [OPS: ops_manual.md §Alignment].',
}


def _install_quoting_retrieval(monkeypatch, *, merged: Optional[str] = MERGED) -> Dict[str, Any]:
    """Wrap the harness retrieval fake: set its merged context and the answer for the question's key."""
    from app.modules.f2w_agent import api as api_mod

    base = api_mod.RetrievalAgent
    state: Dict[str, Any] = {"merged": merged}

    class QuotingRetrieval(base):
        async def query(self, question, *args, **kwargs):
            verdict = await super().query(question, *args, **kwargs)
            key = str(question).split(":", 1)[0].strip()
            self._trace_merged_context = state["merged"]
            if key in ANSWERS and isinstance(verdict, dict):
                verdict = dict(verdict, answer=ANSWERS[key])
            return verdict

    monkeypatch.setattr(api_mod, "RetrievalAgent", QuotingRetrieval)
    return state


def _app(monkeypatch, tmp_path, runtime: str, **kw):
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    if runtime == "langgraph":
        monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    else:
        monkeypatch.delenv("F2W_RUNTIME", raising=False)
    state = _install_quoting_retrieval(monkeypatch, **kw)
    services: List[Any] = []
    init = api_mod.AgentPipelineService.__init__
    monkeypatch.setattr(api_mod.AgentPipelineService, "__init__",
                        lambda self, cfg: (init(self, cfg), services.append(self))[0])
    app = api_mod.create_app(make_cfg(ctx, "json"))
    assert (services[-1]._read_path is not None) is (runtime == "langgraph")
    services[-1].test_retrieval_state = state
    return TestClient(app, raise_server_exceptions=False), services[-1], ctx


def _answer(client: TestClient, route: str, message: str, turn: str) -> str:
    """Answer for ``"<key>: ..."``; the harness fakes answer only Term A questions from the KG."""
    body: Dict[str, Any] = {"message": message, "client_turn_id": turn}
    if ":" in message:  # retrieval cases; the paper case uses the legacy session's workflow state
        body.update(message=message.split(":", 1)[0] + ": What is Term A resonant scattering?",
                    session_id="h2-session")
    resp = client.post(route, json=body)
    assert resp.status_code == 200
    if route == "/chat":
        return resp.json()["answer"]
    events = H.parse_sse(resp.content)
    final = [e for e in events if e["event"] in ("complete", "error")]
    assert len(final) == 1, events
    return final[0]["data"]["answer"]


RUNTIMES = ("legacy", "langgraph")
ROUTES = ("/chat", "/chat/stream")


@pytest.fixture(params=RUNTIMES)
def runtime(request):
    return request.param


@pytest.mark.parametrize("route", ROUTES)
def test_verbatim_quote_of_a_retrieved_chunk_keeps_its_quotes(monkeypatch, tmp_path, runtime, route):
    client, _svc, _ctx = _app(monkeypatch, tmp_path, runtime)
    answer = _answer(client, route, "verbatim: what does RSoXS contrast depend on?", "h2-verbatim")
    assert answer == ANSWERS["verbatim"]


@pytest.mark.parametrize("route", ROUTES)
def test_paraphrased_quote_is_downgraded_to_a_cited_statement(monkeypatch, tmp_path, runtime, route):
    client, _svc, _ctx = _app(monkeypatch, tmp_path, runtime)
    answer = _answer(client, route, "paraphrase: what does RSoXS contrast depend on?", "h2-para")
    assert answer == ANSWERS["paraphrase"].replace('"', "")
    assert CITE in answer


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind,marker", [("dump", SOURCE_TEXT_OMITTED), ("dump_quoted", QUOTE_OMITTED)])
def test_answer_echoing_most_of_a_region_is_clipped(monkeypatch, tmp_path, runtime, route, kind, marker):
    client, _svc, _ctx = _app(monkeypatch, tmp_path, runtime)
    answer = _answer(client, route, f"{kind}: print the page about RSoXS contrast", "h2-dump")
    assert marker in answer
    assert CHUNK not in answer and CITE in answer


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind", ["kg_only", "ops_only"])
def test_kg_and_ops_answers_are_unchanged(monkeypatch, tmp_path, runtime, route, kind):
    """Same projection as T18B (no PDF region cited: quotes become statements, nothing clipped)."""
    with_pdf, _s, _c = _app(monkeypatch, tmp_path / "a", runtime)
    expected = ANSWERS[kind].replace('"', "")
    assert _answer(with_pdf, route, f"{kind}: what is term A?", "h2-kg") == expected


@pytest.mark.parametrize("route", ROUTES)
def test_turn_without_pdf_regions_is_the_t18b_projection(monkeypatch, tmp_path, runtime, route):
    client, _svc, _ctx = _app(monkeypatch, tmp_path, runtime, merged=None)
    answer = _answer(client, route, "verbatim: what does RSoXS contrast depend on?", "h2-nosrc")
    assert answer == ANSWERS["verbatim"].replace('"', "")


def test_regions_are_per_turn(monkeypatch, tmp_path, runtime):
    """A later turn whose retrieval used no PDF text cannot verify against an earlier turn's regions."""
    client, svc, _ctx = _app(monkeypatch, tmp_path, runtime)
    assert _answer(client, "/chat", "verbatim: first", "h2-t1") == ANSWERS["verbatim"]
    svc.test_retrieval_state["merged"] = None
    assert _answer(client, "/chat/stream", "verbatim: second", "h2-t2") == ANSWERS["verbatim"].replace('"', "")


# -- paper-evidence path -----------------------------------------------------------------

PAGE = ("Synthetic paper page. Grazing incidence measurements were collected at the carbon edge. "
        "The lamellar period of the annealed film was twelve nanometres in every sample we examined. "
        "Further sections describe the detector geometry and the calibration procedure in detail, "
        "including the dark current subtraction and the flat field correction applied to each frame.")
PAGE_QUOTE = "The lamellar period of the annealed film was twelve nanometres in every sample we examined."
TITLE = "Synthetic lamellar annealing contrast"


def _write_pdf(path: Path) -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(36, 36, 560, 800), PAGE, fontsize=10)
    doc.save(str(path))
    doc.close()


def _install_paper_evidence(monkeypatch, answer: str) -> Dict[str, Any]:
    from app.modules.f2w_agent import api as api_mod

    seen: Dict[str, Any] = {"calls": 0}

    class FakePaperEvidence:
        def __init__(self, *args, **kwargs):
            pass

        async def query(self, question, pdf_path, manifest_path):
            seen["calls"] += 1
            seen["pdf_path"] = pdf_path
            return {"status": "success", "sufficient": True, "answer": answer, "used_pages": [1],
                    "missing_topics": []}

    monkeypatch.setattr(api_mod, "PaperEvidenceAgent", FakePaperEvidence)
    return seen


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind", ["verbatim", "paraphrase", "dump"])
def test_paper_evidence_page_quotes(monkeypatch, tmp_path, runtime, route, kind):
    cite = "[PDF: lamellar.pdf p.1]"
    answers = {
        "verbatim": f'The authors report: "{PAGE_QUOTE}" {cite}',
        "paraphrase": f'The authors report: "the period was about twelve nanometres in all annealed films" {cite}',
        "dump": f"{PAGE} {cite}",
    }
    seen = _install_paper_evidence(monkeypatch, answers[kind])
    client, svc, ctx = _app(monkeypatch, tmp_path, runtime)
    pdf = ctx.workspace / "pdfs" / "lamellar.pdf"
    _write_pdf(pdf)
    svc.workflow.update(current_topic="lamellar annealing", phase="paper_answered", active_paper={
        "paper_id": "synthetic", "filename": "lamellar.pdf", "path": str(pdf), "title": TITLE,
        "topic": "lamellar annealing", "status": "extracted"})
    answer = _answer(client, route, "What lamellar annealing period does lamellar.pdf report?", "h2-paper")
    assert seen["calls"] == 1
    if kind == "verbatim":
        assert answer == answers["verbatim"]
    elif kind == "paraphrase":
        assert answer == answers["paraphrase"].replace('"', "")
    else:
        assert SOURCE_TEXT_OMITTED in answer and cite in answer


# -- units -------------------------------------------------------------------------------


def test_pdf_blocks_keep_only_pdf_chunks():
    blocks = pdf_blocks(MERGED)
    assert blocks == {(PDF, 3): CHUNK}


def test_capture_is_request_bound_and_never_from_input():
    class Agent:
        _trace_merged_context = MERGED

    assert note_retrieval(Agent(), {"x": 1}) == {"x": 1}  # no active turn: nothing recorded, nothing raised
    with capture_turn() as turn:
        note_retrieval(Agent(), {})
        note_paper_evidence("/server/pdfs/a.pdf",
                            {"used_pages": [2], "answer": "x [PDF: a.pdf p.4] [PDF: b.pdf p.9]"})
    assert [r.citation for r in turn.refs()] == ["[PDF: a.pdf p.2]", "[PDF: a.pdf p.4]", CITE]


def test_reader_runs_only_under_a_quote_verification_grant(local):
    """The guard's verifier reads through SourceAccessScope; a foreign or revoked grant reads nothing."""
    from app.modules.security.content_access import SourceAccessDenied, SourceKind

    guard = local.app.state.f2w_legacy_ui
    turn = TurnSources()
    turn.add_merged_context(MERGED)
    reads: List[str] = []
    original = turn.reader_for

    def spy(refs):
        inner = original(refs)
        return lambda version, loc: (reads.append(version), inner(version, loc))[1]

    turn.reader_for = spy  # type: ignore[method-assign]
    from app.modules.security.authorization import local_operator_context

    ctx = local_operator_context(guard.settings, request_id="h2-req")
    projector, refs, grant = guard.turn_projector(ctx, turn)
    assert grant is not None and grant.purpose.value == "quote_verification"
    assert grant.kinds == frozenset({SourceKind.PARSED_TEXT}) and grant.work_ids == frozenset({PDF})
    shown = ANSWERS["verbatim"].split('"')[1]
    verdict = projector.verifier.verify(shown, work_id=PDF, source_version=refs[0].source_version,
                                        locator=refs[0].locator)
    assert verdict.verified and reads
    guard._source_authority.revoke(grant)
    with pytest.raises(SourceAccessDenied):
        projector.verifier.verify(shown, work_id=PDF, source_version=refs[0].source_version,
                                  locator=refs[0].locator)
    other = local_operator_context(guard.settings, request_id="h2-other")
    with pytest.raises(SourceAccessDenied):
        guard._source_authority.scope(other, grant).require(SourceKind.PARSED_TEXT, work_id=PDF)
    assert json.dumps([r.citation for r in refs]) == json.dumps([CITE])


def test_als_user_gets_verified_quotes(monkeypatch, tmp_path):
    """Same projection for an ordinary ALS user (N12); the grant binds to the verified context."""
    import _wiring_support as W

    install = H.install
    monkeypatch.setattr(H, "install",
                        lambda mp, ws, repo: (install(mp, ws, repo), _install_quoting_retrieval(mp))[0])
    als = W.build_als(monkeypatch, tmp_path)
    client = als.client("user")
    for key in ("verbatim", "paraphrase"):
        resp = client.post("/chat", json={"message": f"{key}: What is Term A resonant scattering?",
                                          "session_id": "h2-als",
                                          "client_turn_id": f"h2-als-{key}"})
        assert resp.status_code == 200
        expected = ANSWERS[key] if key == "verbatim" else ANSWERS[key].replace('"', "")
        assert resp.json()["answer"] == expected
