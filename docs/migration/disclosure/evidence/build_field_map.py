"""Build docs/migration/disclosure/route_field_map.json for T21A.

All file:line references are against the frozen baseline export 222b1e7.
Run: python -I build_field_map.py <repo_root>
"""
import json
import sys
from collections import Counter
from pathlib import Path

ORDER = ["public-safe", "quote-bounded", "internal-only", "source-serving"]
API = "app/modules/f2w_agent/api.py"

# ---------------------------------------------------------------- shared DTO field maps
F = {}  # key -> field record


def fld(key, field, cls, returns, ref, bound, recon, source_derived=True, conforms="yes", notes=""):
    F[key] = {
        "field": field,
        "class": cls,
        "source_derived": source_derived,
        "currently_returns": returns,
        "source_ref": ref,
        "size_bound": bound,
        "reconstruction_risk": recon,
        "conforms_to_D19_today": conforms,
        "notes": notes,
    }
    return key


GRAPH_NODE = [
    fld("GN.id_label_type", "GraphNode.id/label/type/graph_id/graph_label", "public-safe",
        "Node id, term name, category, graph id/label.", f"{API}:167-193,1056-1064,1084-1090",
        "none (identifiers)", "none"),
    fld("GN.description", "GraphNode.description", "quote-bounded",
        "MatKG: raw description|definition|code_description (LLM-written term definition). Tiled: title+description+identity props joined (tiled_graph.py:645-656).",
        f"{API}:1065-1069; app/modules/json2kg.py:332", "none in code; tracked KGs max 530 chars (measured)",
        "low per node; whole-graph enumeration via GET /graph returns every description. Verbatim-ness of definitions not verified.",
        conforms="unverified", notes="Rendered as the citation popover snippet (ui/src/app/components/kgCitations.ts:548)."),
    fld("GN.publications", "GraphNode.publications[] (_PUBLICATION_FIELDS incl. abstract_text, pages)", "quote-bounded",
        "Bibliographic records: source_paper filename, title, authors, DOI, journal, pages, keywords, abstract_text.",
        f"{API}:822-835,878-993", "none on abstract_text in this path (candidate path truncates, see CR.publications)",
        "none for paper body; abstract is publisher text shown whole", conforms="yes",
        notes="Tracked KGs contain 0 abstract_text values (measured); OpenAlex abstracts enter via download candidates."),
    fld("GN.source_papers", "GraphNode.source_papers", "public-safe",
        "PDF filenames / DOIs (e.g. DOI-derived filenames, local names XRAY1). Not URLs, not paths.",
        f"{API}:640-654", "list, unbounded count", "none"),
    fld("GN.code_snippet", "GraphNode.code_snippet and linked_code_snippets[].code_snippet", "quote-bounded",
        "Verbatim code text of CodeSnippet nodes (extracted from paper pages or source repos). Only when include_code=True: graph_node_from_file and graph_subset_from_file.",
        f"{API}:1073-1076,1031-1044,1150-1152,1418-1423", "none in code; tracked KGs: 30 code nodes, max 859 chars (measured)",
        "low (short listings); code transcribed from a paper is exact source text", conforms="yes",
        notes="Code from repos carries repo/commit/line provenance; code from papers carries paper+page only."),
    fld("GN.properties", "GraphNode.properties", "quote-bounded",
        "MatKG property list passthrough, or the entire Tiled/Splash entity properties dict except haystack/outgoingLinks/relations.",
        f"{API}:657-676; app/modules/tiled_graph.py:639,666", "none; tracked KGs max 3633 chars per property entry (measured)",
        "CONDITIONAL HIGH: the dict path forwards every stored property. Splash import stores all non-core thing fields as properties (splash_links/scripts/import_kg.py:42-58), so context_snippets would pass through for any Tiled/Splash node imported from a MatKG JSON.",
        conforms="conditional",
        notes="Live Tiled holds experiment identity entities today (tiled_graph.py:1-19,44), so no paper text observed on this path at baseline."),
    fld("GN.extra_fields", "GraphNode.extra_fields", "quote-bounded",
        "All non-core raw keys: strings cut at 800 chars + ellipsis, lists <=24 items, dicts <=16 keys (inner values unbounded), plus identity keys.",
        f"{API}:705-746", "800 chars per string; nested values unbounded",
        "low; context_snippets is a core key and excluded (api.py:598). Tracked KGs only carry raw_category here (measured).",
        notes="Any new passthrough slot (json2kg.py:61-73 copies unknown term keys) lands here automatically."),
    fld("GN.repo_provenance", "GraphNode.repo_url/repo_owner/repo_name/repo_commit_sha/source_file_path/source_file_url/source_start_line/source_end_line/repository_license/formula",
        "public-safe", "Code repository provenance and formula.", f"{API}:749-761,1082", "none", "none",
        notes="source_file_path is a repository-relative path, not a local storage path."),
]
GRAPH_EDGE = [
    fld("GE.edge", "GraphEdge.source/target/predicate", "public-safe", "Edge endpoints and predicate only.",
        f"{API}:196-200,1366-1378,1436-1442", "none", "none",
        notes="Edge has_evidence (json2kg.py:405-411) is NOT projected; it stays internal. Tracked KGs carry 0 has_evidence values (measured)."),
]
GRAPH_PAYLOAD = GRAPH_NODE + GRAPH_EDGE + [
    fld("GP.source_path", "GraphPayload.source_path", "internal-only",
        "Absolute filesystem path of the graph JSON (or tiled://graphql).", f"{API}:1381,2876,1512",
        "one string", "none", source_derived=False, conforms="no", notes="CONTENT_ACCESS_POLICY s1: no storage paths in user output."),
]

