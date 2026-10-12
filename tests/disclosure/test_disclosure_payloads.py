"""T21B payload projections: SEC-04, SEC-05, SEC-01, FIG-04, DEP-10, TIL-05, REL-04, JSON-08 (ADR N2, N4-N6, N9, N10)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _disclosure_support as S  # noqa: E402
from app.modules.contracts.errors import ContractError, ErrorCode  # noqa: E402
from app.modules.security.disclosure import PathRoots, source_route_denied  # noqa: E402

PAGE = S.synthetic_page(2)
LOCAL_PATHS = ["/Users/alice/f2w/run/kg.json", "/private/tmp/xyz/run/uploads/a.json", "/home/f2w/storage/kg/x.json",
               "/var/folders/ab/T/tmp1/workflow_state.json", "C:\\Users\\bob\\run\\x.json", "~/papers/a.pdf"]
LONG_ABSTRACT = "Abstract sentence about polymer morphology. " * 200


def projector(**kw):
    roots = PathRoots.of(workdir="/Users/alice/f2w/run", repo="/Users/alice/f2w")
    return S.make_projector(roots=roots, **kw)


def leaks(obj) -> list:
    text = json.dumps(obj, ensure_ascii=False)
    found = [p for p in LOCAL_PATHS if p in text or p.replace("\\", "\\\\") in text]
    for marker in (S.SENTINEL, "/Users/", "/private/", "/var/folders", "/home/", "Traceback", "object has no attribute",
                   "Errno", "sk-live", "context_snippets", "page_text", "merged_context\": \"", LONG_ABSTRACT[:400]):
        if marker in text:
            found.append(marker)
    return found


# ------------------------------------------------------------------ N6: rounds / SSE complete


def raw_rounds():
    return [{
        "round": 1,
        "retrieval": {"status": "error", "sufficient": False, "answer": PAGE[:3000], "selected": ["matkg:A"],
                      "selected_hits": [{"id": "matkg:A", "label": "A", "score": 0.9, "context": PAGE}],
                      "hybrid_rag": {"literature_hits": 3, "chunks": [{"text": PAGE}], "status": "ok"},
                      "error": "FileNotFoundError: [Errno 2] No such file or directory: '/Users/alice/f2w/run/x'",
                      "missing_topics": ["t"]},
        "candidate_search": {"status": "success", "count": 1, "candidates": [
            {"id": "W1", "title": "T", "doi": "10.1/x", "abstract": LONG_ABSTRACT, "publication_year": 2020,
             "pdf_urls": ["http://localhost:8090/papers/x.pdf"], "score": 0.5, "repository": "openalex"}]},
        "download": {"status": "success", "count": 1, "downloaded": ["/Users/alice/f2w/run/pdfs/x.pdf"]},
        "pending_pdfs": ["/Users/alice/f2w/run/pdfs/x.pdf"],
        "extraction": {"status": "error", "error": "Traceback (most recent call last): ...",
                       "output_path": "/Users/alice/f2w/run/terms.json",
                       "pdf_results": [{"filename": "/Users/alice/f2w/run/pdfs/x.pdf", "selected_pages": [1]}]},
        "extra": {"merged_context": PAGE, "prompt": PAGE},
    }]


def chat_payload(**overrides):
    payload = {"status": "answered", "answer": "Answer [KG: rsoxs: A].", "sufficient": True, "node_ids": ["matkg:A"],
               "publications": [{"source_paper": "x.pdf", "abstract_text": LONG_ABSTRACT,
                                 "pdf_path": "/Users/alice/f2w/papers/x.pdf"}],
               "confidence": 0.8, "rounds": raw_rounds(),
               "graph": {"nodes": [], "edges": [], "source_path": "query:/Users/alice/f2w/run/kg.json"},
               "graph_source_requested": "splash", "graph_source_used": "splash",
               "workdir": "/Users/alice/f2w/run", "pending": None,
               "orchestration": {"action": "retrieve_kg", "reason": "fake"}, "turn_id": "t1"}
    payload.update(overrides)
    return payload


def test_rounds_become_a_projected_summary():
    proj, viewer, _ = projector()
    out = proj.project_chat_response(chat_payload(), viewer)
    assert list(out) == list(chat_payload())
    assert leaks(out) == []
    rnd = out["rounds"][0]
    assert rnd["retrieval"]["selected"] == ["matkg:A"] and rnd["retrieval"]["error_code"] == "internal"
    assert "answer" not in rnd["retrieval"] and "extra" not in rnd
    assert rnd["retrieval"]["hybrid_rag"] == {"literature_hits": 3, "status": "ok"}
    cand = rnd["candidate_search"]["candidates"][0]
    assert set(cand) == {"id", "title", "doi", "abstract", "publication_year", "score", "repository"}
    assert len(cand["abstract"]) <= 600 + 4
    assert rnd["download"]["downloaded"] == ["x.pdf"] and rnd["pending_pdfs"] == ["x.pdf"]
    assert rnd["extraction"]["pdf_results"] == [{"filename": "x.pdf", "selected_pages": [1]}]
    assert out["workdir"] == "f2w://workdir"
    assert out["graph"]["source_path"] == "query:f2w://workdir/kg.json"
    assert len(out["publications"][0]["abstract_text"]) <= 1600 + 4
    assert "pdf_path" not in out["publications"][0]


def test_sse_complete_and_error_events_are_projected():
    proj, viewer, _ = projector()
    complete = proj.project_sse_event("complete", chat_payload(), viewer)
    assert leaks(complete) == []
    err = proj.project_sse_event(
        "error", {"status": "stream_error", "message": "'list' object has no attribute 'get'", "turn_id": "t"},
        viewer)
    assert list(err) == ["status", "message", "turn_id"]
    assert "attribute" not in err["message"] and viewer.correlation_id in err["message"]
    retrieval_error = proj.project_sse_event(
        "error", chat_payload(status="retrieval_error",
                              answer="Retrieval failed safely: Connection refused to /Users/alice/f2w/run/idx"),
        viewer)
    assert retrieval_error["answer"].startswith("Retrieval failed safely (")
    assert leaks(retrieval_error) == []


def test_progress_events_drop_private_data_and_keep_graph_shape():
    proj, viewer, _ = projector()
    node = {"id": "n", "label": "N", "description": "d", "properties": {"context_snippets": [{"text": PAGE}]}}
    data = {"phase": "graph_update", "message": "Mapping 1 node(s)", "turn_id": "t", "node_ids": ["n"],
            "graph": {"nodes": [node], "edges": [{"source": "n", "target": "m", "predicate": "p",
                                                   "has_evidence": [PAGE]}]},
            "titles": ["/Users/alice/f2w/run/pdfs/x.pdf"], "merged_context": PAGE,
            "error": "KeyError: 'x'"}
    out = proj.project_progress(data, viewer)
    assert list(out)[:3] == ["phase", "message", "turn_id"]
    assert set(out["graph"]) == {"nodes", "edges"} and isinstance(out["node_ids"], list)
    assert out["graph"]["edges"] == [{"source": "n", "target": "m", "predicate": "p"}]
    assert out["titles"] == ["x.pdf"] and out["error_code"] == "internal"
    assert leaks(out) == []


# ------------------------------------------------------------------ N4: traces


def raw_trace():
    fixture = json.loads((S.FIXTURES / "http" / "traces_get.json").read_text(encoding="utf-8"))
    trace = fixture["response"]["json"]
    trace["env"]["settings"]["F2W_TRACE_DIR"] = "/Users/alice/f2w/runs/traces"
    trace["env"]["graphs"][0]["path"] = "/Users/alice/f2w/storage/kg/matkg_contract_v1.json"
    trace["retrieval"].update({
        "merged_context": "### KG\n[PDF: x.pdf p.2] " + PAGE, "raw_judge_output": '{"answer": "' + PAGE[:800] + '"}',
        "parsed_verdict": {"sufficient": True, "answer": PAGE[:2000], "missing_topics": []},
        "leeway_system_prompt": "SYSTEM " + PAGE[:500],
        "selected_hits": [{"id": "matkg:A", "label": "A", "score": 1.0, "graph_id": "kg", "snippet": PAGE}]})
    trace["llm_calls"] = [{"label": "paper-evidence", "model": "m",
                           "messages": [{"role": "system", "content": "rules"},
                                        {"role": "user", "content": "PAGE 2:\n" + PAGE * 3}],
                           "system_prompt_sha256": "ab" * 32, "response": PAGE[:1500], "latency_ms": 5,
                           "timeout": 30, "error": {"type": "ValueError", "message": "bad /Users/alice/x"}}]
    trace["errors"] = [{"type": "OSError", "message": "[Errno 2] /Users/alice/f2w/papers/x.pdf: " + PAGE[:200],
                        "where": "paper_evidence"}]
    trace["orchestration"]["raw_classifier_output"] = '{"classification": "x", "quote": "' + PAGE[:300] + '"}'
    return trace


def test_trace_projection_has_no_prompt_or_page_text():
    proj, viewer, _ = projector()
    out = proj.project_trace(raw_trace(), viewer)
    assert leaks(out) == []
    assert set(out) == set(raw_trace())
    call = out["llm_calls"][0]
    assert call["message_count"] == 2 and set(call["prompt"]) == {"sha256", "chars"}
    assert call["prompt"]["chars"] > 3 * len(PAGE)
    assert set(out["retrieval"]["merged_context"]) == {"sha256", "chars"}
    assert out["retrieval"]["parsed_verdict"] == {"sufficient": True, "missing_topics": []}
    assert out["errors"] == [{"code": "internal", "type": "OSError", "where": "paper_evidence"}]
    assert "settings" not in out["env"] and out["env"]["graphs"][0]["graph"] == "matkg_contract_v1.json"
    # The projected answer and citations are still there.
    assert out["response"]["answer"].endswith("[KG: matkg:TermA].")
    assert out["response"]["citations"]["kg"] == ["matkg:TermA"]


# ------------------------------------------------------------------ N5: errors


@pytest.mark.parametrize("message", [
    "JSON graph not found: /Users/alice/f2w/storage/kg/missing.json",
    "Traceback (most recent call last):\n  File \"/Users/alice/f2w/app/x.py\", line 3",
    "'NoneType' object has no attribute 'get'",
    "[Errno 13] Permission denied: '/private/tmp/f2w'",
    "HTTPConnectionPool(host='tiled', port=8000): Max retries exceeded",
    "upstream said: api_key=sk-live-abcdefghijkl",
    "x" * 400,
])
def test_error_messages_are_typed_or_path_free(message):
    proj, viewer, _ = projector()
    out = proj.project_http_error(404, {"detail": message}, viewer)
    assert list(out) == ["detail"] and isinstance(out["detail"], str)
    assert leaks(out) == []
    assert "sk-live" not in out["detail"] and len(out["detail"]) <= 300


def test_legacy_validation_messages_keep_their_wording():
    proj, viewer, _ = projector()
    for message in ("Self relationships are not supported", "Invalid session ID",
                    "candidate_index is required to download a paper", "Node not found: matkg:Missing"):
        assert proj.project_http_error(400, {"detail": message}, viewer) == {"detail": message}
        assert proj.project_error_message(message, viewer) == message


def test_project_exception_never_returns_raw_str_exc():
    proj, viewer, _ = projector()
    cases = [
        (FileNotFoundError("/Users/alice/f2w/storage/kg/x.json"), ErrorCode.NOT_FOUND),
        (KeyError("secret_key"), ErrorCode.INTERNAL),
        (RuntimeError("synthetic retrieval failure " + PAGE[:100]), ErrorCode.INTERNAL),
        (ValueError("JSON graph mode requires a graph file"), ErrorCode.INVALID_INPUT),
        (ContractError(ErrorCode.DENIED, "Not permitted for this profile"), ErrorCode.DENIED),
    ]
    for exc, code in cases:
        got_code, message = proj.project_exception(exc, viewer)
        assert got_code is code
        assert leaks({"m": message}) == [] and "secret_key" not in message
    assert proj.project_exception(ValueError("JSON graph mode requires a graph file"), viewer)[1] == \
        "JSON graph mode requires a graph file"


def test_settings_and_health_paths_become_opaque():
    proj, viewer, _ = projector()
    settings = {"json_graph_path": "storage/kg/a.json", "available_graphs": [{"path": "/Users/alice/f2w/run/uploads/u.json"}],
                "tiled_uri": "http://user:pw@tiled.example:8000/api?api_key=sk-live-abcdefghijkl",
                "tiled_error": "probe failed: HTTPConnectionPool(host='x') Max retries exceeded"}
    out = proj.project_settings(settings, viewer)
    assert out["json_graph_path"] == "storage/kg/a.json"
    assert out["available_graphs"][0]["path"] == "f2w://workdir/uploads/u.json"
    assert out["tiled_uri"] == "http://tiled.example:8000/api"
    assert "Max retries" not in out["tiled_error"]
    health = proj.project_paths_only({"status": "ok", "workdir": "/Users/alice/f2w/run",
                                      "session_memory": "/Users/alice/f2w/run/session_memory.json"})
    assert health == {"status": "ok", "workdir": "f2w://workdir", "session_memory": "f2w://workdir/session_memory.json"}


# ------------------------------------------------------------------ N9, N2: graph properties and artifact URLs


GRAPH_NODE_KEYS = ["id", "label", "type", "description", "publications", "code_snippet", "code_language",
                   "function_name", "linked_code_snippets", "graph_id", "graph_label", "formula", "source_papers",
                   "properties", "extra_fields", "source_type", "repo_url", "repo_owner", "repo_name",
                   "repo_commit_sha", "source_file_path", "source_file_url", "source_start_line",
                   "source_end_line", "repository_license"]


def splash_node(properties):
    node = {k: None for k in GRAPH_NODE_KEYS}
    node.update(id="matkg:A", label="A", type="Technique", description="Term A.", publications=[],
                linked_code_snippets=[], source_papers=["x.pdf"], properties=properties,
                extra_fields={"context_snippets": [{"text": PAGE, "page": 2}], "entityType": "Technique",
                              "figure_url": "https://f2w.example/artifacts/fig1.png"})
    return node


@pytest.mark.parametrize("props", [
    {"context_snippets": [{"text": PAGE, "source_paper": "x.pdf", "page": 2}], "esaf_number": "123",
     "has_evidence": [PAGE], "crop_url": "http://localhost:8090/assets/crops/fig1.png",
     "figure": "/papers/rsoxs/2024/x.pdf", "full_text": PAGE, "image_path": "/Users/alice/f2w/crops/a.png"},
    [{"property": "context_snippets", "value": [{"text": PAGE}]}, {"property": "esaf_number", "value": "123"},
     {"property": "figure_url", "value": "file:///Users/alice/f2w/crops/a.png"},
     {"property": "thumbnail", "value": "https://f2w.example/blobs/sha256/abcd"},
     {"property": "page_text", "value": PAGE}],
])
def test_graph_node_private_properties_and_artifact_urls_removed(props):
    proj, viewer, _ = projector()
    out = proj.project_graph_node(splash_node(props), viewer)
    assert list(out) == GRAPH_NODE_KEYS
    assert leaks(out) == [] and "crops" not in json.dumps(out) and "artifacts/" not in json.dumps(out)
    assert "blobs/" not in json.dumps(out) and "/papers/" not in json.dumps(out)
    kept = out["properties"]
    if isinstance(kept, dict):
        assert kept == {"esaf_number": "123"}
    else:
        assert kept == [{"property": "esaf_number", "value": "123"}]
    assert out["extra_fields"] == {"entityType": "Technique"}


def test_graph_payload_and_neighborhood_strip_edge_evidence():
    proj, viewer, _ = projector()
    payload = {"nodes": [splash_node({"context_snippets": [PAGE]})],
               "edges": [{"source": "a", "target": "b", "predicate": "p", "has_evidence": [{"text": PAGE}]}],
               "source_path": "/Users/alice/f2w/run/uploads/u.json"}
    out = proj.project_graph_payload(payload, viewer)
    assert list(out) == ["nodes", "edges", "source_path"] and leaks(out) == []
    assert out["source_path"] == "f2w://workdir/uploads/u.json"
    assert "/uploads/" in out["source_path"]  # the UI's uploadedGraphQueryParam test still recognizes it


def test_repo_code_provenance_is_kept_and_local_urls_dropped():
    proj, viewer, _ = projector()
    node = splash_node([])
    node.update(code_snippet="def f():\n    return 1", repo_url="https://github.com/o/r",
                source_file_url="file:///Users/alice/r/x.py", source_file_path="src/x.py")
    out = proj.project_graph_node(node, viewer)
    assert out["code_snippet"] == "def f():\n    return 1" and out["repo_url"] == "https://github.com/o/r"
    assert out["source_file_url"] is None and out["source_file_path"] == "src/x.py"


@pytest.mark.parametrize("path", [
    "/papers/rsoxs/2024/10.1002_x.pdf", "/static/papers/x.pdf", "/assets/crops/fig-1a.png", "/figures/fig1",
    "/artifacts/sha256:abcd", "/fair2wise/api/papers/x.pdf", "/crops/x.png", "/source_index/literature/chunks.jsonl",
    "/fulltext/work:1", "/sections/work:1/3", "/chunks/12", "/blobs/sha256/ab", "/files/x.pdf", "/api/pdf/x"])
def test_guessed_source_urls_are_denied(path):
    assert source_route_denied(path)


def test_no_frozen_legacy_route_is_source_serving():
    routes = json.loads((S.FIXTURES / "routes.json").read_text(encoding="utf-8"))
    paths = [r["path"] for group in routes.values() if isinstance(group, list) for r in group]
    assert paths and not [p for p in paths if source_route_denied(p)]


# ------------------------------------------------------------------ N10, P6: graph export


def test_graph_export_keeps_facts_and_moves_snippets_to_private_evidence():
    proj, _viewer, _ = projector()
    kg = {"things": [{"id": "matkg:A", "name": "A", "category": "Technique", "description": "Term A.",
                      "context_snippets": [{"text": PAGE[:900], "source_paper": "x.pdf", "page": 2},
                                           {"text": PAGE[900:1800], "source_paper": "x.pdf", "page": 2}]}],
          "associations": [{"subject": "matkg:A", "predicate": "rel:p", "object": "matkg:B",
                            "has_evidence": [{"text": PAGE[:400], "source_paper": "x.pdf", "page": 2}]}],
          "metadata": {"version": "v1"}}
    public, private = proj.split_graph_export(kg)
    text = json.dumps(public)
    assert PAGE[:60] not in text and S.SENTINEL not in text
    assert public["things"][0]["name"] == "A" and public["things"][0]["description"] == "Term A."
    assert public["associations"][0]["object"] == "matkg:B" and public["metadata"] == {"version": "v1"}
    refs = [r["evidence_ref"] for r in public["things"][0]["context_snippets"]]
    assert len(refs) == 2 and {r["evidence_ref"] for r in private} >= set(refs)
    assert len(private) == 3 and all(r["text"] for r in private)
    # Re-projection of the public part is stable (re-import / readback).
    assert proj.split_graph_export(public)[0]["things"][0]["context_snippets"][0]["source_paper"] == "x.pdf"
