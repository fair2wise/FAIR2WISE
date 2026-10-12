"""T21B projection over every recorded T18A legacy-UI payload (QA-01, QUOTE-01, SEC-04, SEC-05, TIL-05).

For each of the 66 HTTP fixtures and the 12 SSE transcripts in
``tests/contracts/legacy_ui/fixtures`` this runs the projection twice:

1. on the recorded payload as is, asserting the fields the frozen UI reads are
   still present with the same key order, the same JSON type skeleton (except
   the documented internal-only ``rounds`` contents and the trace detail), the
   same statuses, ids, citation tokens and pending cards;
2. on the same payload with source-serving content injected where the
   baseline code would put it (page text in answers, rounds, traces,
   properties, edges, progress data, errors), asserting none of it survives and
   the result has the same skeleton as the plain projection.

Places where the legacy shape and the disclosure rules pull against each other
are listed in ``KNOWN_CONFLICTS`` and asserted explicitly; nothing is weakened
silently.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _disclosure_support as S  # noqa: E402
import _legacy_ui_harness as H  # noqa: E402
from app.modules.security.disclosure import citation_tokens, normalize_for_match  # noqa: E402

CASES: Dict[str, Dict[str, Any]] = {
    p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((S.FIXTURES / "http").glob("*.json"))}
SSE_IDS = [cid for cid, c in CASES.items() if "sse_events" in c["response"]]
PAGE = S.synthetic_page(2)
ECHO = normalize_for_match(PAGE)
SOURCES = [S.page_ref(2)]

# Frozen field lists (copied from tests/contracts/legacy_ui/test_legacy_ui_contract.py, FROZEN_MODEL_FIELDS).
CHAT_RESPONSE_FIELDS = ["status", "answer", "sufficient", "node_ids", "publications", "confidence", "rounds",
                        "graph", "graph_source_requested", "graph_source_used", "workdir", "pending",
                        "orchestration", "turn_id"]
GRAPH_NODE_FIELDS = ["id", "label", "type", "description", "publications", "code_snippet", "code_language",
                     "function_name", "linked_code_snippets", "graph_id", "graph_label", "formula", "source_papers",
                     "properties", "extra_fields", "source_type", "repo_url", "repo_owner", "repo_name",
                     "repo_commit_sha", "source_file_path", "source_file_url", "source_start_line",
                     "source_end_line", "repository_license"]
LINKED_SNIPPET_FIELDS = ["id", "label", "function_name", "code_language", "code_snippet", "publications",
                         "source_type", "repo_url", "repo_owner", "repo_name", "repo_commit_sha", "source_file_path",
                         "source_file_url", "source_start_line", "source_end_line"]
PENDING_PAPER_FIELDS = {"title", "doi", "publication_year", "abstract", "score", "repository", "recommended", "index"}
TOP_LEVEL_FIELDS = {
    "/settings": ["backend", "model", "graph_source", "workflow_mode", "extraction_mode", "targeted_max_pages",
                  "json_graph_path", "json_graph_paths", "kg_query_max_nodes", "kg_query_hops", "source_rag",
                  "use_live_tiled", "tiled_uri", "tiled_api_key_set", "tiled_status", "tiled_error",
                  "available_json_graphs", "available_graphs", "selected_graph_ids", "available_cborg_models",
                  "default_ollama_model"],
    "/publications/search": ["status", "query", "publications", "matched_node_ids", "source"],
    "/graph/nodes/search": ["query", "retrieval_backend", "results"],
    "/graph/upload": ["graph", "graph_path", "filename"],
}

# Where the legacy wire value and the disclosure rule conflict (owner questions; see the T21B report).
KNOWN_CONFLICTS = {
    "graph_upload": "GraphPayload.source_path / GraphUploadResponse.graph_path are absolute workdir paths. "
                    "Projected to f2w://workdir/uploads/<name>.json. The UI keeps working for overlay detection "
                    "('/uploads/' test, GraphMockup.tsx:434-438) but sends the value back as ?json_graph_path= to "
                    "GET /graph/node, which today resolves only real paths (api.py:3402-3418): the route must "
                    "accept the opaque id before the projection is wired in.",
    "graph_node_get_uploaded": "Request side of the same round trip (recorded with the absolute path).",
    "settings_get": "AgentSettingsResponse.tiled_uri is internal-only in route_field_map ST.paths but the "
                    "settings dialog displays and edits it; kept (credentials/query stripped) pending owner.",
    "traces_get": "GET /traces/{turn_id} keeps its top-level keys only; prompts, merged context, env.settings "
                  "and raw outputs become digests. scripts/check_trace.py and run_eval_batch.py read raw "
                  "traces and must move to the protected store (ADR section 8).",
}

LEAK_RE = re.compile(r"<WORKSPACE>|<REPO>|<HOME>|/Users/|/private/|/var/folders|object has no attribute|"
                     r"Traceback|\[Errno|" + re.escape(S.SENTINEL))


def _path_of(case) -> str:
    return case["request"]["path"]


def _body(case):
    resp = case["response"]
    return resp["json"] if "json" in resp else resp.get("text")


def project_case(case, *, inject: bool = False, ctx=None):
    """Return the projected response (body or list of SSE events) for one fixture."""
    projector, viewer, _ = S.make_projector(ctx or S.local_context(), roots=S.fixture_roots())
    resp = case["response"]
    if "sse_events" in resp:
        events = copy.deepcopy(resp["sse_events"])
        if inject:
            events = [dict(e, data=inject_event(e["event"], e["data"])) for e in events]
        return [{"event": e["event"], "data": projector.project_sse_event(e["event"], e["data"], viewer,
                                                                          sources=SOURCES)} for e in events]
    body = copy.deepcopy(_body(case))
    if inject:
        body = inject_body(_path_of(case), resp["status_code"], body)
    return projector.project_route_response(case["request"]["method"], _path_of(case), resp["status_code"], body,
                                            viewer, sources=SOURCES)


# ------------------------------------------------------------------ injection of source-serving content


def _inject_node(node):
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, list):
            props.append({"property": "context_snippets", "value": [{"text": PAGE, "page": 2}]})
        elif isinstance(props, dict):
            props["context_snippets"] = [{"text": PAGE}]
        if isinstance(node.get("extra_fields"), dict):
            node["extra_fields"].update(full_text=PAGE, crop_url="http://localhost:8090/assets/crops/f.png")
        for pub in node.get("publications") or []:
            if isinstance(pub, dict):
                pub["pdf_path"] = "<WORKSPACE>/papers/x.pdf"
    return node


def _inject_graph(graph):
    if isinstance(graph, dict):
        for node in graph.get("nodes") or []:
            _inject_node(node)
        for edge in graph.get("edges") or []:
            edge["has_evidence"] = [{"text": PAGE}]
    return graph


def _inject_chat(body):
    if not isinstance(body, dict) or "rounds" not in body:
        return body
    if isinstance(body.get("answer"), str):
        body["answer"] += "\n\n" + ECHO + " [PDF: synthetic_paper.pdf p.2]"
    body["rounds"] = (body.get("rounds") or []) + [{
        "round": 9, "retrieval": {"status": "success", "answer": PAGE, "selected": [],
                                  "error": "Traceback (most recent call last): <WORKSPACE>/x.py"},
        "extra": {"merged_context": PAGE}, "pending_pdfs": ["<WORKSPACE>/run/pdfs/x.pdf"]}]
    _inject_graph(body.get("graph"))
    for pub in body.get("publications") or []:
        pub["abstract_text"] = "x " * 900 + PAGE
    pending = body.get("pending")
    if isinstance(pending, dict):
        for paper in pending.get("papers") or []:
            paper["abstract"] = ("y" * 650) + PAGE
            paper["pdf_urls"] = ["http://localhost:8090/papers/x.pdf"]
    return body


def inject_body(path: str, status: int, body):
    if status >= 400:
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            body["detail"] = f"Traceback (most recent call last): {S.SENTINEL} <WORKSPACE>/run/x.json"
        return body
    if path in ("/chat", "/chat/action"):
        return _inject_chat(body)
    if path.startswith("/traces/") and isinstance(body, dict):
        body["retrieval"]["merged_context"] = PAGE
        body["retrieval"]["raw_judge_output"] = PAGE
        body["llm_calls"] = [{"label": "judge", "model": "m", "messages": [{"role": "user", "content": PAGE}],
                              "response": PAGE, "error": {"type": "KeyError", "message": S.SENTINEL}}]
        body["errors"] = [{"type": "OSError", "message": PAGE, "where": "x"}]
        body["response"]["answer"] += " " + ECHO
        for item in body.get("progress") or []:
            item["data"]["merged_context"] = PAGE
        return body
    if path.startswith("/graph/node/"):
        return _inject_node(body)
    if path == "/graph" or path.startswith("/graph/neighborhood/"):
        return _inject_graph(body)
    if path == "/graph/nodes/search":
        for result in body.get("results") or []:
            _inject_node(result.get("node"))
        return body
    if path == "/graph/upload":
        _inject_graph(body.get("graph"))
        return body
    if path == "/publications/search":
        for pub in body.get("publications") or []:
            pub["abstract_text"] = "x " * 900 + PAGE
            pub["context_snippets"] = [PAGE]
        return body
    if path == "/settings":
        body["tiled_error"] = f"KeyError: {S.SENTINEL} at <WORKSPACE>/x"
        return body
    if path in ("/api/chat", "/v1/chat/completions"):
        if isinstance(body.get("message"), dict):
            body["message"]["content"] += " " + ECHO
        for choice in body.get("choices") or []:
            choice["message"]["content"] += " " + ECHO
        return body
    return body


def inject_event(event: str, data):
    data = copy.deepcopy(data)
    if event == "progress":
        data["merged_context"] = PAGE
        data["prompt"] = PAGE
        if "graph" in data:
            _inject_graph(data["graph"])
        return data
    if data.get("status") == "stream_error":
        data["message"] = f"'list' object has no attribute 'get' {S.SENTINEL} <WORKSPACE>/x"
        return data
    return _inject_chat(data)


# ------------------------------------------------------------------ UI contract checks


def _strip(value, drop: Tuple[str, ...]):
    if isinstance(value, dict):
        return {k: _strip(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [_strip(v, drop) for v in value]
    return value


def assert_chat_contract(original, projected, fid):
    assert list(projected) == CHAT_RESPONSE_FIELDS, fid
    assert set(projected["graph"]) == {"nodes", "edges", "source_path"}, fid
    for key in ("status", "sufficient", "node_ids", "confidence", "graph_source_requested", "graph_source_used",
                "turn_id", "orchestration"):
        assert projected[key] == original[key], (fid, key)
    assert citation_tokens(projected["answer"])[:len(citation_tokens(original["answer"]))] == \
        citation_tokens(original["answer"]), fid
    assert isinstance(projected["rounds"], list) and isinstance(projected["workdir"], str), fid
    recorded = [r.get("round") for r in original["rounds"]]
    assert [r.get("round") for r in projected["rounds"]][:len(recorded)] == recorded, fid
    pending = projected["pending"]
    if pending and pending.get("kind") == "download":
        for p, o in zip(pending["papers"], original["pending"]["papers"]):
            assert set(p) >= PENDING_PAPER_FIELDS and {k: p[k] for k in PENDING_PAPER_FIELDS if k != "abstract"} == \
                {k: o[k] for k in PENDING_PAPER_FIELDS if k != "abstract"}, fid
    if pending and pending.get("kind") == "extraction":
        assert pending["candidate"]["source_paper"] == original["pending"]["candidate"]["source_paper"], fid
    for pn, on in zip(projected["graph"]["nodes"], original["graph"]["nodes"]):
        assert_node_contract(on, pn, fid)


def assert_node_contract(original, projected, fid):
    assert list(projected) == GRAPH_NODE_FIELDS, fid
    for key in ("id", "label", "type", "description", "graph_id", "graph_label", "source_papers", "code_snippet",
                "formula"):
        assert projected[key] == original[key], (fid, key)
    for ps, os_ in zip(projected["linked_code_snippets"], original["linked_code_snippets"]):
        assert list(ps) == LINKED_SNIPPET_FIELDS and ps["code_snippet"] == os_["code_snippet"], fid
    assert [{k: v for k, v in p.items() if k in ("source_paper", "doi", "paper_title", "publication_year")}
            for p in projected["publications"]] == \
        [{k: v for k, v in p.items() if k in ("source_paper", "doi", "paper_title", "publication_year")}
         for p in original["publications"]], fid


def check_ui_contract(fid: str, case, projected):
    path = _path_of(case)
    resp = case["response"]
    original = resp.get("sse_events") or _body(case)
    turn_id = (case["request"].get("json") or {}).get("client_turn_id")
    if "sse_events" in resp:
        assert [e["event"] for e in projected] == [e["event"] for e in original], fid
        assert projected[0]["data"] == original[0]["data"], fid  # turn_started
        for pe, oe in zip(projected, original):
            data, odata = pe["data"], oe["data"]
            if pe["event"] == "progress":
                assert list(data)[:3] == ["phase", "message", "turn_id"] == list(odata)[:3], fid
                assert data["phase"] == odata["phase"] and data["turn_id"] == turn_id, fid
                if data["phase"] == "graph_update":
                    assert data["node_ids"] == odata["node_ids"] and set(data["graph"]) == {"nodes", "edges"}, fid
                    for pn, on in zip(data["graph"]["nodes"], odata["graph"]["nodes"]):
                        assert_node_contract(on, pn, fid)
            elif data.get("status") == "stream_error":
                assert list(data) == ["status", "message", "turn_id"] and data["turn_id"] == turn_id, fid
            else:
                assert_chat_contract(odata, data, fid)
        # The projected events still frame as baseline SSE (api.py:1667-1669) and parse strictly.
        raw = "".join(f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False)}\n\n"
                      for e in projected).encode("utf-8")
        assert H.parse_sse(raw) == projected, fid
        return
    if resp["status_code"] >= 400:
        if isinstance(original, dict):
            assert list(projected) == list(original), fid
            assert H.shape(projected) == H.shape(original), fid  # {"detail": str|list} or {"error": str}
        else:
            assert isinstance(projected, str), fid
        return
    if path in ("/chat", "/chat/action"):
        assert_chat_contract(original, projected, fid)
        assert projected["turn_id"] == turn_id, fid
    elif path.startswith("/graph/node/"):
        assert_node_contract(original, projected, fid)
    elif path == "/graph" or path.startswith("/graph/neighborhood/"):
        assert list(projected) == ["nodes", "edges", "source_path"], fid
        assert projected["edges"] == original["edges"], fid
        for pn, on in zip(projected["nodes"], original["nodes"]):
            assert_node_contract(on, pn, fid)
    elif path in TOP_LEVEL_FIELDS:
        assert list(projected) == TOP_LEVEL_FIELDS[path], fid
        if path == "/graph/nodes/search":
            for pr, orr in zip(projected["results"], original["results"]):
                assert_node_contract(orr["node"], pr["node"], fid)
                assert pr["score"] == orr["score"]
    elif path.startswith("/traces/"):
        assert set(projected) == set(original), fid
    elif path in ("/api/chat", "/v1/chat/completions"):
        assert H.shape(projected) == H.shape(original), fid
    else:
        assert projected == original or H.shape(projected) == H.shape(original), fid


def assert_no_source_serving(obj, fid):
    text = json.dumps(obj, ensure_ascii=False)
    hit = LEAK_RE.search(text)
    assert hit is None, f"{fid}: {hit.group(0) if hit else ''} ...{text[max(0, hit.start() - 80):hit.end() + 40]}" \
        if hit else fid
    assert PAGE[:200] not in text and ECHO[:400] not in text, fid
    for key in ("merged_context", "raw_judge_output", "llm_calls"):
        for match in re.finditer(rf'"{key}": "', text):
            raise AssertionError(f"{fid}: {key} carries text at {match.start()}")


# ------------------------------------------------------------------ tests


def test_every_recorded_fixture_is_covered():
    assert len(CASES) == 66 and len(SSE_IDS) == 12


@pytest.mark.parametrize("fid", sorted(CASES))
def test_projection_keeps_legacy_ui_fields(fid):
    case = CASES[fid]
    projected = project_case(case)
    check_ui_contract(fid, case, projected)
    assert_no_source_serving(projected, fid)


@pytest.mark.parametrize("fid", sorted(CASES))
def test_projection_is_identical_in_shape_except_documented_internal_fields(fid):
    case = CASES[fid]
    projected = project_case(case)
    original = case["response"].get("sse_events") or _body(case)
    if _path_of(case).startswith("/traces/"):
        assert set(projected) == set(original)
        return
    assert H.shape(_strip(projected, ("rounds",))) == H.shape(_strip(original, ("rounds",))), fid


@pytest.mark.parametrize("fid", sorted(CASES))
def test_injected_source_content_never_survives(fid):
    case = CASES[fid]
    projected = project_case(case, inject=True)
    assert_no_source_serving(projected, fid)
    check_ui_contract(fid, case, projected)
    plain = project_case(case)
    if not _path_of(case).startswith("/traces/"):
        # Injection also fills allowed slots (a bounded abstract_text, a typed tiled_error); every other
        # injected key must be gone, so the skeleton equals the plain projection.
        allowed = ("rounds", "abstract_text", "tiled_error")
        assert H.shape(_strip(projected, allowed)) == H.shape(_strip(plain, allowed)), fid


@pytest.mark.parametrize("fid", ["chat_answered", "chat_stream_answered", "traces_get", "graph_node_get"])
def test_projection_is_role_independent_on_fixtures(fid):
    case = CASES[fid]
    outs = [json.dumps(project_case(case, inject=True, ctx=c))
            for c in (S.local_context(request_id="r"), S.als_context(admin=True, request_id="r"),
                      S.als_context(admin=False, request_id="r"))]
    assert outs[0] == outs[1] == outs[2]


def test_unchanged_fixtures_are_byte_identical_where_nothing_is_internal():
    """Most recorded payloads carry no internal-only or source content and pass through unchanged."""
    unchanged = [fid for fid, case in CASES.items() if project_case(case) == (
        case["response"].get("sse_events") or _body(case))]
    changed = sorted(set(CASES) - set(unchanged))
    expected_changed = {
        # absolute workdir paths (internal-only): health, session reset, chat workdir/source_path, uploads
        "health", "session_reset_named", "session_reset_legacy", "graph_upload", "graph_get", "graph_get_json_mode",
        # ChatResponse.rounds summary and workdir/source_path
        "chat_answered", "chat_insufficient_to_download", "chat_insufficient_for_failed_download",
        "chat_action_decline", "chat_action_no_pending",
        # SSE complete/error carry ChatResponse; stream_error raw exception text
        "chat_stream_answered", "chat_stream_insufficient_to_download", "chat_stream_direct_response",
        "chat_stream_retrieval_error", "chat_stream_error_malformed_verdict",
        "chat_stream_json_override_missing_file_falls_back", "chat_action_stream_download_yes",
        "chat_action_stream_extraction_yes", "chat_action_stream_download_failed_retry",
        "chat_action_stream_decline",
        # trace detail
        "traces_get",
    }
    assert set(changed) <= expected_changed, sorted(set(changed) - expected_changed)


def test_known_conflicts_are_the_documented_ones():
    up = project_case(CASES["graph_upload"])
    assert up["graph"]["source_path"] == up["graph_path"] == "f2w://workdir/uploads/contract_upload.json"
    assert "/uploads/" in up["graph"]["source_path"]
    req_path = CASES["graph_node_get_uploaded"]["request"]
    assert "<WORKSPACE>" in json.dumps(req_path)  # the UI sent back the absolute path it had received
    # kgCatalog.graphIdFromPath reads only the file name, so the primary graph id survives the opaque id.
    json_mode = project_case(CASES["graph_get_json_mode"])
    assert json_mode["source_path"] == "f2w://workspace/storage/kg/matkg_contract_v1.json"
    assert project_case(CASES["graph_get"])["source_path"] == "f2w://workdir/kg.json"
    settings = project_case(CASES["settings_get"])
    assert settings["tiled_uri"] == CASES["settings_get"]["response"]["json"]["tiled_uri"]
    assert set(KNOWN_CONFLICTS) == {"graph_upload", "graph_node_get_uploaded", "settings_get", "traces_get"}


def test_stream_error_raw_exception_becomes_typed_but_validation_text_stays():
    malformed = project_case(CASES["chat_stream_error_malformed_verdict"])[-1]["data"]["message"]
    assert "attribute" not in malformed and "error: internal" in malformed
    missing = project_case(CASES["chat_action_stream_error_missing_candidate"])[-1]["data"]["message"]
    assert missing == "candidate_index is required to download a paper"
    empty = project_case(CASES["chat_stream_error_empty_json_selection"])[-1]["data"]["message"]
    assert empty == "JSON graph mode requires a graph file"


def test_answered_fixture_answer_is_unchanged():
    out = project_case(CASES["chat_answered"])
    assert out["answer"] == CASES["chat_answered"]["response"]["json"]["answer"]
    stream = project_case(CASES["chat_stream_answered"])[-1]["data"]
    assert stream["answer"] == CASES["chat_stream_answered"]["response"]["sse_events"][-1]["data"]["answer"]
    assert "μ" in stream["answer"]