CHAT_RESPONSE = [
    fld("CR.status_flags", "ChatResponse.status/sufficient/confidence/node_ids/graph_source_requested/graph_source_used/turn_id",
        "public-safe", "Status literal, flags, node ids, turn id.", f"{API}:210-225,6493-6508", "none", "none",
        notes="turn_id doubles as the only key needed to fetch the trace (GET /traces/{turn_id})."),
    fld("CR.answer", "ChatResponse.answer", "quote-bounded",
        "LLM answer grounded in KG context, optional hybrid literature/ops chunks (source_rag) and, for active-paper questions, eligible PDF page text. Citations [KG:..]/[PDF: f p.N]/[OPS:..].",
        f"{API}:6475,6495; app/modules/f2w_agent/paper_evidence_agent.py:366-448; app/modules/hybrid_rag/context.py:36-145",
        "none (model output length)",
        "MEDIUM [inferred]: no exact-quote verification and no output constraint; repeated prompts can ask the model to repeat supplied context. Only citation format/page eligibility is validated (paper_evidence_agent.py:83-120).",
        conforms="partial", notes="Legacy snippet/citation behaviour to preserve (QA-01)."),
    fld("CR.publications", "ChatResponse.publications[]", "quote-bounded",
        "Selected-node publications (as GN.publications) or candidate publication with abstract_text cut to 1600 chars.",
        f"{API}:6488-6492,6551-6559,2438-2477", "abstract_text 1600 chars on candidate path; unbounded on KG path", "none"),
    fld("CR.rounds", "ChatResponse.rounds[]", "internal-only",
        "Raw per-round internal state: retrieval verdict (answer, selected_hits id/label/score, hybrid_rag counts, live_tiled/materials_project meta, error str(exc)), download result, pending_pdfs names, extraction result (output_file path, page counts), kg rebuild, splash reimport, reload, candidate_search with full candidate dicts (OpenAlex abstract untruncated), debate summary, paper_evidence result, extraction_report.",
        f"{API}:4810-4815,4892-5016,5321,5388,5534-5702,6344,6368; app/modules/f2w_agent/retrieval_agent.py:1244-1400; app/modules/f2w_agent/download_agent.py:212",
        "none", "low for paper body (no chunk text observed in verdicts: retrieval_agent.py:1244-1252 keeps counts only); abstracts and raw error strings are forwarded whole",
        source_derived=True, conforms="no",
        notes="Forwards internal job/tool state to the browser and SSE; policy s1 row 'Job/checkpoint/tool/trace payloads' requires sanitizing."),
    fld("CR.graph", "ChatResponse.graph (GraphPayload)", "quote-bounded", "Query subgraph via _query_graph_payload.",
        f"{API}:6486,2896-2907", "kg_query_max_nodes (10..1000, api.py:424)", "see GraphNode fields"),
    fld("CR.workdir", "ChatResponse.workdir", "internal-only", "Session working directory path.", f"{API}:222,6504",
        "one string", "none", source_derived=False, conforms="no"),
    fld("CR.pending", "ChatResponse.pending", "quote-bounded",
        "Pending download/extraction card: title, DOI, year, abstract cut to 600 chars, score, repository, downloaded source_paper filename.",
        f"{API}:6511-6531,6581-6600", "abstract 600 chars", "none",
        notes="Rendered by ui/src/app/components/ChatSidebar.tsx:53-55."),
    fld("CR.orchestration", "ChatResponse.orchestration", "public-safe",
        "Classifier/orchestrator decision summary (action/agent/reason).", f"{API}:224,2576,4007,6506",
        "none", "none [inferred: reason is model text about the question, not source text]"),
]

SSE = [
    fld("SSE.progress", "SSE event 'progress' {phase,message,turn_id,**data}", "quote-bounded",
        "Progress phases; graph_update carries graph={nodes,edges} built from GraphNode dicts with include_code=True; candidate titles; extraction counts; missing_topics.",
        f"{API}:6848-6858,3886-3901,4837,5141,6388,1418-1424", "per event none", "low (GraphNode content only)"),
    fld("SSE.complete", "SSE event 'complete'", "internal-only", "Full ChatResponse JSON (includes rounds and workdir).",
        f"{API}:6876-6881,6967-6972", "none", "see ChatResponse fields", conforms="no"),
    fld("SSE.error", "SSE event 'error'", "internal-only",
        "Either ChatResponse with *_error status or {status:'stream_error', message:str(exc)}.",
        f"{API}:6878-6890,6969-6981", "none", "low; raw exception text may include paths or provider messages", source_derived=False, conforms="no"),
]

