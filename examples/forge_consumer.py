"""FORGE-style consumer of F2W literature services (task T15, owner decision B30, 2026-10-30).

Shows the two consumption paths the migration plan requires of FORGE, against offline
fakes only (no network, no model key, no Tiled, no ``.env``):

(a) **in-process**: ``import f2w.public`` (``app.public`` in a checkout), build one
    ``F2WClient`` with an explicit lifecycle (construct, use, close), run the direct SDK
    calls (search, resolve, read, evidence bundle) and one *delegated* literature task
    through ``create_literature_agent``;
(b) **HTTP**: the equivalent ``/v1`` calls behind the ``/fair2wise/api`` gateway prefix,
    printed as ``curl`` lines and, with ``--testclient`` inside an F2W checkout, executed
    against the real FastAPI app with Starlette's ``TestClient`` (job submit, SSE stream
    with its single terminal event, cancel, a Knowledge Operation read).

Run from a checkout::

    python examples/forge_consumer.py            # path (a) and the curl lines of (b)
    python examples/forge_consumer.py --testclient
    python examples/forge_consumer.py --json     # machine-readable summary

What FORGE keeps: the mapping from the evidence bundle's observations to simulation
parameters, its own KG/run/sample tools, and the translation of ``ToolResult.status``
into its tool envelope *without* hiding ``denied``/``unavailable``/``partial``
(see ``translate_envelope`` below). Nothing here is FORGE code; the FORGE-side
adapters wait for the owner and Alex (docs/migration/forge_spec.md).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# -- one package name per process (ADR package-and-imports): f2w when installed, app in a checkout ---
_CHECKOUT = Path(__file__).resolve().parents[1]
if importlib.util.find_spec("f2w") is not None:
    PKG = "f2w"
else:  # a checkout: run as ``python examples/forge_consumer.py`` with the repo root importable
    if importlib.util.find_spec("app") is None and (_CHECKOUT / "app" / "__init__.py").exists():
        sys.path.insert(0, str(_CHECKOUT))
    PKG = "app"


def f2w(name: str) -> Any:
    """Import ``<package>.<name>`` under the one name this process uses."""
    return importlib.import_module(f"{PKG}.{name}")


public = f2w("public")

DOI = "10.1/a"
WORK_ID = f"doi:{DOI}"
GATEWAY = "https://<als-host>/fair2wise/api"  # the public prefix; routes stay rooted at /v1 inside


# -------------------------------------------------------------------------- offline fixtures


def minimal_pdf_bytes(text: str = "Hello", *, pad: int = 2000) -> bytes:
    """A small, structurally valid one-page PDF (no PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    out += b"%" + b"x" * pad + b"\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


SECTIONS = (
    ("Abstract", "We report resonant soft X-ray scattering (RSoXS) of PM6:Y6 blend films.", 1),
    ("Results", "The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film. "
                "The film thickness was 105 ± 5 nm as measured by profilometry. "
                "The domain spacing is 25 nm after annealing.", 3),
    ("Discussion", "Previous reports found a domain spacing of 40 nm [12].", 5),
)


class DemoParser:
    """Deterministic parser: the same three sections for any PDF (no PDF library)."""

    name = "demo"

    def version(self) -> str:
        return "1.0"

    def config(self) -> Dict[str, Any]:
        return {"fixture": "forge_consumer"}

    def parse(self, pdf_path: Path, work_dir: Path, *, source_ref: str) -> Any:
        parsed = f2w("modules.literature.parsed_document")
        return parsed.ParsedDocument(
            source_ref=source_ref, markdown="\n\n".join(f"# {n}\n{t}" for n, t, _ in SECTIONS), num_pages=7,
            parser=self.name, parser_version=self.version(), parser_config=self.config(),
            sections=tuple(parsed.ParsedSection(name=n, text=t, page=p, level=1) for n, t, p in SECTIONS),
        )


class StubRetrievalAgent:
    """The existing QA path's agent, scripted (what ``answer_from_knowledge`` wraps)."""

    def __init__(self) -> None:
        self.calls = 0

    def set_live_tiled(self, enabled: bool, *, uri: Optional[str] = None) -> None:
        pass

    async def query(self, question: str, history: Any = None, source_rag: Any = None) -> Dict[str, Any]:
        self.calls += 1
        return {"status": "success", "question": question, "sufficient": True,
                "answer": "RSoXS probes chemical contrast [KG: demo: RSoXS].", "missing_topics": [],
                "selected": ["RSoXS"], "no_evidence": False,
                "selected_hits": [{"id": "RSoXS", "graph_id": "demo"}], "direct_evidence_count": 1,
                "graph_ids": ["demo"]}


def openalex_work(doi: str, title: str) -> Dict[str, Any]:
    """An OpenAlex work record shaped like the API's JSON."""
    return {"id": "https://openalex.org/W1", "doi": f"https://doi.org/{doi}", "title": title,
            "display_name": title, "publication_year": 2021,
            "authorships": [{"author": {"display_name": "Ada Lovelace"}, "institutions": []}],
            "primary_location": {"source": {"display_name": "J. Synchrotron"}, "pdf_url": None},
            "best_oa_location": {"pdf_url": None, "is_oa": False}, "open_access": {"is_oa": False, "oa_url": None},
            "cited_by_count": 3, "concepts": [{"display_name": "Scattering"}],
            "abstract_inverted_index": {"RSoXS": [0], "films": [1]}, "referenced_works": [], "ids": {}}


def offline_openalex_client() -> Any:
    """An ``OpenAlexClient`` over an ``httpx.MockTransport``: nothing leaves the process."""
    import httpx

    oa = f2w("modules.literature.openalex")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/works/doi:"):
            if path.endswith(DOI):
                return httpx.Response(200, json=openalex_work(DOI, "RSoXS of PM6:Y6 films"))
            if path.endswith("10.1/down"):
                return httpx.Response(500)
            return httpx.Response(404)
        if path.startswith("/works"):
            search = request.url.params.get("search", "")
            if "nothing" in search:
                return httpx.Response(200, json={"results": []})
            if "down" in search:
                return httpx.Response(503)
            return httpx.Response(200, json={"results": [openalex_work(DOI, "RSoXS of PM6:Y6 films")]})
        raise AssertionError(f"unexpected offline request {request.method} {request.url}")

    return oa.OpenAlexClient(httpx.Client(transport=httpx.MockTransport(handler)),
                             cache=oa.ResponseCache(":memory:"), breaker=oa.QuotaBreaker(),
                             settings=oa.OpenAlexSettings(), sleep=lambda s: None)


@dataclass
class Fixtures:
    """Everything a consumer wires into one client (built by trusted host code, never by the model)."""

    settings: Any
    retrieval: Any
    literature: Any
    evidence: Any
    authority: Any


def build_fixtures(root: Path, *, with_scope: bool = True) -> Fixtures:
    """Offline toolset (OpenAlex fake + stored demo paper), candidate evidence sink and QA stub."""
    root = Path(root)
    store_mod, legacy, parse_service = (f2w("modules.literature.store"), f2w("modules.literature.legacy"),
                                        f2w("modules.literature.parse_service"))
    reading, tools, content = (f2w("modules.literature.reading"), f2w("modules.literature.tools"),
                               f2w("modules.security.content_access"))
    evidence, records = f2w("modules.evidence"), f2w("modules.evidence.records")
    retrieval_mod, authz = f2w("modules.runtime_bridge.retrieval"), f2w("modules.security.authorization")
    ctxmod = f2w("modules.contracts.context")

    settings = authz.DeploymentSettings(profile="local", writer_identity="f2w-writer")
    # The stored paper: adopted under its legacy filename so ``read_paper`` finds it without a download.
    store = store_mod.FileDocumentStore(root / "store")
    index = legacy.LegacyIndex(store)
    papers = root / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    (papers / (DOI.replace("/", "_") + ".pdf")).write_bytes(minimal_pdf_bytes("demo paper"))
    index.adopt_tree(authz.local_operator_context(settings, request_id="fixtures"), "papers", papers)
    reader = reading.PaperReader(store, index, parse_service.ParseService(store, [DemoParser()]))
    authority = content.SourceAccessAuthority()
    toolset = tools.LiteratureToolset(
        offline_openalex_client(), reader,
        scope_provider=tools.authority_scope_provider(authority) if with_scope else None)
    # Candidate evidence of the same Work, written by the T06 rules over the parsed source version.
    document = DemoParser().parse(Path("unused"), root, source_ref="sha256:" + "a" * 64)
    sink = evidence.InMemoryEvidenceSink()
    quantities = [
        records.ReportedQuantity(
            name="first_peak_q", raw_value=0.30, raw_unit="Å⁻¹", location_label="Results",
            quote="The lamellar stacking peak appears at q = 0.30 Å⁻¹ for the annealed film."),
        records.ReportedQuantity(
            name="film_thickness", raw_value=105.0, raw_unit="nm", location_label="Results",
            quote="The film thickness was 105 ± 5 nm as measured by profilometry.",
            uncertainty=records.MeasuredUncertainty(5.0, records.UncertaintyKind.STANDARD_DEVIATION)),
        records.ReportedQuantity(
            name="domain_spacing", raw_value=40.0, raw_unit="nm", location_label="Discussion",
            evidence_source=records.EvidenceSource.CITED,
            quote="Previous reports found a domain spacing of 40 nm [12]."),
    ]
    evidence.record_reported_quantities(sink, document, quantities, work_id=WORK_ID,
                                        pub_path=f"publications/{DOI}", strategy="demo/1")
    del ctxmod  # context objects come from the client, never from here
    return Fixtures(settings=settings, retrieval=retrieval_mod.LegacyRetrievalService(StubRetrievalAgent()),
                    literature=toolset, evidence=sink, authority=authority)


def scripted_supervisor_model(task_tool: str, args: Dict[str, Any]) -> Any:
    """A deterministic LangChain chat model: call ``task_tool`` once, then relay the tool's status."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Scripted(BaseChatModel):
        turn: int = 0

        @property
        def _llm_type(self) -> str:
            return "forge-consumer-scripted"

        def bind_tools(self, tools: Sequence[Any], **kw: Any) -> "Scripted":
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
            self.turn += 1
            if self.turn == 1:
                message = AIMessage(content="", tool_calls=[{"name": task_tool, "args": args, "id": "c1",
                                                             "type": "tool_call"}])
            else:
                body = json.loads(messages[-1].content)
                count = (body.get("data") or {}).get("count", 0)
                message = AIMessage(content=f"[{body['status']}] {count} candidate paper(s); none ingested")
            return ChatResult(generations=[ChatGeneration(message=message)])

    return Scripted()


# -------------------------------------------------------------------------- FORGE envelope


def translate_envelope(result: Any) -> Dict[str, Any]:
    """Translate a typed ``ToolResult`` to a FORGE-style envelope without hiding states.

    FORGE's tools return ``{"ok": bool, ...}``; the rich F2W states survive as ``state`` and
    ``error_code``: ``denied``, ``unavailable`` (with ``retryable``) and ``partial`` are never
    collapsed into ``ok`` or into a generic failure.
    """
    status = result.status.value
    return {"ok": status in ("ok", "partial", "empty"), "state": status, "partial": status == "partial",
            "error_code": result.error_code.value if result.error_code else None, "retryable": result.retryable,
            "warnings": list(result.warnings), "evidence_ids": result.evidence_ids, "data": result.data}


# -------------------------------------------------------------------------- path (a): in-process


def sdk_path(root: Path, *, out: Any = print) -> Dict[str, Any]:
    """Construct, use and close one client; return a JSON-ready summary of every step."""
    fx = build_fixtures(root)
    summary: Dict[str, Any] = {"package": PKG}
    # Lifecycle: the client owns its application; ``with`` closes what it opened. Two clients would
    # share no registry, executor or ledger (one FORGE process may hold its own agents beside it).
    with public.F2WClient.from_settings(fx.settings, retrieval=fx.retrieval, literature=fx.literature,
                                        evidence=fx.evidence) as client:
        ctx = client.context_for(session_id="forge-demo")  # local-operator on :local; a verified principal on ALS
        summary["principal"] = ctx.principal.subject

        search = client.search_literature(ctx, "RSoXS PM6:Y6", limit=5)
        summary["search"] = translate_envelope(search)
        out(f"search_literature -> {search.status.value}: {search.data['count']} external candidate(s), "
            f"record_kind={search.data['record_kind']}")

        resolved = client.resolve_work(ctx, f"https://doi.org/{DOI}")
        summary["resolve"] = translate_envelope(resolved)
        out(f"resolve_work -> {resolved.status.value}: {resolved.data['papers'][0]['title']!r}")

        paper = client.read_paper(ctx, DOI)
        summary["read_paper"] = translate_envelope(paper)
        out(f"read_paper -> {paper.status.value}: sections={paper.data['section_names']} "
            f"source_version={paper.data['source_version'][:12]}...")

        section = client.read_section(ctx, DOI, "results")
        summary["read_section"] = translate_envelope(section)
        out(f"read_section -> {section.status.value}: page {section.data['page']}, "
            f"{len(section.data['text'])} chars, truncated={section.data['truncated']} "
            "(agent-private; disclose() before showing a person)")
        summary["read_section_disclosed"] = client.disclose(section)

        bundle = client.evidence_bundle(ctx, WORK_ID)
        text = bundle.to_json()
        back = type(bundle).from_json(text)
        assert back == bundle
        summary["bundle"] = back.to_dict()
        out(f"evidence_bundle -> {len(bundle.quantities)} quantities, {len(bundle.evidence)} evidence records, "
            f"source_sha256={bundle.source_sha256[:12]}..., caveats={len(bundle.caveats)}; JSON round-trip ok")
        for q in bundle.quantities:
            out(f"  {q.name}: {q.raw_value} {q.raw_unit} -> {q.value} {q.unit or ''} [{q.evidence_source}] "
                f"evidence={list(q.evidence_ids)}")

        wanted = [*bundle.evidence_ids, "evidence:missing"]
        found = client.get_evidence(ctx, wanted)
        summary["get_evidence"] = translate_envelope(found)
        out(f"get_evidence -> {found.status.value}: {found.data['found']}/{found.data['requested']} "
            f"(missing: {found.data['missing']})")

        down = client.search_literature(ctx, "provider down")
        summary["unavailable"] = translate_envelope(down)
        out(f"search_literature (provider down) -> {down.status.value}, retryable={down.retryable}: "
            f"FORGE envelope keeps it: {translate_envelope(down)['state']}")

        answer = client.answer_from_knowledge_sync(ctx, "What does RSoXS probe?")
        summary["answer"] = answer.to_legacy()["answer"]
        out(f"answer_from_knowledge -> {answer.result.status.value}: {summary['answer']}")

    # A toolset without a trusted source-access scope: reads are DENIED, never silently empty.
    denied_fx = build_fixtures(root / "denied", with_scope=False)
    with public.F2WClient.from_settings(denied_fx.settings, retrieval=denied_fx.retrieval,
                                        literature=denied_fx.literature) as client:
        denied = client.read_section(client.context_for(), DOI, "results")
        summary["denied"] = translate_envelope(denied)
        out(f"read_section without a source-access scope -> {denied.status.value} ({denied.error_code.value})")

    # Delegated literature task: the LangGraph supervisor over the same registry, model injected by the caller.
    model = scripted_supervisor_model("lit_search_papers", {"query": "RSoXS PM6:Y6", "max_results": 3})
    with public.create_literature_agent(settings=fx.settings, retrieval=fx.retrieval, chat_model=model,
                                        literature=fx.literature) as agent:
        ctx = agent.client.context_for(session_id="forge-demo")
        outcome = asyncio.run(agent.delegate(ctx, "Find recent RSoXS papers on PM6:Y6", thread_id="t-demo"))
        summary["delegated"] = {"answered": outcome.answered, "final_text": outcome.final_text,
                                "tools": [r.tool for r in outcome.tool_results],
                                "statuses": [r.status.value for r in outcome.tool_results]}
        out(f"delegate -> answered={outcome.answered}; tools={summary['delegated']['tools']}; "
            f"reply={outcome.final_text!r}")
    return summary


# -------------------------------------------------------------------------- path (b): HTTP


CURL_LINES = f"""# Equivalent HTTP calls behind the gateway (ALS: browser session cookie; :local: no sign-in).
# Routes are rooted at /v1 inside the app; the gateway publishes them under {GATEWAY}.
C="--cookie f2w_session=<cookie>"   # omit on :local
curl $C -X POST {GATEWAY}/v1/jobs -H 'content-type: application/json' \\
     -d '{{"kind":"ingest","inputs":{{"corpus_scope":["rsoxs"],"source_sha256":"<64 hex>",
             "source_version":"v1"}}}}'
