"""T18A frozen legacy-UI contract: routes, OpenAPI, payloads, SSE and storage keys.

Replays the recorded baseline flows (fixtures/http, fixtures/sse) against a
freshly constructed in-process app with fake inference and temp state, then
compares. Fixtures were produced from the frozen baseline 222b1e7 by
record_fixtures.py. No network, no .env, no model or Tiled service.

Run from a clean export:
    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> <venv>/bin/python -m pytest tests/contracts/legacy_ui -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _legacy_ui_harness as H  # noqa: E402

REPO_ROOT = HERE.parents[2]
FIX = H.FIXTURE_DIR

# Fixtures were recorded at baseline 222b1e7, then refreshed for the optional
# ChatRequest.batch_id field (pre-migration commit 2430320), adopted at H1 (H1-6).

# The 19 UI-agent routes (f2w_http_routes.json, api.py:6625-7029) and the six
# model-compatible routes (kg_rag_api.py:246-316). FastAPI adds /openapi.json,
# /docs, /docs/oauth2-redirect and /redoc on both apps.
UI_ROUTES = {
    ("GET", "/health"), ("GET", "/settings"), ("PUT", "/settings"), ("POST", "/session/reset"),
    ("DELETE", "/session/{session_id}"), ("GET", "/graph"), ("POST", "/graph/nodes/search"),
    ("GET", "/graph/neighborhood/{node_id:path}"), ("GET", "/graph/node/{node_id}"),
    ("PATCH", "/graph/node/{node_id}"), ("POST", "/graph/upload"), ("POST", "/chat"),
    ("POST", "/chat/action"), ("POST", "/publications/search"), ("POST", "/chat/stream"),
    ("POST", "/chat/action/stream"), ("GET", "/traces/{turn_id}"), ("POST", "/telemetry/ui"),
    ("POST", "/annotations"),
}
MODEL_ROUTES = {
    ("POST", "/api/chat"), ("POST", "/v1/chat/completions"), ("GET", "/api/tags"),
    ("GET", "/api/version"), ("GET", "/v1/models"), ("GET", "/api/ps"),
}
FRAMEWORK_ROUTES = {("GET", "/openapi.json"), ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"),
                    ("GET", "/redoc"), ("HEAD", "/openapi.json"), ("HEAD", "/docs"),
                    ("HEAD", "/docs/oauth2-redirect"), ("HEAD", "/redoc")}

CHAT_RESPONSE_FIELDS = [
    "status", "answer", "sufficient", "node_ids", "publications", "confidence", "rounds", "graph",
    "graph_source_requested", "graph_source_used", "workdir", "pending", "orchestration", "turn_id",
]
# Pydantic field order is part of the serialized JSON key order.
FROZEN_MODEL_FIELDS = {
    "ChatRequest": ["message", "messages", "session_id", "client_turn_id", "ui_settings", "batch_id",
                    "graph_source", "json_graph_path", "json_graph_paths", "source_rag", "use_live_tiled"],
    "ChatActionRequest": ["decision", "kind", "candidate_index", "messages", "session_id", "client_turn_id",
                          "parent_turn_id", "ui_settings", "graph_source", "json_graph_path"],
    "ChatResponse": CHAT_RESPONSE_FIELDS,
    "GraphPayload": ["nodes", "edges", "source_path"],
    "GraphEdge": ["source", "target", "predicate"],
    "GraphNode": ["id", "label", "type", "description", "publications", "code_snippet", "code_language",
                  "function_name", "linked_code_snippets", "graph_id", "graph_label", "formula",
                  "source_papers", "properties", "extra_fields", "source_type", "repo_url", "repo_owner",
                  "repo_name", "repo_commit_sha", "source_file_path", "source_file_url",
                  "source_start_line", "source_end_line", "repository_license"],
    "LinkedCodeSnippet": ["id", "label", "function_name", "code_language", "code_snippet", "publications",
                          "source_type", "repo_url", "repo_owner", "repo_name", "repo_commit_sha",
                          "source_file_path", "source_file_url", "source_start_line", "source_end_line"],
    "GraphNodeUpdateRequest": ["label", "type", "description", "code_snippet", "publications",
                               "linked_code_snippets", "relationship_updates"],
    "GraphRelationshipUpdate": ["action", "source", "predicate", "target"],
    "AgentSettingsResponse": ["backend", "model", "graph_source", "workflow_mode", "extraction_mode",
                              "targeted_max_pages", "json_graph_path", "json_graph_paths",
                              "kg_query_max_nodes", "kg_query_hops", "source_rag", "use_live_tiled",
                              "tiled_uri", "tiled_api_key_set", "tiled_status", "tiled_error",
                              "available_json_graphs", "available_graphs", "selected_graph_ids",
                              "available_cborg_models", "default_ollama_model"],
    "AgentSettingsUpdate": ["backend", "model", "graph_source", "workflow_mode", "extraction_mode",
                            "targeted_max_pages", "json_graph_path", "json_graph_paths", "selected_graph_ids",
                            "kg_query_max_nodes", "kg_query_hops", "source_rag", "use_live_tiled", "tiled_uri"],
    "SessionResetResponse": ["status", "session_memory", "session_memory_has_context", "workflow_state",
                             "workflow_phase"],
    "PublicationSearchResponse": ["status", "query", "publications", "matched_node_ids", "source"],
    "GraphNodeSearchResponse": ["query", "retrieval_backend", "results"],
    "GraphUploadResponse": ["graph", "graph_path", "filename"],
    "AnnotationRequest": ["turn_id", "verdict", "failure_tags", "note", "annotator", "ts"],
    "UiTelemetryEvent": ["type", "turn_id", "ts", "data"],
}
# Literal statuses the recorded flows reach (subset of f2w_ui_contracts.json; not a closed enum).
OBSERVED_CHAT_STATUSES = {
    "direct_response", "answered", "awaiting_download_decision", "awaiting_extraction_decision",
    "stopped_by_user", "no_pending_action", "retrieval_error", "stream_error",
}
SSE_EVENTS = {"progress", "complete", "error"}


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _case_files() -> List[Path]:
    return sorted((FIX / "http").glob("*.json"))


def _cases() -> Dict[str, Dict[str, Any]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in _case_files()}


CASES = _cases()
UI_STEP_IDS = [step[0] for flow in H.flows().values() for step in flow["steps"]]
MODEL_STEP_IDS = [step[0] for step in H.MODEL_STEPS]
SSE_IDS = [cid for cid, c in CASES.items() if "sse_file" in c["response"]]


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    """Build fresh apps and replay every recorded step in order."""
    mp = pytest.MonkeyPatch()
    try:
        ctx = H.install(mp, tmp_path_factory.mktemp("legacy-ui") / "ws", REPO_ROOT)
        results: Dict[str, Any] = {"ctx": ctx, "responses": {}, "raw": {}}
        for flow in H.flows().values():
            app = H.make_ui_app(ctx, kg_mode=flow["kg_mode"])
            results["ui_app"] = app
            results["ui_openapi"] = app.openapi()
            client = H.client_for(app)
            for fid, method, path, kwargs, _notes in flow["steps"]:
                resp = H.perform(client, ctx, method, path, kwargs)
                results["responses"][fid] = H.response_record(resp, ctx)
                results["raw"][fid] = resp.content
        model_app = H.make_model_app(mp, ctx)
        results["model_app"] = model_app
        results["model_openapi"] = model_app.openapi()
        model_client = H.client_for(model_app)
        for fid, method, path, kwargs, _notes in H.MODEL_STEPS:
            resp = H.perform(model_client, ctx, method, path, kwargs)
            results["responses"][fid] = H.response_record(resp, ctx)
            results["raw"][fid] = resp.content
        results["calls"] = dict(ctx.calls)
        yield results
    finally:
        mp.undo()


def _route_set(app) -> set:
    out = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or []:
            out.add((method, route.path))
    return out


# ------------------------------------------------------------------ route set and schema


def test_ui_route_set_is_the_frozen_19(replay):
    routes = _route_set(replay["ui_app"]) - FRAMEWORK_ROUTES
    assert routes == UI_ROUTES
    recorded = {(m, r["path"]) for r in _load("routes.json")["ui_app"] for m in r["methods"]}
    assert recorded - FRAMEWORK_ROUTES == UI_ROUTES


def test_model_route_set_is_the_frozen_6(replay):
    routes = _route_set(replay["model_app"]) - FRAMEWORK_ROUTES
    assert routes == MODEL_ROUTES


def test_ui_openapi_matches_baseline(replay):
    assert replay["ui_openapi"] == _load("openapi_ui.json")


def test_model_openapi_matches_baseline(replay):
    assert replay["model_openapi"] == _load("openapi_model_api.json")


@pytest.mark.parametrize("model_name", sorted(FROZEN_MODEL_FIELDS))
def test_pydantic_model_fields_frozen(replay, model_name):
    from app.modules.f2w_agent import api as api_mod

    model = getattr(api_mod, model_name)
    assert list(model.model_fields) == FROZEN_MODEL_FIELDS[model_name]


def test_snippet_action_alias_and_enums_frozen(replay):
    schemas = _load("openapi_ui.json")["components"]["schemas"]
    snippet = schemas["LinkedCodeSnippetUpdate"]["properties"]
    assert "_action" in snippet and "action" not in snippet
    assert snippet["_action"]["pattern"] == "^(upsert|unlink)$"
    assert schemas["GraphRelationshipUpdate"]["properties"]["action"]["pattern"] == "^(add|remove)$"
    for name in ("ChatRequest", "ChatActionRequest", "AgentSettingsUpdate"):
        gs = schemas[name]["properties"]["graph_source"]["anyOf"][0]
        assert gs["pattern"] == "^(splash|json)$", name
    assert schemas["ChatActionRequest"]["properties"]["decision"]["pattern"] == "^(yes|no)$"
    assert schemas["AnnotationRequest"]["properties"]["verdict"]["pattern"] == "^(pass|fail)$"


def test_stream_routes_declare_no_response_model():
    spec = _load("openapi_ui.json")
    for path in ("/chat/stream", "/chat/action/stream"):
        ok = spec["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok == {}, path  # OpenAPI does not describe the SSE body; the fixtures do.


# ------------------------------------------------------------------ recorded payloads


def test_every_route_has_a_recorded_fixture():
    covered = set()
    for case in CASES.values():
        req = case["request"]
        covered.add((req["method"], req["path"]))
    templates = {
        ("DELETE", "/session/{session_id}"): r"^/session/[^/]+$",
        ("GET", "/graph/neighborhood/{node_id:path}"): r"^/graph/neighborhood/.+$",
        ("GET", "/graph/node/{node_id}"): r"^/graph/node/[^/]+$",
        ("PATCH", "/graph/node/{node_id}"): r"^/graph/node/[^/]+$",
        ("GET", "/traces/{turn_id}"): r"^/traces/[^/]+$",
    }
    for method, path in UI_ROUTES | MODEL_ROUTES:
        pattern = templates.get((method, path), "^" + re.escape(path) + "$")
        assert any(m == method and re.match(pattern, p) for m, p in covered), (method, path)


def test_fixture_ids_match_flow_definition():
    assert sorted(CASES) == sorted(UI_STEP_IDS + MODEL_STEP_IDS)


@pytest.mark.parametrize("fid", UI_STEP_IDS + MODEL_STEP_IDS)
def test_replayed_response_matches_fixture(replay, fid):
    expected = H.expected_response(fid, CASES[fid]["response"])
    actual = replay["responses"][fid]
    assert actual["status_code"] == expected["status_code"]
    assert actual["content_type"] == expected["content_type"]
    assert actual.get("cors_headers") == expected.get("cors_headers")
    if "sse_events" in expected:
        assert actual["sse_events"] == expected["sse_events"]
    elif "json" in expected:
        assert actual["json"] == expected["json"]
        assert H.shape(actual["json"]) == H.shape(expected["json"])
    else:
        assert actual.get("text") == expected.get("text")


def test_no_fixture_leaks_local_paths_or_secrets():
    pattern = re.compile(r"/Users/|/private/tmp|/var/folders|scratchpad|sk-[A-Za-z0-9]{8,}|api[_-]?key\"?: \"[^\"]+")
    for path in sorted(FIX.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            hit = pattern.search(text)
            assert hit is None, f"{path.name}: {hit.group(0) if hit else ''}"


def test_chat_response_fields_on_every_chat_payload():
    for fid, case in CASES.items():
        resp = case["response"]
        bodies = []
        if resp.get("sse_events"):
            bodies = [e["data"] for e in resp["sse_events"] if e["event"] == "complete"]
            bodies += [e["data"] for e in resp["sse_events"]
                       if e["event"] == "error" and e["data"].get("status") != "stream_error"]
        elif case["request"]["path"] in ("/chat", "/chat/action") and resp["status_code"] == 200:
            bodies = [resp["json"]]
        for body in bodies:
            assert list(body) == CHAT_RESPONSE_FIELDS, fid
            assert set(body["graph"]) == {"nodes", "edges", "source_path"}, fid
            assert body["turn_id"] == case["request"]["json"].get("client_turn_id"), fid


def test_observed_status_catalogue():
    seen = set()
    for case in CASES.values():
        resp = case["response"]
        for event in resp.get("sse_events") or []:
            if event["event"] in ("complete", "error"):
                seen.add(event["data"]["status"])
        body = resp.get("json")
        if case["request"]["path"] in ("/chat", "/chat/action") and isinstance(body, dict) and "status" in body:
            seen.add(body["status"])
    assert seen == OBSERVED_CHAT_STATUSES
    # stream_error is synthesized by the route (api.py:6886/6977), not a service literal.
    assert seen - {"stream_error"} <= COMPANION_LITERAL_STATUSES


# docs/migration/companion/f2w_ui_contracts.json "literal_response_statuses" (static, 23 values),
# copied here so the suite runs from a bare baseline export.
COMPANION_LITERAL_STATUSES = {
    "agent_unavailable", "answered", "awaiting_download_decision", "awaiting_extraction_decision",
    "candidate_search_error", "debate_error", "direct_response", "download_error", "download_query_needed",
    "extraction_error", "insufficient_evidence", "insufficient_json_graph", "kg_rebuild_error", "max_rounds",
    "no_download_candidates", "no_new_papers", "no_pending_action", "no_unprocessed_pdfs",
    "orchestration_stopped", "retrieval_error", "splash_error", "stop_insufficient", "stopped_by_user",
}
COMPANION_LITERAL_PHASES = {
    "action_selected", "awaiting_download_decision", "awaiting_extraction_decision", "candidate_search_result",
    "candidate_search_started", "debate_result", "debate_started", "download_result", "download_started",
    "extraction_result", "extraction_started", "graph_update", "kg_rebuild_result", "kg_rebuild_started",
    "orchestrator_decision", "reload_result", "reload_started", "retrieval_result", "retrieval_started",
    "splash_reimport_result", "splash_reimport_started",
}


def test_observed_phases_are_companion_literals_plus_turn_started():
    seen = set()
    for case in CASES.values():
        for event in case["response"].get("sse_events") or []:
            if event["event"] == "progress":
                seen.add(event["data"]["phase"])
    assert "turn_started" in seen
    assert seen - {"turn_started"} <= COMPANION_LITERAL_PHASES
    # Phases never reached by the recorded flows (documented in ui_contracts/README.md).
    assert COMPANION_LITERAL_PHASES - seen == set()


# ------------------------------------------------------------------ SSE contract


@pytest.mark.parametrize("fid", SSE_IDS)
def test_sse_raw_bytes_match_fixture(replay, fid):
    expected = (FIX / CASES[fid]["response"]["sse_file"]).read_bytes()
    ctx = replay["ctx"]
    actual = H.sanitize_text(replay["raw"][fid].decode("utf-8"), ctx).encode("utf-8")
    assert actual == expected


@pytest.mark.parametrize("fid", SSE_IDS)
def test_sse_framing_and_event_order(fid):
    raw = (FIX / CASES[fid]["response"]["sse_file"]).read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n\n")
    events = H.parse_sse(raw)  # strict 'event: x\ndata: <one-line json>\n\n'
    names = [e["event"] for e in events]
    assert set(names) <= SSE_EVENTS
    first = events[0]
    request = CASES[fid]["request"]["json"]
    assert first["event"] == "progress"
    assert first["data"] == {"phase": "turn_started", "message": "Turn started",
                             "turn_id": request["client_turn_id"]}
    assert names[-1] in ("complete", "error")
    assert sum(1 for n in names if n in ("complete", "error")) == 1
    for event in events[:-1]:
        data = event["data"]
        assert list(data)[:3] == ["phase", "message", "turn_id"]
        assert data["turn_id"] == request["client_turn_id"]
        if data["phase"] == "graph_update":
            assert isinstance(data["node_ids"], list)
            assert set(data["graph"]) == {"nodes", "edges"}
    terminal = events[-1]
    if terminal["event"] == "error" and terminal["data"].get("status") == "stream_error":
        assert list(terminal["data"]) == ["status", "message", "turn_id"]
    elif terminal["event"] == "error":
        assert terminal["data"]["status"].endswith("_error")
    else:
        assert not terminal["data"]["status"].endswith("_error")


def _client_parse(chunks: List[bytes]) -> List[Dict[str, Any]]:
    """Python port of liveAgent.ts:448-462 + 488-509 (TextDecoder stream, split on LF-LF)."""
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    out: List[Dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        buffer += decoder.decode(chunk, final=last)
        blocks = buffer.split("\n\n")
        buffer = blocks.pop()
        for block in blocks:
            block = block.strip()
            event, data_lines = "message", []
            for line in re.split(r"\r?\n", block):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if data_lines:
                out.append({"event": event, "data": json.loads("\n".join(data_lines))})
    return out


@pytest.mark.parametrize("fid", SSE_IDS)
def test_sse_survives_client_fragmentation(fid):
    raw = (FIX / CASES[fid]["response"]["sse_file"]).read_bytes()
    whole = H.parse_sse(raw)
    for size in (1, 2, 3, 7, 64):
        chunks = [raw[i:i + size] for i in range(0, len(raw), size)]
        assert _client_parse(chunks) == whole, size


def test_utf8_multibyte_is_emitted_raw():
    raw = (FIX / CASES["chat_stream_answered"]["response"]["sse_file"]).read_bytes()
    assert "μ".encode("utf-8") in raw  # ensure_ascii=False (api.py:1661-1663)
    assert b"\\u03bc" not in raw


def test_expected_phase_sequences():
    def phases(fid):
        return [e["data"].get("phase") or f"<{e['event']}:{e['data']['status']}>"
                for e in CASES[fid]["response"]["sse_events"]]

    assert phases("chat_stream_direct_response") == [
        "turn_started", "orchestrator_decision", "<complete:direct_response>"]
    assert phases("chat_stream_answered") == [
        "turn_started", "orchestrator_decision", "retrieval_started", "retrieval_result", "graph_update",
        "<complete:answered>"]
    assert phases("chat_stream_insufficient_to_download") == [
        "turn_started", "orchestrator_decision", "retrieval_started", "retrieval_result",
        "orchestrator_decision", "candidate_search_started", "orchestrator_decision",
        "candidate_search_result", "debate_started", "debate_result", "action_selected",
        "orchestrator_decision", "awaiting_download_decision", "<complete:awaiting_download_decision>"]
    assert phases("chat_action_stream_error_missing_candidate") == [
        "turn_started", "<error:stream_error>"]
    assert phases("chat_stream_error_empty_json_selection")[-1] == "<error:stream_error>"
    assert phases("chat_stream_error_malformed_verdict") == [
        "turn_started", "orchestrator_decision", "retrieval_started", "<error:stream_error>"]
    assert phases("chat_stream_retrieval_error")[-1] == "<error:retrieval_error>"
    assert phases("chat_action_stream_download_yes") == [
        "turn_started", "orchestrator_decision", "download_started", "download_result",
        "orchestrator_decision", "awaiting_extraction_decision", "<complete:awaiting_extraction_decision>"]
    ex = phases("chat_action_stream_extraction_yes")
    assert ex[:2] == ["turn_started", "orchestrator_decision"]
    for phase in ("extraction_started", "extraction_result", "kg_rebuild_started", "kg_rebuild_result",
                  "splash_reimport_started", "splash_reimport_result", "reload_started", "reload_result",
                  "retrieval_started", "retrieval_result"):
        assert phase in ex
    assert ex[-1] == "<complete:answered>"
    assert phases("chat_action_stream_download_failed_retry")[-1] == "<complete:awaiting_download_decision>"
    assert phases("chat_action_stream_decline") == ["turn_started", "<complete:stopped_by_user>"]


def test_pending_envelopes():
    dl = CASES["chat_stream_insufficient_to_download"]["response"]["sse_events"][-1]["data"]["pending"]
    assert dl["kind"] == "download"
    assert [p["index"] for p in dl["papers"]] == [0, 1]
    assert [p["recommended"] for p in dl["papers"]] == [True, False]
    assert set(dl["papers"][0]) == {"title", "doi", "publication_year", "abstract", "score", "repository",
                                    "recommended", "index"}
    assert "approval_token" not in json.dumps(dl)
    ex = CASES["chat_action_stream_download_yes"]["response"]["sse_events"][-1]["data"]
    assert ex["pending"]["kind"] == "extraction"
    assert ex["pending"]["candidate"]["source_paper"] == "candidate_one.pdf"
    assert ex["answer"] == ""
    retry = CASES["chat_action_stream_download_failed_retry"]["response"]["sse_events"][-1]["data"]
    assert retry["pending"]["papers"][1]["unavailable"] is True


def test_side_effects_only_after_approval(replay):
    calls = replay["calls"]
    # Two insufficient asks + one failed retry ask; downloads only on explicit yes actions.
    assert calls.get("download.download_selected") == 2
    assert calls.get("extractor.extract_targeted") == 1
    assert calls.get("kg_update.rebuild_kg") == 1
    assert calls.get("kg_update.splash_reimport") == 1


# ------------------------------------------------------------------ browser persistence


def test_persistence_key_evidence():
    inventory = _load("persistence_keys.json")
    keys = [k["key"] for k in inventory["keys"]]
    assert keys == [
        "fair2wise-agent-settings-v1", "fair2wise-available-graphs-cache-v1", "fair2wise.chat.sessions.v2",
        "fair2wise.chat.messages.v1", "fair2wise-publication-favorites-v1", "fair2wise.citeFocusNodeId",
    ]
    for item in inventory["keys"]:
        file, line = item["declared_at"].rsplit(":", 1)
        lines = (REPO_ROOT / file).read_text(encoding="utf-8").splitlines()
        assert f"'{item['key']}'" in lines[int(line) - 1], item["declared_at"]
        api = item["storage"]
        for field, verb in (("read_at", "getItem"), ("written_at", "setItem"), ("removed_at", "removeItem")):
            for ref in item.get(field, []):
                f, span = ref.rsplit(":", 1)
                start, _, end = span.partition("-")
                text = "\n".join((REPO_ROOT / f).read_text(encoding="utf-8").splitlines()[
                    int(start) - 1:int(end or start)])
                assert f"window.{api}.{verb}" in text, ref


def test_no_unlisted_browser_storage_in_ui_source():
    listed = {k["key"] for k in _load("persistence_keys.json")["keys"]}
    src = REPO_ROOT / "ui" / "src"
    found = set()
    for path in src.rglob("*.ts*"):
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        text = path.read_text(encoding="utf-8")
        assert "indexedDB" not in text, path
        for match in re.finditer(r"(?:const|export const) \w*(?:STORAGE_KEY|CACHE_KEY)\w* = '([^']+)'", text):
            found.add(match.group(1))
    assert found == listed


def test_graph_source_wire_tokens_in_ui_source():
    text = (REPO_ROOT / "ui/src/app/components/agentSettings.ts").read_text(encoding="utf-8").splitlines()
    assert text[1] == "export type AgentGraphSource = 'splash_links' | 'json';"
    assert text[220] == "  return source === 'json' ? 'json' : 'splash';"
    assert text[224] == "  return source === 'json' ? 'json' : 'splash_links';"
    # T18D: the Edit gate moved into canEditNode (json source never edits; a server `can_save: false` hides it too),
    # so pin it by content, not by line number.
    gm = (REPO_ROOT / "ui/src/app/components/GraphMockup.tsx").read_text(encoding="utf-8")
    assert "  const canEdit = canEditNode(loadAgentSettings().graphSource, capabilities);" in gm.splitlines()
    caps = (REPO_ROOT / "ui/src/app/components/capabilitiesApi.ts").read_text(encoding="utf-8")
    assert "graphSource !== 'json' && (caps?.can_save ?? true)" in caps