TRACE = [
    fld("TR.merged_context", "trace.retrieval.merged_context", "source-serving",
        "Exact prompt context: KG context plus hybrid literature/ops paper chunks (1200-char chunk text, packed into a 6000-char source budget) and Materials Project context.",
        "app/modules/f2w_agent/retrieval_agent.py:1120-1130,1279-1291,1440; app/modules/hybrid_rag/context.py:9,120-145; app/modules/f2w_agent/trace.py:103",
        "hybrid source budget 6000 chars + KG context budget", "HIGH: verbatim paper chunks; repeated questions enumerate different chunks",
        conforms="no"),
    fld("TR.llm_messages", "trace.llm_calls[].messages", "source-serving",
        "Every rendered prompt: retrieval judge/leeway prompts with merged context; paper-evidence prompts with eligible PDF page text (up to 8 pages x 7000 chars in full mode; every selected page x 7000 chars in targeted mode, where selection follows targeted_max_pages 1..100) and a repair prompt that repeats the same pages.",
        "app/modules/f2w_agent/trace.py:460-494; app/modules/kg_rag_api.py:1590-1637; app/modules/term_extractor/clients.py:16-45; app/modules/f2w_agent/paper_evidence_agent.py:16-17,336-339,358-377,402-410; app/modules/f2w_agent/api.py:417",
        "no trace-side bound; prompt-side 7000 chars/page", "HIGH: one paper-evidence turn can carry most of a paper's text; a few turns can carry all of it",
        conforms="no",
        notes="Extraction prompts are probably not captured: ExtractorAgent runs in run_in_executor without bind_context (extractor_agent.py:106-107) [inferred; confirm with a runtime trace]."),
    fld("TR.llm_response", "trace.llm_calls[].response, retrieval.raw_judge_output, retrieval.parsed_verdict, orchestration.raw_classifier_output",
        "quote-bounded", "Raw model outputs before post-processing.", "app/modules/f2w_agent/trace.py:408-412,481-489; retrieval_agent.py:1356-1358",
        "none", "MEDIUM: raw outputs may repeat context verbatim", conforms="partial"),
    fld("TR.request", "trace.request.message/effective_question/history/ui_settings", "public-safe",
        "The caller's own messages and settings.", "app/modules/f2w_agent/trace.py:72-81,386-396", "none", "none",
        conforms="yes", notes="Readable by any caller who knows the turn id: cross-user exposure on a shared deployment."),
    fld("TR.env", "trace.env.settings/graphs/git_sha", "internal-only",
        "F2W_*/KG_RAG_* environment values with secret-named keys redacted; graph file paths and SHA-256.",
        "app/modules/f2w_agent/trace.py:206-275", "none", "none", source_derived=False, conforms="no",
        notes="Redaction is name/value based only (trace.py:44-47,206-243)."),
    fld("TR.errors", "trace.errors[]", "internal-only", "Exception type/message/where.", "app/modules/f2w_agent/trace.py:496-503",
        "none", "low", source_derived=False, conforms="no"),
    fld("TR.response", "trace.response.*", "quote-bounded", "Answer, parsed citations, publications, pending, status.",
        "app/modules/f2w_agent/trace.py:505-525", "none", "as ChatResponse.answer"),
]

SETTINGS = [
    fld("ST.config", "AgentSettingsResponse.backend/model/graph_source/workflow_mode/extraction_mode/targeted_max_pages/kg_query_*/source_rag/use_live_tiled/available_cborg_models/default_ollama_model/selected_graph_ids/tiled_status/tiled_api_key_set",
        "public-safe", "Runtime configuration values and booleans.", f"{API}:383-407,2669-2711", "none", "none", source_derived=False),
    fld("ST.paths", "AgentSettingsResponse.json_graph_path/json_graph_paths/available_json_graphs/available_graphs[].path/tiled_uri",
        "internal-only", "Graph file paths under storage/kg and uploads; configured Tiled URI.", f"{API}:391-403,465-476",
        "none", "none", source_derived=False, conforms="no"),
    fld("ST.tiled_error", "AgentSettingsResponse.tiled_error", "internal-only", "Probe/lookup error text with the Tiled key value redacted.",
        "app/modules/tiled_graph.py:714-728; api.py:2713-2724", "none", "none", source_derived=False, conforms="no"),
]

HTTP_ERR = fld("ERR.detail", "HTTPException.detail = str(exc)", "internal-only",
               "Raw exception text (FileNotFoundError/ValueError/any Exception) returned as 400/404/502 detail.",
               f"{API}:6654-6655,6667-6668,6682-6683,6755-6760,6767-6768,6786-6787,6802-6803,6814-6815",
               "none", "low; may include local paths, Splash/Tiled responses", source_derived=False, conforms="no")

# ---------------------------------------------------------------- routes
R = []


def route(rid, method, path, file, line, handler, fields, extra=None, serves_pdf=False, serves_fulltext=False,
          effects="read", notes="", cases=None, local_fields=None):
    rec = {
        "route_id": rid, "method": method, "path": path, "file": file, "line": line, "handler": handler,
        "effects": effects, "fields": fields, "route_specific_fields": local_fields or [],
        "serves_pdf": serves_pdf, "serves_full_text": serves_fulltext,
        "notes": notes, "binary_cases": cases or [],
    }
    if extra:
        rec.update(extra)
    R.append(rec)