curl $C {GATEWAY}/v1/jobs/<job_id>                       # JobHandle (same job_id as the SDK's get_job)
curl $C -N {GATEWAY}/v1/jobs/<job_id>/stream             # SSE: job_event* then exactly one 'end' event
curl $C -X POST {GATEWAY}/v1/jobs/<job_id>/cancel        # -> state 'cancelled'; the stream then ends 'terminal'
curl $C '{GATEWAY}/v1/operations/paper-subgraph?graph_id=rsoxs&paper=paper_a.pdf&max_nodes=5'
#   -> the same bounded ToolResult F2WClient.paper_subgraph returns (status ok|partial, never hidden)
# Error envelopes: {{"code": "denied"|"not_found"|"unavailable"|..., "message": ..., "retryable": bool}}
# No /v1/literature route exists yet (search/read are SDK-only today; see docs/migration/public_api_spec.md)."""


def parse_sse(text: str) -> List[Any]:
    """``(event, id, data)`` triples of an SSE body in the legacy framing."""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((fields.get("event"), fields.get("id"), json.loads(fields.get("data", "null"))))
    return events


def http_path_testclient(repo_root: Path, workdir: Path, *, out: Any = print) -> Dict[str, Any]:
    """Run the HTTP path against the real ``create_api_app`` over the checkout's offline test wiring.

    Needs an F2W checkout (``tests/api_v1/_v1_support.py``): the harness blocks sockets, fakes the
    job pipeline and refuses to run next to a ``.env``.
    """
    import pytest

    sys.path.insert(0, str(repo_root / "tests" / "api_v1"))
    sys.path.insert(0, str(repo_root))
    support = importlib.import_module("_v1_support")
    mp = pytest.MonkeyPatch()
    summary: Dict[str, Any] = {}
    try:
        v1 = support.build_local(mp, workdir)
        c = v1.client()
        job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": support.ingest_inputs()}).json()
        out(f"POST /v1/jobs -> {job['state']} {job['job_id']}")
        v1.work()  # what the worker does
        events = parse_sse(c.get(f"/v1/jobs/{job['job_id']}/stream").text)
        kinds = [e[0] for e in events]
        summary["stream"] = {"events": kinds, "end": events[-1][2]}
        out(f"GET /v1/jobs/{{id}}/stream -> {kinds.count('job_event')} job_event(s), "
            f"{kinds.count('end')} end event: {events[-1][2]}")
        second = c.post("/v1/jobs", json={"kind": "ingest", "inputs": support.ingest_inputs(wait_for_review=True)}
                        ).json()
        cancelled = c.post(f"/v1/jobs/{second['job_id']}/cancel").json()
        after = parse_sse(c.get(f"/v1/jobs/{second['job_id']}/stream").text)
        summary["cancel"] = {"state": cancelled["state"], "end": after[-1][2]}
        out(f"POST /v1/jobs/{{id}}/cancel -> {cancelled['state']}; stream end: {after[-1][2]}")
        # The same call through the SDK against the same operations service: identical job ids and states.
        app_mod = importlib.import_module(f"{PKG}.modules.application")
        retrieval_mod = importlib.import_module(f"{PKG}.modules.runtime_bridge.retrieval")
        app = app_mod.F2WApplication(settings=v1.settings, retrieval=retrieval_mod.LegacyRetrievalService(
            StubRetrievalAgent()))
        with public.F2WClient(app, owns_application=True, operations=v1.ops) as client:
            handle = client.get_job(client.context_for(), job["job_id"])
            summary["sdk_job"] = {"job_id": handle.job_id, "state": handle.state.value}
            out(f"F2WClient.get_job -> {handle.job_id} {handle.state.value} (same object as GET /v1/jobs/{{id}})")
        missing = c.get("/v1/jobs/job-missing")
        summary["not_found"] = missing.json()
        out(f"GET /v1/jobs/job-missing -> {missing.status_code} {missing.json()}")
    finally:
        mp.undo()
    return summary


# -------------------------------------------------------------------------- entrypoint


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run path (a), print the curl lines of path (b) and, on request, run (b) with the TestClient."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--testclient", action="store_true", help="run the HTTP path with Starlette's TestClient")
    parser.add_argument("--json", action="store_true", help="print a JSON summary instead of prose")
    parser.add_argument("--workdir", type=Path, default=None, help="scratch directory (default: a temp dir)")
    args = parser.parse_args(argv)
    import tempfile

    out = (lambda *a, **k: None) if args.json else print
    with tempfile.TemporaryDirectory() as tmp:
        root = args.workdir or Path(tmp)
        out("== (a) in-process: f2w.public ==")
        summary: Dict[str, Any] = {"sdk": sdk_path(root / "sdk", out=out)}
        out("\n== (b) HTTP: /v1 behind /fair2wise/api ==")
        out(CURL_LINES)
        if args.testclient:
            repo = Path(__file__).resolve().parents[1]
            out("")
            summary["http"] = http_path_testclient(repo, root / "http", out=out)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
