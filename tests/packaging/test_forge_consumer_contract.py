"""T15 consumer contract: what FORGE can rely on from an installed F2W (owner decision B30, 2026-10-30).

Runs against offline fakes only (``examples/forge_consumer.py`` builds them): no network, no
model key, no Tiled, no ``.env``. The HTTP side uses the T14 test wiring (``tests/api_v1/_v1_support``)
over the real ``create_api_app``. Covered, as plan T15 asks: import from outside the checkout as
``f2w.public``; two clients share no state; stream termination with a single terminal event;
cancellation; error envelopes (denied / unavailable / partial / invalid / not_found never hidden);
scope and citation ids stable across the SDK and HTTP paths; the evidence bundle round-trips
through JSON; ``publish_plan`` applies an existing approval through the publication job.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO / "examples", REPO / "tests" / "api_v1", REPO / "tests" / "graph"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import forge_consumer as consumer  # noqa: E402  (the runnable example doubles as the fixture builder)
from _v1_support import build_als, build_local, ingest_inputs  # noqa: E402
from graph_samples import things_payload, write  # noqa: E402

from app import public  # noqa: E402
from app.modules.application import F2WApplication  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.contracts.jobs import JobState  # noqa: E402
from app.modules.contracts.tools import ToolStatus  # noqa: E402
from app.modules.evidence.bundle import LiteratureEvidenceBundle  # noqa: E402
from app.modules.graph.backends.json import JsonGraphBackend  # noqa: E402
from app.modules.operations.graph import GraphOperationsTool  # noqa: E402
from app.modules.operations.knowledge import KnowledgeOperations  # noqa: E402
from app.modules.security.authorization import create_approval, resolve_context  # noqa: E402

GRAPH = "rsoxs"


@pytest.fixture
def fx(tmp_path):
    return consumer.build_fixtures(tmp_path / "fx")


def _client(fx, **kw):
    return public.F2WClient.from_settings(fx.settings, retrieval=fx.retrieval, literature=fx.literature,
                                          evidence=fx.evidence, **kw)


def _knowledge(tmp_path):
    path = write(tmp_path / "storage" / "kg" / "matkg_rsoxs_v2.json", things_payload())
    return KnowledgeOperations(GraphOperationsTool(JsonGraphBackend({GRAPH: path})))


@pytest.fixture
def local(monkeypatch, tmp_path):
    return build_local(monkeypatch, tmp_path / "v1", knowledge_operations=_knowledge(tmp_path))


def _sdk_over(v1, fx):
    """A client over the same ``F2WOperations`` the ``/v1`` routes call (one service, two surfaces)."""
    app = F2WApplication(settings=v1.settings, retrieval=fx.retrieval, literature=fx.literature,
                         evidence=fx.evidence)
    return public.F2WClient(app, owns_application=True, operations=v1.ops)


def _comparable(body):
    out = dict(body)
    out.pop("provenance_id", None)
    data = dict(out.get("data") or {})
    for key in ("started_at", "finished_at"):
        data.pop(key, None)
    out["data"] = data
    return out


# ------------------------------------------------------------------ 1. import as f2w.public from outside


def test_consumer_paths_run_under_the_installed_name_outside_the_checkout(tmp_path):
    """Load the package as ``f2w`` from ``cwd=/`` (as an install would) and run the whole example path (a)."""
    code = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('f2w', {str(REPO / 'app' / '__init__.py')!r},\n"
        f"    submodule_search_locations=[{str(REPO / 'app')!r}])\n"
        "mod = importlib.util.module_from_spec(spec); sys.modules['f2w'] = mod; spec.loader.exec_module(mod)\n"
        "import f2w.public\n"
        "heavy = {'torch', 'faiss', 'fitz', 'openai', 'langgraph', 'langchain_core', 'tiled', 'dotenv',\n"
        "         'fastapi'}\n"
        "light = sorted(heavy & {m.split('.')[0] for m in sys.modules})\n"
        f"sys.path.insert(0, {str(REPO / 'examples')!r})\n"
        "import forge_consumer\n"
        "from pathlib import Path\n"
        f"summary = forge_consumer.sdk_path(Path({str(tmp_path / 'run')!r}), out=lambda *a, **k: None)\n"
        "summary['heavy_after_public_import'] = light\n"
        "summary['app_loaded'] = 'app' in sys.modules\n"
        "print(json.dumps(summary, default=str))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd="/",
                         env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr[-3000:]
    summary = json.loads(out.stdout.strip().splitlines()[-1])
    assert summary["package"] == "f2w" and summary["app_loaded"] is False
    assert summary["heavy_after_public_import"] == []
    assert summary["search"]["state"] == "ok" and summary["resolve"]["state"] == "ok"
    assert summary["read_paper"]["state"] == "ok" and summary["read_section"]["state"] == "ok"
    assert summary["get_evidence"]["state"] == "partial" and summary["unavailable"]["state"] == "unavailable"
    assert summary["denied"] == {**summary["denied"], "state": "denied", "error_code": "denied", "ok": False}
    assert summary["delegated"]["answered"] is True and summary["delegated"]["tools"] == ["lit_search_papers"]
    assert LiteratureEvidenceBundle.from_dict(summary["bundle"]).work_id == consumer.WORK_ID


def test_example_main_runs_without_network_or_keys(tmp_path, capsys):
    assert consumer.main(["--json", "--workdir", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sdk"]["package"] == "app" and summary["sdk"]["answer"].startswith("RSoXS")


# ------------------------------------------------------------------ 2. isolation and lifecycle


def test_two_clients_share_no_registry_executor_ledger_or_toolset_state(tmp_path):
    a = _client(consumer.build_fixtures(tmp_path / "a"))
    b = _client(consumer.build_fixtures(tmp_path / "b"))
    assert a.application is not b.application
    assert a.application.registry is not b.application.registry
    assert a.application.executor is not b.application.executor
    assert a.application.ledger is not b.application.ledger
    assert a.application.event_sink is not b.application.event_sink
    assert a.application.literature is not b.application.literature
    assert a.application.evidence is not b.application.evidence
    ctx = a.context_for()
    assert a.search_literature(ctx, "x").status is ToolStatus.OK
    # Reads are not audited; neither sink saw the other's call.
    assert len(a.application.event_sink.events) == len(b.application.event_sink.events) == 0
    a.close(), b.close()


def test_explicit_lifecycle_closes_only_what_the_client_owns(fx):
    model = consumer.scripted_supervisor_model("lit_search_papers", {"query": "q"})
    with public.create_literature_agent(settings=fx.settings, retrieval=fx.retrieval, chat_model=model,
                                        literature=fx.literature) as agent:
        app = agent.client.application
        assert app.checkpointer is not None and app._owned_checkpointer is not None
    assert app._owned_checkpointer is None  # the factory-built client closed its own checkpointer
    wrapped = public.F2WClient(F2WApplication(settings=fx.settings, retrieval=fx.retrieval))
    wrapped.close()  # not owned: nothing to close, nothing raised


# ------------------------------------------------------------------ 3./4. stream termination and cancellation


def test_stream_ends_with_exactly_one_terminal_event_and_cancel_is_terminal(local, fx):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    events = consumer.parse_sse(c.get(f"/v1/jobs/{job['job_id']}/stream").text)
    kinds = [e[0] for e in events]
    assert kinds.count("end") == 1 and kinds[-1] == "end" and set(kinds[:-1]) == {"job_event"}
    assert events[-1][2] == {"state": "succeeded", "reason": "terminal"}
    # Cancellation through the SDK, observed over HTTP: one terminal end event with state cancelled.
    with _sdk_over(local, fx) as client:
        ctx = client.context_for()
        handle = client.submit_ingest(ctx, ingest_inputs(wait_for_review=True))
        assert client.cancel_job(ctx, handle.job_id).state is JobState.CANCELLED
    assert local.work() is None  # nothing runs after a cancel
    after = consumer.parse_sse(c.get(f"/v1/jobs/{handle.job_id}/stream").text)
    assert [e[0] for e in after].count("end") == 1 and after[-1][2] == {"state": "cancelled", "reason": "terminal"}
    assert c.get(f"/v1/jobs/{handle.job_id}").json()["state"] == "cancelled"
    # A waiting job ends the stream with reason 'waiting', never 'terminal' (distinguishable states).
    waiting = c.post("/v1/jobs", json={"kind": "ingest",
                                       "inputs": ingest_inputs(wait_for_review=True, n=2)}).json()
    local.work()
    end = consumer.parse_sse(c.get(f"/v1/jobs/{waiting['job_id']}/stream").text)[-1][2]
    assert end == {"state": "awaiting_review", "reason": "waiting"}


# ------------------------------------------------------------------ 5. error envelopes


def test_sdk_states_are_distinct_and_the_forge_envelope_keeps_them(fx, tmp_path):
    with _client(fx) as client:
        ctx = client.context_for()
        down = client.search_literature(ctx, "provider down")
        assert down.status is ToolStatus.UNAVAILABLE and down.error_code is ErrorCode.UNAVAILABLE
        assert down.retryable
        empty = client.search_literature(ctx, "nothing here")
        assert empty.status is ToolStatus.EMPTY and empty.data is None and empty.error_code is None
        invalid = client.search_literature(ctx, "x", limit=99)
        assert invalid.status is ToolStatus.INVALID and invalid.error_code is ErrorCode.INVALID_INPUT
        gone = client.resolve_work(ctx, "10.1/gone")
        assert gone.status is ToolStatus.EMPTY  # the provider knows no such work: a fact, not a fault
        bad = client.resolve_work(ctx, "W2741809807")
        assert bad.status is ToolStatus.INVALID and "search_literature" in bad.warnings[0]
        arxiv = client.resolve_work(ctx, "arXiv:2101.00001")
        assert arxiv.status is ToolStatus.EMPTY  # resolved through 10.48550/arXiv.<id>; the fake has none
        missing = client.read_section(ctx, consumer.DOI, "methods")
        assert missing.status is ToolStatus.ERROR and missing.error_code is ErrorCode.NOT_FOUND
        assert missing.data["available_sections"] == ["Abstract", "Results", "Discussion"]
        known = client.evidence_bundle(ctx, consumer.WORK_ID).evidence_ids
        partial = client.get_evidence(ctx, ["evidence:nope", *known])
        assert partial.status is ToolStatus.PARTIAL and partial.data["missing"] == ["evidence:nope"]
        assert client.get_evidence(ctx, ["evidence:nope"]).status is ToolStatus.EMPTY
        cases = ((down, "unavailable"), (invalid, "invalid"), (partial, "partial"), (missing, "error"))
        for result, state in cases:
            env = consumer.translate_envelope(result)
            expected_code = result.error_code.value if result.error_code else None
            assert env["state"] == state and env["error_code"] == expected_code
        assert consumer.translate_envelope(partial)["ok"] is True
        assert consumer.translate_envelope(down)["ok"] is False
    denied_fx = consumer.build_fixtures(tmp_path / "noscope", with_scope=False)
    with _client(denied_fx) as client:
        denied = client.read_paper(client.context_for(), consumer.DOI)
        assert denied.status is ToolStatus.DENIED and denied.error_code is ErrorCode.DENIED
        assert denied.warnings == ("source access denied",) and denied.data is None


def test_capability_refusals_match_over_sdk_and_http(monkeypatch, tmp_path, fx):
    als = build_als(monkeypatch, tmp_path / "als")
    user = resolve_context(als.settings, als.store.get("cookie-user"), request_id="t15", identity_port=als.port,
                           clock=als.ops.clock)
    http = als.client("user").post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert http.status_code == 403 and http.json()["code"] == "denied" and http.json()["retryable"] is False
    with _sdk_over(als, fx) as client:
        with pytest.raises(ContractError) as err:
            client.submit_ingest(user, ingest_inputs())
        assert err.value.code is ErrorCode.DENIED
        # Evidence reads need chat.ask; an ALS user has it. A context stripped of it is refused, not emptied.
        bundle = client.evidence_bundle(user, consumer.WORK_ID)
        assert not bundle.empty
        with pytest.raises(ContractError) as err:
            client.get_evidence(user.model_copy(update={"capabilities": frozenset()}), bundle.evidence_ids)
        assert err.value.code is ErrorCode.DENIED
    assert als.ops.jobs.store.list_jobs() == [] and als.tiled.writes == []
    missing = als.client("admin").get("/v1/jobs/job-missing").json()
    assert missing == {"code": "not_found", "message": missing["message"], "retryable": False}


# ------------------------------------------------------------------ 6. stable ids across SDK and HTTP


def test_scope_and_ids_are_identical_through_sdk_and_http(local, fx):
    c = local.client()
    with _sdk_over(local, fx) as client:
        ctx = client.context_for()
        sdk = client.paper_subgraph(ctx, GRAPH, "paper_a.pdf", max_nodes=5).model_dump(mode="json")
        http = c.get("/v1/operations/paper-subgraph", params={"graph_id": GRAPH, "paper": "paper_a.pdf",
                                                              "max_nodes": 5}).json()
        assert _comparable(sdk) == _comparable(http) and sdk["status"] == "ok"
        handle = client.submit_ingest(ctx, ingest_inputs())
        over_http = c.get(f"/v1/jobs/{handle.job_id}").json()
        assert over_http["job_id"] == handle.job_id and over_http["kind"] == handle.kind
        assert local.ops.jobs.store.get(handle.job_id).inputs.corpus_scope == ("rsoxs",)  # scope as submitted
        again = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
        assert again["job_id"] == handle.job_id  # idempotent across surfaces
        bundle = client.evidence_bundle(ctx, consumer.WORK_ID)
        refs = client.get_evidence(ctx, bundle.evidence_ids)
        assert refs.evidence_ids == bundle.evidence_ids == sorted(json.loads(bundle.to_json())["evidence"])
        assert all(q.evidence_ids and set(q.evidence_ids) <= set(bundle.evidence_ids) for q in bundle.quantities)
        read = client.answer_from_knowledge_sync(ctx, "What does RSoXS probe?")
        # Graph-scoped evidence id (never a URI smash) and the citation token in the legacy answer.
        assert read.evidence_ids == read.result.evidence_ids == ["kg:demo:RSoXS"]
        assert read.result.evidence[0].work_id == "kg-node:demo:RSoXS"
        assert "[KG: demo: RSoXS]" in read.to_legacy()["answer"]


# ------------------------------------------------------------------ 7. evidence bundle


def test_evidence_bundle_round_trips_through_json_and_states_its_caveats(fx):
    with _client(fx) as client:
        ctx = client.context_for()
        bundle = client.evidence_bundle(ctx, consumer.WORK_ID)
        text = bundle.to_json()
        back = LiteratureEvidenceBundle.from_json(text)
        assert back == bundle and json.loads(text) == bundle.to_dict() == back.to_dict()
        assert bundle.work_id == consumer.WORK_ID and bundle.status == "candidate" and bundle.bundle_version == 1
        assert bundle.source_version == "sha256:" + "a" * 64 and bundle.source_sha256 == "a" * 64
        assert bundle.parser["parser"] == "demo" and bundle.parser["parser_version"] == "1.0"
        names = {q.name: q for q in bundle.quantities}
        assert set(names) == {"first_peak_q", "film_thickness", "domain_spacing"}
        q = names["first_peak_q"]
        assert (q.raw_value, q.raw_unit, q.value, q.unit) == (0.30, "Å⁻¹", 3.0, "nm^-1")
        assert q.dimension == "reciprocal"
        assert q.conversion_profile and q.evidence_source == "reported" and q.paper_measurement is True
        assert "uncertainty_not_stated" in q.caveats
        t = names["film_thickness"]
        assert t.uncertainty.value == 5.0 and t.uncertainty.kind == "standard_deviation"
        d = names["domain_spacing"]
        assert d.evidence_source == "cited" and d.paper_measurement is False
        for q in bundle.quantities:
            ev = bundle.evidence[q.evidence_ids[0]]
            assert ev.quote is None and ev.verified_quote_match and ev.match_type in ("exact", "normalized")
            assert ev.page in (3, 5) and ev.section in ("Results", "Discussion") and ev.char_span
        assert any("candidate" in c for c in bundle.caveats) and any("quotes omitted" in c for c in bundle.caveats)
        quoted = client.evidence_bundle(ctx, consumer.WORK_ID, include_quotes=True)
        assert all(e.quote for e in quoted.evidence.values())
        assert any("agent-private" in c for c in quoted.caveats)
        assert LiteratureEvidenceBundle.from_json(quoted.to_json()) == quoted
        unknown = client.evidence_bundle(ctx, "doi:10.1/unknown")
        assert unknown.empty and unknown.source_version is None and unknown.quantities == ()
        disclosed = client.disclose(client.read_section(ctx, consumer.DOI, "results"))
        assert disclosed["status"] == "ok" and len(disclosed["data"]["text"]) <= 600
        assert "store" not in json.dumps(client.disclose(client.read_paper(ctx, consumer.DOI)))


# ------------------------------------------------------------------ 8. publish_plan applies an existing approval


def test_publish_plan_hands_an_existing_approval_to_the_publication_job(local, fx):
    with _sdk_over(local, fx) as client:
        ctx = client.context_for()
        handle = client.plan_publication(ctx, "rsoxs/v1", local.target)
        assert local.work() == (handle.job_id, JobState.AWAITING_REVIEW)
        plan = client.operations.current_plan(ctx, handle.job_id)
        approval = create_approval(ctx, plan, local.settings, approval_id="appr-t15", clock=local.ops.clock,
                                   operation_id=handle.job_id)
        other = plan.model_copy(update={"plan_sha256": "0" * 64})
        with pytest.raises(ContractError) as err:
            client.publish_plan(ctx, other, approval)
        assert err.value.code is ErrorCode.STALE_APPROVAL
        with pytest.raises(ContractError) as err:
            client.publish_plan(ctx, plan, approval.model_copy(update={"operation_id": None}))
        assert err.value.code is ErrorCode.INVALID_INPUT
        assert local.tiled.writes == []  # nothing applied yet
        requeued = client.publish_plan(ctx, plan, approval)
        assert requeued.job_id == handle.job_id and requeued.state is JobState.QUEUED
        assert local.work() == (handle.job_id, JobState.SUCCEEDED)
        view = client.operations.job_view(ctx, handle.job_id)
        assert view["ui_state"] == "published" and view["publication"]["receipt_id"]
        assert len(local.tiled.entities) == 3


def test_publish_plan_without_operations_is_unsupported(fx):
    with _client(fx) as client:
        with pytest.raises(ContractError) as err:
            client.publish_plan(client.context_for(), object(), object())
        assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