def L(field, cls, returns, ref, bound, recon, source_derived=True, conforms="yes", notes=""):
    return {"field": field, "class": cls, "source_derived": source_derived, "currently_returns": returns,
            "source_ref": ref, "size_bound": bound, "reconstruction_risk": recon,
            "conforms_to_D19_today": conforms, "notes": notes}


route("R01", "GET", "/health", API, 6625, "create_app.health", [], effects="read",
      local_fields=[
          L("status/kg_mode/backend/model/workflow_mode/extraction_mode/targeted_max_pages/session_memory_has_context/workflow_phase/pending_approval/max_rounds",
            "public-safe", "Process status and runtime settings.", f"{API}:6627-6642", "none", "none", source_derived=False),
          L("workdir/session_memory/workflow_state", "internal-only", "Absolute paths of workdir, session memory and workflow state files.",
            f"{API}:6629,6636,6638", "none", "none", source_derived=False, conforms="no",
            notes="ALS exposes health to not-signed-in callers; needs a reduced non-sensitive projection."),
      ], cases=["AUTH-01", "DEP-10"])
route("R02", "GET", "/settings", API, 6645, "create_app.get_settings", ["ST.config", "ST.paths", "ST.tiled_error"],
      effects="read; outbound Tiled probe that sends the process TILED_API_KEY to runtime.tiled_uri (tiled_graph.py:225-247,183-190)",
      cases=["LOCAL-06", "AUTH-06"])
route("R03", "PUT", "/settings", API, 6650, "create_app.update_settings", ["ST.config", "ST.paths", "ST.tiled_error", "ERR.detail"],
      effects="process-global mutation of model/graph/source_rag/live Tiled/tiled_uri (api.py:2726-2860); tiled_uri accepts any http(s) host except *.als.lbl.gov (tiled_graph.py:161-175)",
      notes="Not a source-disclosure leak, but a request can redirect the backend's Tiled key to an arbitrary host; see current_state.md.",
      cases=["LOCAL-07", "AUTH-08", "LLM-01"])
route("R04", "POST", "/session/reset", API, 6658, "create_app.reset_session", [],
      effects="clears session memory/workflow (api.py:4230-4245)",
      local_fields=[
          L("status/session_memory_has_context/workflow_state/workflow_phase", "public-safe", "Reset result.", f"{API}:364-370", "none", "none", source_derived=False),
          L("session_memory", "internal-only", "Session memory file path.", f"{API}:367", "none", "none", source_derived=False, conforms="no"),
      ])
route("R05", "DELETE", "/session/{session_id}", API, 6663, "create_app.delete_session", ["ERR.detail"],
      effects="deletes any named session; session ids are caller-chosen (api.py:128-133,4246-4258)",
      local_fields=[L("status/session_id", "public-safe", "{'status':'deleted','session_id':key}", f"{API}:4258", "none", "none", source_derived=False)])
route("R06", "GET", "/graph", API, 6671, "create_app.graph",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "GE.edge", "GP.source_path"],
      notes="Whole active graph in one response; code_snippet omitted (include_code False at api.py:1362). Full-graph enumeration of descriptions/properties; no context_snippets.",
      cases=["SEC-05", "QUOTE-01"])
route("R07", "POST", "/graph/nodes/search", API, 6676, "create_app.search_graph_nodes",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.code_snippet", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "ERR.detail"],
      notes="limit 1..25 (api.py:301); nodes from graph_node_from_file include code (api.py:2930).", cases=["SEC-05"])
route("R08", "GET", "/graph/neighborhood/{node_id:path}", API, 6686, "create_app.graph_neighborhood",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "GE.edge", "GP.source_path"],
      notes="Tiled lookup graph only (api.py:2971-3047, 1447-1513); 'hops' query param is accepted but CITE_FOCUS_HOPS is used. Properties = full Tiled entity props.",
      cases=["SEC-05"])
route("R09", "GET", "/graph/node/{node_id}", API, 6716, "create_app.graph_node",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.code_snippet", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "ERR.detail"],
      notes="Node detail = the per-node evidence surface. Optional json_graph_path query param is confined to storage/kg or workdir/uploads (api.py:3396-3412). Arbitrary node ids may be enumerated; content is the GraphNode projection only.",
      cases=["QUOTE-01", "QUOTE-02", "SEC-05"])
route("R10", "PATCH", "/graph/node/{node_id}", API, 6751, "create_app.patch_graph_node",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.code_snippet", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "ERR.detail"],
      effects="writes Splash-links entity via unauthenticated GraphQL and the session graph JSON (api.py:2978-3046; kg_update.py:57-77,270-299)",
      notes="Caller-supplied description/code/publications become graph content; no exactness check on edited quotes.",
      cases=["TIL-05", "AUTH-04", "JSON-10"])
route("R11", "POST", "/graph/upload", API, 6763, "create_app.upload_graph",
      ["GN.id_label_type", "GN.description", "GN.publications", "GN.source_papers", "GN.properties", "GN.extra_fields", "GN.repo_provenance", "GE.edge", "GP.source_path", "ERR.detail"],
      effects="writes arbitrary caller JSON to workdir/uploads/<name>.json with no size limit (api.py:3374-3394)",
      local_fields=[L("graph_path", "internal-only", "Absolute path of the written upload.", f"{API}:294,3394", "none", "none", source_derived=False, conforms="no")],
      notes="Echoes the caller's own upload through the GraphNode projection.", cases=["AUTH-04"])
