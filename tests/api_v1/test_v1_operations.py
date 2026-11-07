"""T19E: the five T19C operations over ``/v1/operations``, the SDK and the CLI (local and ALS).

Every surface calls the one ``F2WOperations`` -> ``KnowledgeOperations`` path, so the same
call gives the same bounded typed result (the agent Tool's) and the same refusal. Partial
stays partial through every surface; the ``knowledge_operation`` job reads the whole
population; no source material (descriptions, snippets, paths) leaves any surface.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict

import pytest

from _v1_support import REPO, build_als, build_local, ingest_inputs, local_settings
from _wiring_support import FORGED_HEADERS

from app import public
from app.api import cli
from app.api.operations import build_operations
from app.modules.application import F2WApplication
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.graph.backends.json import JsonGraphBackend
from app.modules.operations.corpus import TOOL_AUDIT, CorpusAuditTool, mapping_corpus_resolver
from app.modules.operations.graph import TOOL_COMPARE, TOOL_STATS, TOOL_SUBGRAPH, GraphOperationsTool
from app.modules.operations.knowledge import OPERATION_JOB_KIND, KnowledgeOperations, knowledge_job_context
from app.modules.runtime_bridge.retrieval import LegacyRetrievalService
from app.modules.security.authorization import resolve_context
from tests.read_path.test_read_tool import SUCCESS, StubAgent

sys.path.insert(0, str(REPO / "tests" / "graph"))
from graph_samples import things_payload, write  # noqa: E402

CANARY = "SYNTHETIC-DESCRIPTION-CANARY-5521"
SNIPPET = "SYNTHETIC-SNIPPET-CANARY-1187"
GRAPH, PREV, CORPUS_NAME = "rsoxs", "rsoxs_prev", "rsoxs"  # inside the ALS fixture scope {"rsoxs"}


def _payload() -> Dict[str, Any]:
    data = things_payload()
    data["things"][0]["description"] = CANARY
    data["things"].append({"id": "matkg:Film", "name": "Film", "category": "Material",
                           "source_papers": ["/abs/dir/paper_a.pdf", "10.1021_x.1.pdf"],
                           "context_snippets": [{"text": SNIPPET}]})
    data["associations"].append({"subject": "matkg:Film", "predicate": "rel:PART_OF", "object": "matkg:P3HT"})
    return data


def _terms() -> Dict[str, Any]:
    def term(name, papers):
        return {"term": name, "category": "Material", "definition": f"{CANARY} {name}", "pages": [1],
                "source_papers": papers,
                "context_snippets": [{"text": SNIPPET, "source_paper": f"/abs/dir/{p}", "page": 1}
                                     for p in papers],
                "relations": []}

    return {"terms": [term("P3HT", ["paper_a.pdf"]), term("PCBM", ["paper_a.pdf", "paper_b.pdf"])],
            "metadata": {"paper_a.pdf": {"title": "Paper A", "year": 2021}}}


@pytest.fixture
def files(tmp_path):
    root = tmp_path / "data"
    return {GRAPH: write(root / "storage" / "kg" / "matkg_rsoxs_v2.json", _payload()),
            PREV: write(root / "storage" / "kg" / "matkg_rsoxs_prev.json", things_payload()),
            "terms": write(root / "storage" / "terminology" / f"{CORPUS_NAME}.json", _terms()), "root": root}


def _knowledge(files, **kw) -> KnowledgeOperations:
    graph = GraphOperationsTool(JsonGraphBackend({GRAPH: files[GRAPH], PREV: files[PREV]}), **kw)
    return KnowledgeOperations(graph, CorpusAuditTool(mapping_corpus_resolver({CORPUS_NAME: str(files["terms"])})))


@pytest.fixture
def local(monkeypatch, tmp_path, files):
    return build_local(monkeypatch, tmp_path, knowledge_operations=_knowledge(files))


@pytest.fixture
def als(monkeypatch, tmp_path, files):
    return build_als(monkeypatch, tmp_path, knowledge_operations=_knowledge(files))


def _comparable(result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result)
    out.pop("provenance_id", None)
    data = dict(out.get("data") or {})
    for key in ("started_at", "finished_at"):
        data.pop(key, None)
    out["data"] = data
    return out


def _direct(v1, operation, args):
    return _comparable(v1.ops.knowledge.result(v1.ops.cli_context(), operation, args).model_dump(mode="json"))


def _sdk(v1):
    app = F2WApplication(settings=v1.settings, retrieval=LegacyRetrievalService(StubAgent(SUCCESS)))
    return public.F2WClient(app, owns_application=True, operations=v1.ops)


def _ctx(als, who):
    cred = als.store.get(f"cookie-{who}")
    return resolve_context(als.settings, cred, request_id="sdk", identity_port=als.port, clock=als.ops.clock)


# ------------------------------------------------------------------ routes (local)


def test_route_table_declares_the_operations_routes(local):
    from app.api.policies import API_ROUTE_POLICIES

    mine = {p.path: p for p in API_ROUTE_POLICIES if p.path.startswith("/v1/operations")}
    assert set(mine) == {"/v1/operations/statistics", "/v1/operations/compare", "/v1/operations/paper-subgraph",
                         "/v1/operations/recent", "/v1/operations/corpus-audit", "/v1/operations/jobs",
                         "/v1/operations/jobs/{job_id}"}
    reads = [p for path, p in mine.items() if p.method == "GET" and "{job_id}" not in path]
    assert all(p.capabilities == frozenset({"chat.ask"}) for p in reads) and len(reads) == 5
    assert mine["/v1/operations/jobs"].capabilities == frozenset({"ops.run"})
    assert mine["/v1/operations/jobs/{job_id}"].capabilities == frozenset()  # object policy in JobService
    assert local.ops.jobs.has_kind(OPERATION_JOB_KIND)  # the factory registered the job kind


@pytest.mark.parametrize("path, params, operation, args", [
    ("statistics", {"graph_id": GRAPH}, TOOL_STATS, {"graph_id": GRAPH}),
    ("compare", {"graph_ids": [PREV, GRAPH]}, TOOL_COMPARE, {"graph_ids": [PREV, GRAPH]}),
    ("paper-subgraph", {"graph_id": GRAPH, "paper": "paper_a.pdf", "max_nodes": 5}, TOOL_SUBGRAPH,
     {"graph_id": GRAPH, "paper": "paper_a.pdf", "max_nodes": 5}),
    ("corpus-audit", {"corpus": CORPUS_NAME, "sections": ["summary", "stubs"]}, TOOL_AUDIT,
     {"corpus": CORPUS_NAME, "sections": ["summary", "stubs"]}),
])
def test_reads_return_the_agent_tools_bounded_result(local, path, params, operation, args):
    resp = local.client().get(f"/v1/operations/{path}", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tool"] == operation and body["status"] == "ok"
    assert body["data"]["coverage"]["completeness"] == "complete" and body["data"]["model_calls"] == 0
    assert _comparable(body) == _direct(local, operation, args)
    assert local.port.calls == 0


def test_statistics_counts_match_the_t19c_units(local):
    body = local.client().get("/v1/operations/statistics", params={"graph_id": GRAPH}).json()
    m = body["data"]["metrics"]
    assert (m["entities"], m["relations"], m["distinct_works"]) == (5, 4, 3)
    assert body["data"]["counting_units"]["distinct_works"].startswith("distinct Works")


def test_recent_on_a_json_snapshot_unknown_graph_and_bad_arguments(local):
    c = local.client()
    recent = c.get("/v1/operations/recent", params={"graph_id": GRAPH})
    assert recent.status_code == 501 and recent.json()["code"] == "unsupported_capability"
    assert c.get("/v1/operations/statistics", params={"graph_id": "nope"}).status_code == 404
    assert c.get("/v1/operations/statistics").status_code == 422
    assert c.get("/v1/operations/compare", params={"graph_ids": [GRAPH]}).status_code == 422
    assert c.get("/v1/operations/paper-subgraph", params={"graph_id": GRAPH, "paper": "p", "max_nodes": 0}
                 ).status_code == 422
    assert c.get("/v1/operations/corpus-audit", params={"corpus": "nope"}).status_code == 404
    bad = c.get("/v1/operations/corpus-audit", params={"corpus": CORPUS_NAME, "sections": ["nope"]})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_input"


def test_operation_job_reads_the_whole_population(local):
    c = local.client()
    resp = c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}})
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["kind"] == OPERATION_JOB_KIND and job["state"] == "queued"
    again = c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}}).json()
    assert again["job_id"] == job["job_id"]  # idempotent for identical inputs
    pending = c.get(f"/v1/operations/jobs/{job['job_id']}").json()
    assert pending["result"] is None and pending["job"]["state"] == "queued"
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    done = c.get(f"/v1/operations/jobs/{job['job_id']}").json()
    assert done["job"]["state"] == "succeeded" and done["result"]["coverage"]["completeness"] == "complete"
    inline = c.get("/v1/operations/statistics", params={"graph_id": GRAPH}).json()["data"]
    assert done["result"]["metrics"] == inline["metrics"]
    progress = c.get(f"/v1/jobs/{job['job_id']}/progress").json()
    assert progress["phases"]["other"]["stages"] == {"compute": "completed"}
    assert progress["phases"]["other"]["progress"]["compute.complete"] == 1
    assert c.get(f"/v1/jobs/{job['job_id']}/view").json()["ui_state"] == "completed"
    record = local.ops.jobs.store.get(job["job_id"])
    assert record.inputs.corpus_scope == (GRAPH,)  # scope derived from the arguments


def test_operation_job_refusals(local):
    c = local.client()
    assert c.post("/v1/operations/jobs", json={"operation": "run_python", "args": {}}).status_code == 422
    assert c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {}}).status_code == 422
    assert c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH},
                                               "mode": "local_trusted"}).status_code == 422
    assert local.ops.jobs.store.list_jobs() == []  # nothing was registered
    ingest = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
    other = c.get(f"/v1/operations/jobs/{ingest['job_id']}")
    assert other.status_code == 422 and other.json()["code"] == "invalid_input"  # not a knowledge job
    assert c.get("/v1/operations/jobs/job-missing").status_code == 404


def test_partial_inline_result_versus_complete_job(monkeypatch, tmp_path, files):
    capped = build_local(monkeypatch, tmp_path, knowledge_operations=_knowledge(files, max_records=4))
    c = capped.client()
    inline = c.get("/v1/operations/statistics", params={"graph_id": GRAPH}).json()
    assert inline["status"] == "partial" and inline["data"]["coverage"]["completeness"] == "partial"
    assert inline["data"]["coverage"]["records_scanned"] == 4 and inline["warnings"][0].startswith("partial:")
    job = c.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}}).json()
    assert capped.work() == (job["job_id"], JobState.SUCCEEDED)
    result = c.get(f"/v1/operations/jobs/{job['job_id']}").json()["result"]
    assert result["coverage"]["completeness"] == "complete"  # the job has no inline bound
    assert result["metrics"]["entities"] == 5 > inline["data"]["metrics"]["entities"]
    with _sdk(capped) as client:
        sdk = client.graph_statistics(capped.ops.cli_context(), GRAPH)
        assert sdk.status.value == "partial" and sdk.data["coverage"]["completeness"] == "partial"


def test_no_source_material_leaves_the_routes(local, tmp_path):
    c = local.client()
    bodies = [
        c.get("/v1/operations/statistics", params={"graph_id": GRAPH}).text,
        c.get("/v1/operations/compare", params={"graph_ids": [PREV, GRAPH]}).text,
        c.get("/v1/operations/paper-subgraph", params={"graph_id": GRAPH, "paper": "paper_a.pdf"}).text,
        c.get("/v1/operations/corpus-audit", params={"corpus": CORPUS_NAME}).text,
        c.get("/v1/operations/statistics", params={"graph_id": "nope"}).text,
    ]
    job = c.post("/v1/operations/jobs", json={"operation": TOOL_AUDIT, "args": {"corpus": CORPUS_NAME}}).json()
    local.work()
    bodies.append(c.get(f"/v1/operations/jobs/{job['job_id']}").text)
    bodies.append(c.get(f"/v1/jobs/{job['job_id']}/events").text)
    for body in bodies:
        assert CANARY not in body and SNIPPET not in body and "/abs/" not in body and str(tmp_path) not in body
        assert "definition" not in body and "context_snippets" not in body


# ------------------------------------------------------------------ SDK


def test_sdk_methods_forward_to_the_same_operations(local):
    c = local.client()
    with _sdk(local) as client:
        ctx = local.ops.cli_context()
        pairs = [
            (client.graph_statistics(ctx, GRAPH), c.get("/v1/operations/statistics", params={"graph_id": GRAPH})),
            (client.compare_graphs(ctx, [PREV, GRAPH]),
             c.get("/v1/operations/compare", params={"graph_ids": [PREV, GRAPH]})),
            (client.paper_subgraph(ctx, GRAPH, "paper_a.pdf", max_edges=3),
             c.get("/v1/operations/paper-subgraph", params={"graph_id": GRAPH, "paper": "paper_a.pdf",
                                                            "max_edges": 3})),
            (client.audit_corpus(ctx, CORPUS_NAME, sections=["summary"]),
             c.get("/v1/operations/corpus-audit", params={"corpus": CORPUS_NAME, "sections": ["summary"]})),
            (client.run_knowledge_operation(ctx, TOOL_STATS, {"graph_id": PREV}),
             c.get("/v1/operations/statistics", params={"graph_id": PREV})),
        ]
        for sdk, http in pairs:
            assert http.status_code == 200, http.text
            assert _comparable(sdk.model_dump(mode="json")) == _comparable(http.json())
        with pytest.raises(ContractError) as err:
            client.recent_entities(ctx, GRAPH, limit=2)
        assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        handle = client.submit_knowledge_operation(ctx, TOOL_STATS, {"graph_id": GRAPH})
        via_http = c.post("/v1/operations/jobs",
                          json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}}).json()
        assert handle.job_id == via_http["job_id"]
        assert client.knowledge_job_result(ctx, handle.job_id)["result"] is None
        assert local.work() == (handle.job_id, JobState.SUCCEEDED)
        result = client.knowledge_job_result(ctx, handle.job_id)
        via_route = c.get(f"/v1/operations/jobs/{handle.job_id}").json()
        assert result["result"]["metrics"] == via_route["result"]["metrics"]


def test_sdk_without_operations_is_unsupported():
    app = F2WApplication(settings=local_settings(), retrieval=LegacyRetrievalService(StubAgent(SUCCESS)))
    with public.F2WClient(app, owns_application=True) as client:
        for call in (lambda: client.graph_statistics(client.context_for(), GRAPH),
                     lambda: client.submit_knowledge_operation(client.context_for(), TOOL_STATS,
                                                               {"graph_id": GRAPH})):
            with pytest.raises(ContractError) as err:
                call()
            assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_operations_without_knowledge_are_unsupported(tmp_path):
    ops = build_operations(local_settings(), tmp_path / "ops")
    with pytest.raises(ContractError) as err:
        ops.graph_statistics(ops.cli_context(), GRAPH)
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    ops.knowledge = _knowledge({GRAPH: tmp_path / "a.json", PREV: tmp_path / "b.json",
                                "terms": tmp_path / "t.json"})
    with pytest.raises(ContractError) as err:  # the job kind is registered by trusted wiring only
        ops.submit_knowledge_operation(ops.cli_context(), TOOL_STATS, {"graph_id": GRAPH})
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


# ------------------------------------------------------------------ CLI


def _cli(capsys, *argv, root, project_root, graph_files=()):
    args = ["--root", root, "--project-root", project_root]
    for f in graph_files:
        args += ["--graph-file", str(f)]
    code = cli.main([*args, "ops", *argv], environ={})
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


def test_cli_ops_subcommands_print_the_same_bounded_results(local, files, tmp_path, capsys):
    root, project = str(tmp_path / "cli-ops"), str(files["root"])
    code, stats = _cli(capsys, "statistics", "--graph", "rsoxs_v2", root=root, project_root=project)
    assert code == 0 and stats["status"] == "ok"
    assert stats["data"]["metrics"] == _direct(local, TOOL_STATS, {"graph_id": GRAPH})["data"]["metrics"]
    code, cmp_ = _cli(capsys, "compare", "--graph", "rsoxs_prev", "--graph", "rsoxs_v2", root=root,
                      project_root=project)
    assert code == 0 and cmp_["data"]["scope"]["graph_ids"] == ["rsoxs_prev", "rsoxs_v2"]
    code, sub = _cli(capsys, "paper-subgraph", "--graph", "rsoxs_v2", "--paper", "paper_a.pdf", "--max-nodes", "3",
                     root=root, project_root=project)
    assert code == 0 and sub["tool"] == TOOL_SUBGRAPH and sub["status"] == "ok"
    code, audit = _cli(capsys, "corpus-audit", "--corpus", CORPUS_NAME, "--section", "summary", root=root,
                       project_root=project)
    assert code == 0 and audit["data"]["metrics"]["summary"]["distinct_works"] == 2
    code, recent = _cli(capsys, "recent", "--graph", "rsoxs_v2", root=root, project_root=project)
    assert code == 2 and recent["code"] == "unsupported_capability"
    code, missing = _cli(capsys, "statistics", "--graph", "nope", root=root, project_root=project)
    assert code == 2 and missing["code"] == "not_found"
    # explicit graph files override storage/kg (graph ids derive from the file names)
    code, explicit = _cli(capsys, "statistics", "--graph", "rsoxs_v2", root=root,
                          project_root=str(tmp_path / "empty"), graph_files=[files[GRAPH]])
    assert code == 0 and explicit["data"]["metrics"] == stats["data"]["metrics"]
    for body in (stats, cmp_, sub, audit):
        text = json.dumps(body)
        assert CANARY not in text and SNIPPET not in text and "/abs/" not in text and str(tmp_path) not in text


def test_cli_ops_list_and_sdk_catalog_name_the_same_operations_graphs_and_corpora(local, files, tmp_path, capsys):
    """T19E closure: the catalog (``ops list`` / ``F2WClient.knowledge_catalog``) is names only."""
    root, project = str(tmp_path / "cli-list"), str(files["root"])
    code, listing = _cli(capsys, "list", root=root, project_root=project)
    assert code == 0
    assert [op["name"] for op in listing["operations"]] == [TOOL_STATS, TOOL_COMPARE, TOOL_SUBGRAPH,
                                                            "kg_recent_entities", TOOL_AUDIT]
    assert all(set(op) == {"name", "version", "description"} for op in listing["operations"])
    assert listing["graphs"] == ["rsoxs_prev", "rsoxs_v2"] and listing["corpora"] == [CORPUS_NAME]
    assert str(files["root"]) not in json.dumps(listing) and "storage" not in json.dumps(listing["graphs"])
    with _sdk(local) as client:
        sdk = client.knowledge_catalog(local.ops.cli_context())
    # the server fixture binds a mapping resolver (no lister) and an explicit graph mapping
    assert [op["name"] for op in sdk["operations"]] == [op["name"] for op in listing["operations"]]
    assert sorted(sdk["graphs"]) == sorted([GRAPH, PREV]) and sdk["corpora"] == []
    # explicit --graph-file: ids derive from the file names; the corpora still come from the project root
    code, explicit = _cli(capsys, "list", root=root, project_root=project, graph_files=[files[GRAPH]])
    assert code == 0 and explicit["graphs"] == ["rsoxs_v2"] and explicit["corpora"] == []


def test_catalog_filters_to_the_scope_and_needs_chat_ask(als):
    user = _ctx(als, "user")  # scope {"rsoxs"}
    listing = als.ops.knowledge_catalog(user)
    assert listing["graphs"] == [GRAPH] and listing["corpora"] == []
    with pytest.raises(ContractError) as err:
        als.ops.knowledge_catalog(user.model_copy(update={"capabilities": frozenset({"chat.read_support"})}))
    assert err.value.code is ErrorCode.DENIED


def test_cli_summarize_is_the_t19b_operation_with_the_sdk_result(local, tmp_path, capsys):
    """OP01 on the CLI: same typed result as ``F2WClient.summarize_paper_terms``; schema-checked; no paths out."""
    from tests.operations.summary_fakes import CASES, write_case

    entry, terms = CASES["capped"]
    paths = write_case(tmp_path / "case", entry, terms)
    root = str(tmp_path / "cli-sum")
    argv = ["--terms", paths["terms_path"], "--manifest", paths["manifest_path"], "--paper", paths["filename"]]
    code, out = _cli(capsys, "summarize", *argv, "--max-terms", "5", root=root, project_root=str(tmp_path / "none"))
    assert code == 0 and out["status"] == "ok" and out["tool"] == "lit_summarize_extracted_terms"
    assert out["data"]["term_count"] == 23 and out["data"]["displayed_term_count"] == 5
    assert str(tmp_path) not in json.dumps(out)
    code, refused = _cli(capsys, "summarize", *argv, "--max-terms", "99", root=root, project_root=str(tmp_path / "none"))
    assert code == 2 and refused["code"] == "invalid_input"
    code, missing = _cli(capsys, "summarize", "--terms", paths["terms_path"], "--manifest", paths["manifest_path"],
                         "--paper", "other.pdf", root=root, project_root=str(tmp_path / "none"))
    assert code == 0 and missing["status"] == "error" and missing["error_code"] == "not_found"
    assert str(tmp_path) not in json.dumps(missing)
    with pytest.raises(TypeError):
        local.ops.summarize_paper_terms(local.ops.cli_context(), paths["terms_path"])


def test_cli_submits_and_reads_back_an_operation_job(files, tmp_path, capsys):
    root, project = str(tmp_path / "cli-jobs"), str(files["root"])
    code, handle = _cli(capsys, "submit", "--operation", TOOL_STATS, "--args",
                        json.dumps({"graph_id": "rsoxs_v2"}), root=root, project_root=project)
    assert code == 0 and handle["kind"] == OPERATION_JOB_KIND and handle["state"] == "queued"
    code, pending = _cli(capsys, "result", handle["job_id"], root=root, project_root=project)
    assert code == 0 and pending["result"] is None
    worker = build_operations(local_settings(), root, owner="worker",
                              knowledge=KnowledgeOperations.from_storage(project))
    worker.register_knowledge_jobs(knowledge_job_context(local_settings()))
    assert worker.jobs.work_one("worker") == (handle["job_id"], JobState.SUCCEEDED)
    code, done = _cli(capsys, "result", handle["job_id"], root=root, project_root=project)
    assert code == 0 and done["result"]["coverage"]["completeness"] == "complete"
    assert done["result"]["metrics"]["entities"] == 5
    code, bad = _cli(capsys, "submit", "--operation", TOOL_STATS, "--args", "[1]", root=root, project_root=project)
    assert code == 2 and bad["code"] == "invalid_input"


# ------------------------------------------------------------------ ALS


def test_als_signed_in_users_read_within_scope_and_cannot_submit_jobs(als):
    anon = als.client("anon")
    assert anon.get("/v1/operations/statistics", params={"graph_id": GRAPH}).status_code == 401
    for headers in (None, FORGED_HEADERS):
        user = als.client("user", headers=headers)
        ok = user.get("/v1/operations/statistics", params={"graph_id": GRAPH})
        assert ok.status_code == 200 and ok.json()["status"] == "ok", ok.text
        assert user.get("/v1/operations/corpus-audit", params={"corpus": CORPUS_NAME}).status_code == 200
        outside = user.get("/v1/operations/statistics", params={"graph_id": PREV})
        assert outside.status_code == 403 and outside.json()["code"] == "denied"  # T19C scope rule
        assert user.get("/v1/operations/compare", params={"graph_ids": [PREV, GRAPH]}).status_code == 403
        job = user.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}})
        assert job.status_code == 403 and job.json()["code"] == "denied"
    assert als.ops.jobs.store.list_jobs() == []
    with _sdk(als) as client:  # the SDK applies the same policy
        with pytest.raises(ContractError) as err:
            client.graph_statistics(_ctx(als, "user"), PREV)
        assert err.value.code is ErrorCode.DENIED
        with pytest.raises(ContractError) as err:
            client.submit_knowledge_operation(_ctx(als, "user"), TOOL_STATS, {"graph_id": GRAPH})
        assert err.value.code is ErrorCode.DENIED
        assert client.graph_statistics(_ctx(als, "user"), GRAPH).status.value == "ok"


def test_als_admin_runs_an_operation_job_the_worker_reauthorizes(als):
    admin = als.client("admin")
    resp = admin.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": GRAPH}})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert als.work() == (job_id, JobState.SUCCEEDED)
    done = admin.get(f"/v1/operations/jobs/{job_id}").json()
    assert done["result"]["coverage"]["completeness"] == "complete" and done["result"]["metrics"]["entities"] == 5
    for who in ("user", "user2"):
        assert als.client(who).get(f"/v1/operations/jobs/{job_id}").status_code == 404  # invisible to others
    outside = admin.post("/v1/operations/jobs", json={"operation": TOOL_STATS, "args": {"graph_id": PREV}})
    assert outside.status_code == 403 and outside.json()["code"] == "denied"  # job scope outside the deployment's
