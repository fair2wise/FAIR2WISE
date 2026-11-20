"""The built-in function-trace list, applied when tracing is on and ``F2W_OTEL_TRACE_FUNCTIONS`` is unset.

Each entry is ``module:qualname`` (``module:*`` would mean every public module-level function). The list
follows the chat turn and the jobs a turn or an operator starts. Spans nest through the active context, so a
chat request shows as ``POST /chat`` -> ``AgentPipelineService.ask`` -> retrieval -> model calls.

Model calls are not on this list: every call already passes ``trace.record_llm_call``, which emits one
``llm.call <label>`` span (model name, label, latency, sizes, status; never the text) for all call sites
(judge, leeway, general answer, planner, extraction). ``safe_fetch`` and the other ``httpx`` callers get an
``HTTP <method>`` span from the client hook (host, port, status; never the query string).

``tests/tracing`` imports every target, so a rename cannot leave a dead entry behind.
"""

from __future__ import annotations

_API = "app.modules.f2w_agent.api"
_RETRIEVAL = "app.modules.f2w_agent.retrieval_agent"
_PUBLISH = "app.modules.library.publish"
_JOB_PUBLICATION = "app.modules.jobs.publication"

#: Chat turn stages: the turn, pending-message handling, retrieval, judge/leeway/general answers, off-domain gate.
CHAT_TURN = (
    f"{_API}:AgentPipelineService.ask",
    f"{_API}:AgentPipelineService.ask_with_progress",
    f"{_API}:AgentPipelineService._handle_pending_message",
    f"{_API}:AgentPipelineService._judge_agent_requirement",
    f"{_API}:AgentPipelineService._generate_general_answer",
    f"{_API}:AgentPipelineService._generate_json_graph_leeway_answer",
    f"{_RETRIEVAL}:RetrievalAgent.query",
    f"{_RETRIEVAL}:RetrievalAgent._ensure_graphs",
    f"{_RETRIEVAL}:RetrievalAgent._fanout_hits",
    f"{_RETRIEVAL}:RetrievalAgent._intent_routed_retrieve",
    f"{_RETRIEVAL}:RetrievalAgent._parallel_retrieve",
    f"{_RETRIEVAL}:RetrievalAgent._synthesize_leeway_answer",
    f"{_RETRIEVAL}:RetrievalAgent.search_node_scores",
    f"{_RETRIEVAL}:_off_domain",
    "app.modules.cborg_http:call_with_deadline",
)

#: Knowledge-graph load in the chat corpus union, and the Tiled read/write paths.
KG_AND_TILED = (
    "app.modules.f2w_agent.corpus_union:ChatCorpusUnion.refresh",
    "app.modules.f2w_agent.corpus_union:ChatCorpusUnion._entry",
    "app.modules.f2w_agent.corpus_union:ChatCorpusUnion._build",
    "app.modules.graph.backends.tiled_client:TiledGraphClient.query",
    "app.modules.graph.backends.tiled_mutations:TiledMutationClient.mutate",
    "app.modules.graph.backends.tiled_mutations:TiledMutationClient.scan_entities",
    "app.modules.graph.backends.tiled_mutations:TiledMutationClient.scan_links",
    "app.modules.graph.backends.tiled:TiledGraphBackend.get_entity",
    "app.modules.graph.backends.tiled:TiledGraphBackend.find_entities",
    "app.modules.graph.backends.tiled:TiledGraphBackend.neighborhood",
    "app.modules.graph.backends.tiled:TiledGraphBackend.apply_plan",
    "app.modules.graph.backends.tiled:TiledGraphBackend.list_complete",
    "app.modules.graph.legacy_save:TiledSaveBackend.prepare",
    "app.modules.graph.legacy_save:TiledSaveBackend.save",
    "app.modules.graph.legacy_save:TiledSaveBackend.recover",
)

#: Library publish and the publication job stages (export/adopt/stage/plan, review, import/apply, verify, activate).
PUBLICATION = (
    f"{_PUBLISH}:LibraryPublisher.preview",
    f"{_PUBLISH}:LibraryPublisher.submit",
    f"{_PUBLISH}:LibraryPublisher._export",
    f"{_PUBLISH}:LibraryPublisher._adopt",
    f"{_PUBLISH}:LibraryPublisher._stage",
    f"{_PUBLISH}:LibraryPublisher._plan",
    f"{_JOB_PUBLICATION}:Publication.submit",
    f"{_JOB_PUBLICATION}:Publication._plan",
    f"{_JOB_PUBLICATION}:Publication._review",
    f"{_JOB_PUBLICATION}:Publication._revalidate",
    f"{_JOB_PUBLICATION}:Publication._import",
    f"{_JOB_PUBLICATION}:Publication._verify",
    f"{_JOB_PUBLICATION}:Publication._receipt",
    f"{_JOB_PUBLICATION}:Publication._activate",
    f"{_JOB_PUBLICATION}:Publication._op_plan",
    f"{_JOB_PUBLICATION}:Publication._op_apply",
    f"{_JOB_PUBLICATION}:Publication._op_record",
)

#: Extraction pipeline stages and the one SSRF-guarded fetcher.
EXTRACTION_AND_FETCH = (
    "app.modules.library.extraction:LibraryExtractor.parse",
    "app.modules.library.extraction:LibraryExtractor.run",
    "app.modules.library.extraction:LibraryExtractor._run_locked",
    "app.modules.literature.safe_fetch:SafeFetcher.fetch_pdf",
)

DEFAULT_TRACE_FUNCTIONS: tuple[str, ...] = CHAT_TURN + KG_AND_TILED + PUBLICATION + EXTRACTION_AND_FETCH