route("R12", "POST", "/chat", API, 6771, "create_app.chat",
      ["CR.status_flags", "CR.answer", "CR.publications", "CR.rounds", "CR.graph", "CR.workdir", "CR.pending", "CR.orchestration", "ERR.detail"],
      effects="model calls; may set pending download/extraction; writes trace when F2W_TRACE_ENABLED",
      cases=["QA-01", "QUOTE-01", "QUOTE-02", "SEC-04", "AUTH-01", "AUTH-02"])
route("R13", "POST", "/chat/action", API, 6790, "create_app.chat_action",
      ["CR.status_flags", "CR.answer", "CR.publications", "CR.rounds", "CR.graph", "CR.workdir", "CR.pending", "CR.orchestration", "ERR.detail"],
      effects="yes/no on pending: paper download, extraction, KG rebuild, Splash wipe+reimport, retrieval reload (api.py:5919-5971,5504-5702)",
      cases=["AUTH-03", "LOCAL-02", "SEC-04"])
route("R14", "POST", "/publications/search", API, 6806, "create_app.publication_search",
      ["GN.publications", "ERR.detail"], effects="read; optional outbound OpenAlex query (api.py:2416-2436)",
      local_fields=[L("status/query/matched_node_ids/source", "public-safe", "Search metadata.", f"{API}:355-361,3361-3367", "max_results 1..50", "none", source_derived=False)],
      notes="Returns bibliographic records only; abstract_text unbounded on the KG path.", cases=["SEC-05"])
route("R15", "POST", "/chat/stream", API, 6818, "create_app.chat_stream",
      ["SSE.progress", "SSE.complete", "SSE.error", "CR.status_flags", "CR.answer", "CR.publications", "CR.rounds", "CR.graph", "CR.workdir", "CR.pending", "CR.orchestration"],
      effects="as /chat", cases=["SEC-04", "QUOTE-01", "DEP-08"])
route("R16", "POST", "/chat/action/stream", API, 6911, "create_app.chat_action_stream",
      ["SSE.progress", "SSE.complete", "SSE.error", "CR.status_flags", "CR.answer", "CR.publications", "CR.rounds", "CR.graph", "CR.workdir", "CR.pending", "CR.orchestration"],
      effects="as /chat/action", cases=["SEC-04", "AUTH-03"])
route("R17", "GET", "/traces/{turn_id}", API, 7002, "create_app.get_trace",
      ["TR.merged_context", "TR.llm_messages", "TR.llm_response", "TR.request", "TR.env", "TR.errors", "TR.response"],
      serves_fulltext=True,
      effects="reads runs/traces/*/turns/<turn_id>.json (trace.py:778-787,813-823)",
      notes="Returns the unclipped detail record (docstring api.py:7003) to any caller that knows a turn id. Turn ids are uuid4 or caller-chosen (trace.py:346-350); the UI copies the URL to the clipboard (ChatSidebar.tsx:1043-1045, uiTelemetry.ts:98-103). Active whenever F2W_TRACE_ENABLED is on, which docker-compose.yml:43 sets while publishing 8090 on all interfaces (:51).",
      cases=["SEC-04", "SEC-01", "SEC-03"])
route("R18", "POST", "/telemetry/ui", API, 7010, "create_app.telemetry_ui", [],
      effects="appends browser events to runs/traces/<day>/ui_events.jsonl (trace.py:789-799)",
      local_fields=[L("status/written", "public-safe", "Write count.", f"{API}:7012-7016", "none", "none", source_derived=False)])
route("R19", "POST", "/annotations", API, 7019, "create_app.create_annotation", [],
      effects="appends pass/fail label to runs/traces/annotations.jsonl (trace.py:802-810)",
      local_fields=[L("status/turn_id/verdict", "public-safe", "Echo.", f"{API}:7029", "none", "none", source_derived=False)],
      notes="'annotator' is a free-text caller label, not an identity (api.py:281).")

KGRAG = "app/modules/kg_rag_api.py"
SKGRAG = "scripts/app/modules/kg_rag_api.py"
for base, f, lines in (("R20", KGRAG, (246, 262, 285, 294, 299, 314)), ("R26", SKGRAG, (221, 237, 260, 269, 274, 289))):
    n = int(base[1:])
    snip_ref = "app/modules/kg_rag_api.py:141-143,458-471,760-775,1242-1247" if f == KGRAG else "scripts/app/modules/kg_rag_api.py:117-118,428,1116"
    answer = L("message.content / choices[0].message.content", "quote-bounded",
               "LLM answer from RAG_SYSTEM prompt over KG context that includes, per source paper of each rendered node, a 1000-char window of the full PDF text centred on query terms ([PDF name] blocks).",
               f"{snip_ref}; {f}:{lines[0]}-{lines[1] + 20}", "context: CONTEXT_CHAR_BUDGET*0.75 = 12000 chars; snippet KG_RAG_SNIP=1000 (env-tunable); answer unbounded",
               "HIGH [inferred]: the caller chooses the hint terms that position the 1000-char window, so successive queries can move the window across a paper; no output constraint or exact-quote check.",
               conforms="no")
    err = L("error", "public-safe", "{'error':'No messages'}", f"{f}:{lines[0]}", "none", "none", source_derived=False)
    common_notes = "Independent model-compatible proxy (create_fastapi_app); run_fastapi binds 0.0.0.0:11435 with no auth and no CORS policy" + (" (kg_rag_api.py:321-330)." if f == KGRAG else " (scripts/app/modules/kg_rag_api.py:296-305). Duplicate of app/modules/kg_rag_api.py.")
    route(f"R{n:02d}", "POST", "/api/chat", f, lines[0], "create_fastapi_app.api_chat", [], local_fields=[answer, err],
          effects="model call; MissingNodeTracker log write", notes=common_notes, cases=["SEC-04", "QUOTE-02", "DEP-07"])
    route(f"R{n + 1:02d}", "POST", "/v1/chat/completions", f, lines[1], "create_fastapi_app.openai_chat_completions", [],
          local_fields=[answer, err], effects="model call", notes=common_notes, cases=["SEC-04", "QUOTE-02", "DEP-07"])
    for k, (p, h, ln) in enumerate((("/api/tags", "list_models", lines[2]), ("/api/version", "ollama_version", lines[3]),
                                     ("/v1/models", "openai_models", lines[4]), ("/api/ps", "list_processes", lines[5]))):
        route(f"R{n + 2 + k:02d}", "GET", p, f, ln, f"create_fastapi_app.{h}", [],
              local_fields=[L("static body", "public-safe", "Static model/version/process stub.", f"{f}:{ln}-{ln + 10}", "constant", "none", source_derived=False)],
              notes=common_notes, cases=["DEP-07"])

PF = "scripts/paper_finder.py"
route("R32", "GET", "/", PF, 601, "create_app.index", [],
      local_fields=[L("HTML page", "public-safe", "Harvest-queue paper title, journal, year, DOI/arXiv id, staging filenames, 'open' link target (publisher/arXiv URL).",
                      f"{PF}:492-555,601-616", "one paper", "none", conforms="yes")],
      notes="Operator tool; binds 127.0.0.1 only and refuses other hosts (paper_finder.py:61-82). Never fetches or serves the PDF (comment :636).", cases=["LOCAL-08"])
route("R33", "GET", "/detected", PF, 619, "create_app.detected", [],
      local_fields=[L("detected[]", "public-safe", "Local staging PDF filenames only.", f"{PF}:619-625", "list", "none", source_derived=False)])
route("R34", "POST", "/action", PF, 627, "create_app.action", [],
      effects="open in OS browser / mark restricted / skip / assign (move a staged PDF into papers/<corpus>/<year>/) / dismiss",
      local_fields=[L("302 redirect", "public-safe", "Redirect to index.", f"{PF}:627-702", "none", "none", source_derived=False)])

# ---------------------------------------------------------------- resolve and compute
inventory = json.loads((Path(sys.argv[1]) / "docs/migration/companion/f2w_http_routes.json").read_text())
assert len(R) == 34 == len(inventory), (len(R), len(inventory))
for rec, inv in zip(R, inventory):
    assert (rec["method"], rec["path"], rec["file"], rec["line"]) == (inv["method"], inv["path"], inv["file"], inv["line"]), (rec["route_id"], inv)

for rec in R:
    all_fields = [F[k] for k in rec["fields"]] + rec["route_specific_fields"]
    src = [f["class"] for f in all_fields if f["source_derived"]]
    allc = [f["class"] for f in all_fields]
    rec["route_class"] = max(src, key=ORDER.index) if src else "public-safe"
    rec["route_class_incl_non_source_fields"] = max(allc, key=ORDER.index) if allc else "public-safe"
    rec["exposes_internal_paths_or_raw_errors"] = any(not f["source_derived"] and f["class"] == "internal-only" for f in all_fields)
    rec["current_auth"] = "none" if rec["file"] != PF else "none (loopback-only bind enforced)"

field_counts = Counter(f["class"] for f in F.values())
route_specific = [f for r in R for f in r["route_specific_fields"]]
# de-duplicate the two kg_rag copies' shared local field definitions by (file, field)
field_counts_rs = Counter(f["class"] for f in route_specific)
route_counts = Counter(r["route_class"] for r in R)

out = {
    "schema_version": 1,
    "task": "T21A",
    "gate": "H1",
    "status": "proposed",
    "baseline_commit": "222b1e7b130567dbf4a3242697ac2b465794a38d",
    "line_basis": "All file:line values refer to the frozen 222b1e7 export. On forge_merge HEAD, app/modules/f2w_agent/api.py has +6 lines after line 140 (batch_id field) and further small shifts; route decorator lines move accordingly. Route 'line' is the handler def line, matching f2w_http_routes.json.",
    "policy_refs": ["D19", "docs/migration/companion/CONTENT_ACCESS_POLICY.md", "docs/migration/plan.md section 4.8", "docs/adr/content-disclosure.md"],
    "classes": {
        "public-safe": "No source text beyond identifiers and bibliographic metadata, or no source-derived content at all. May be returned to any caller the profile allows.",
        "quote-bounded": "May carry derived results or relevant bounded exact quotations with attribution. Allowed on the public surface only through the disclosure projection (exactness, locator, per-response and per-session bounds).",
        "internal-only": "Agent/job/trace state, local paths, raw errors or private evidence payloads. Never on the browser/API/SSE/FORGE surface; may be kept in protected stores.",
        "source-serving": "Returns raw source material (PDF bytes, page text, chunk text, full prompts containing them) to the caller. Not allowed on any user-facing surface under D19.",
    },
    "method": "Read baseline source; measured tracked storage/kg and storage/terminology JSON with stdlib json; checked 300 random context_snippets against local PDF page text (PyMuPDF from the baseline venv, whitespace-normalized substring match). No server was started and no network was used. Scripts: docs/migration/disclosure/evidence/ (build_field_map.py, snip_check.py, coverage.py).",
    "summary": {
        "routes": len(R),
        "routes_by_route_class": dict(route_counts),
        "shared_dto_fields_by_class": dict(field_counts),
        "route_specific_fields_by_class": dict(field_counts_rs),
        "routes_serving_pdf_bytes": [r["route_id"] for r in R if r["serves_pdf"]],
        "routes_serving_full_text": [r["route_id"] for r in R if r["serves_full_text"]],
        "routes_with_any_auth": [],
    },
    "dto_fields": F,
    "routes": R,
    "stores_and_exports": [
        {"id": "S01", "store": "storage/kg/*.json (47 files tracked in Git at 222b1e7)", "class": "internal-only",
         "content": "things[].context_snippets[{text,source_paper,page}]",
         "measured": {"files_parsed": 45, "snippets": 126977, "snippet_chars": 28342058, "max_snippet_chars": 1890,
                      "verbatim_sample": "300 of 300 random snippets whose PDF is in papers/ occur verbatim (whitespace-normalized) on the cited page",
                      "per_paper_coverage_of_normalized_text": {"papers": 90, "median": 0.307, "max": 0.478, "papers_over_25pct": 57, "papers_over_50pct": 0}},
         "source_ref": "app/modules/json2kg.py:334; app/modules/term_extractor/orchestrator.py:339-341; app/modules/extract_terms.py:1393,1465",
         "risk": "Cumulative reconstruction: the union of snippets reproduces about a third of each paper's text verbatim, in a public Git repository. Not served by any F2W route (GraphNode excludes context_snippets, api.py:598), but it is a graph export under D19/D31 and blocks commit-ready status (REL-04).",
         "binary_cases": ["REL-04", "SEC-05", "DEP-09"]},
        {"id": "S02", "store": "storage/terminology/*.json (58 files tracked)", "class": "internal-only",
         "content": "terms[].context_snippets",
         "measured": {"files_parsed": 56, "snippets": 141458, "snippet_chars": 31632709, "verbatim_sample": "not checked [inferred same producer as S01]"},
         "source_ref": "app/modules/term_extractor/models.py:112,134; store.py:164-169", "risk": "As S01.", "binary_cases": ["REL-04"]},
        {"id": "S03", "store": "papers/ (90 PDFs tracked at 222b1e7; untracked on forge_merge by f0c6ce3, still in history)", "class": "source-serving",
         "content": "Full publisher PDFs", "source_ref": "docs/migration/reviews/H0/license_dependency_audit.md E1",
         "risk": "Full papers are publicly retrievable from Git history. Outside the HTTP API; owner decision E1 pending.", "binary_cases": ["SEC-01", "DEP-09", "REL-04"]},
        {"id": "S04", "store": "storage/source_index/*/chunks.jsonl (gitignored, local)", "class": "internal-only",
         "content": "1200-char paper and ops text chunks with work_id/page/locator", "source_ref": "license_dependency_audit.md E4; app/modules/hybrid_rag/evidence.py:126-159",
         "risk": "Agent-private index; its chunks reach users only through prompts (trace R17) and answers.", "binary_cases": ["SEC-02", "DEP-09"]},
        {"id": "S05", "store": "runs/traces/<day>/turns/<turn_id>.json (F2W_TRACE_DIR)", "class": "source-serving",
         "content": "Unclipped per-turn detail incl. prompts with page/chunk text", "source_ref": "app/modules/f2w_agent/trace.py:1-6,612-627",
         "risk": "At rest it is a protected evaluation store; it becomes source-serving because R17 returns it.", "binary_cases": ["SEC-04"]},
        {"id": "S06", "store": "Splash-links entities (splash_links/scripts/import_kg.py; GraphQL :8081)", "class": "internal-only",
         "content": "Entity properties = every non-core MatKG thing field, including context_snippets; link properties include has_evidence",
         "source_ref": "splash_links/scripts/import_kg.py:42-58,156-163; app/modules/f2w_agent/kg_update.py:57-77",
         "risk": "Unauthenticated GraphQL store holding the S01 snippets; the GN.properties dict path would forward them for Splash/Tiled-sourced nodes.", "binary_cases": ["SEC-05", "TIL-03"]},
        {"id": "S07", "store": "Live Tiled graph (local :8765, allow_anonymous_access: true)", "class": "internal-only",
         "content": "Experiment identity entities today; literature KG after T08B import",
         "source_ref": "storage/tiled_config.yml:14-16; app/modules/tiled_graph.py:1-19,44",
         "risk": "Anonymous read today; a literature import that copies properties wholesale would publish snippets to any anonymous reader.", "binary_cases": ["SEC-05", "TIL-03"]},
    ],
    "http_surfaces_outside_inventory": [
        {"surface": "term_extractor agent dashboard (Flask)", "routes": ["GET /", "GET /events (SSE of agent logs/prompts)", "GET /assets/<path>", "POST /dismiss/<id>", "POST /respond/<id>", "POST /shutdown/<agent_id>"],
         "source_ref": "app/modules/term_extractor/dashboard.py:1080-1106,1273-1323; user_agent.py:23-27",
         "bind": "0.0.0.0:8000 default, no auth", "reachability": "Only through academy_agent.py UserAgent; no launcher reaches it from the API or images [inferred dormant]",
         "class": "internal-only", "note": "Log stream content not audited; treat as internal."},
        {"surface": "splash-links GraphQL", "routes": ["POST /splash_links/graphql"], "source_ref": "app/modules/f2w_agent/kg_update.py:57-77", "bind": "localhost:8081 default", "class": "internal-only"},
        {"surface": "local Tiled server", "routes": ["/api/v1/*", "/api/graphql"], "source_ref": "storage/tiled_config.yml:1-22; docker-compose.yml:79-98", "bind": "127.0.0.1:8765 (compose publishes loopback)", "class": "internal-only"},
        {"surface": "nginx UI container", "routes": ["/healthz", "/api/* -> agent:8090/*", "/ static UI"], "source_ref": "docker/nginx.conf:8-28", "class": "public-safe", "note": "Proxies every F2W API route including /traces."},
    ],
    "browser_rendering": [
        {"what": "Answer text with [KG]/[PDF]/[OPS] citation chips", "source_ref": "ui/src/app/components/kgCitations.ts:5-160", "class": "quote-bounded"},
        {"what": "Citation popover snippet = node.description or publication abstract_text", "source_ref": "ui/src/app/components/kgCitations.ts:548", "class": "quote-bounded"},
        {"what": "Pending paper card abstract", "source_ref": "ui/src/app/components/ChatSidebar.tsx:53-55", "class": "quote-bounded"},
        {"what": "Publication links: doi.org / arXiv / Semantic Scholar search; no local PDF link", "source_ref": "ui/src/app/components/publicationLinks.ts:60-125", "class": "public-safe"},
        {"what": "Copy trace link (absolute /traces/<turn_id> URL)", "source_ref": "ui/src/app/components/uiTelemetry.ts:98-103; ChatSidebar.tsx:1043-1045", "class": "source-serving (target R17)"},
        {"what": "Graph node card: description, publications, code snippets, properties, extra fields; edit form", "source_ref": "ui/src/app/components/nodeCardDetails.ts:150-200; GraphMockup.tsx:447-1070", "class": "quote-bounded"},
    ],
    "findings": [
        {"id": "F1", "severity": "high", "summary": "GET /traces/{turn_id} returns full prompts containing PDF page text and paper chunks to any caller who has a turn id; no auth.", "refs": ["R17", "TR.llm_messages", "TR.merged_context"]},
        {"id": "F2", "severity": "high", "summary": "Tracked KG/terminology JSON holds ~127k/141k verbatim context snippets; their union reproduces a median 31% (max 48%) of each paper's text, in public Git.", "refs": ["S01", "S02"]},
        {"id": "F3", "severity": "high", "summary": "Full publisher PDFs are in public Git history (H0 E1).", "refs": ["S03"]},
        {"id": "F4", "severity": "medium", "summary": "Model-compatible proxy injects query-positioned 1000-char PDF text windows into prompts and returns unconstrained model output; binds 0.0.0.0 without auth.", "refs": ["R20", "R21", "R26", "R27"]},
        {"id": "F5", "severity": "medium", "summary": "ChatResponse.rounds and SSE complete/error forward raw internal state, untruncated OpenAlex abstracts, local paths and str(exc).", "refs": ["CR.rounds", "SSE.complete", "SSE.error", "ERR.detail"]},
        {"id": "F6", "severity": "medium", "summary": "GraphNode.properties forwards every stored property for dict-shaped (Tiled/Splash) nodes; a literature import that keeps context_snippets in properties would serve them.", "refs": ["GN.properties", "S06", "S07"]},
        {"id": "F7", "severity": "low", "summary": "No route serves PDF bytes. No route returns a parsed section or crop. Paper Finder never fetches PDFs.", "refs": ["R32", "R33", "R34"]},
        {"id": "F8", "severity": "low", "summary": "No exact-quote verification exists anywhere: answers, graph descriptions and code snippets are labelled by citation only.", "refs": ["CR.answer", "GN.description", "GN.code_snippet"]},
    ],
}
dest = Path(sys.argv[1]) / "docs/migration/disclosure/route_field_map.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(out["summary"], indent=1))
