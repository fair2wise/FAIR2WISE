# F2W baseline and donor crosswalk - Draft v5

## Draft v5 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V5.md; source observations themselves remain unchanged. O03 is the only pending owner policy, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.


F2W source: uploaded `FAIR2WISE-rsoxs.zip`; archive-recorded commit `222b1e7b130567dbf4a3242697ac2b465794a38d`. Source bytes and listed symbols are statically checked; this does not certify runtime behavior, a remote branch tip, or git-object ancestry. Line ranges refer to this archive.

## Findings that override earlier assumptions

- LangGraph extraction already exists in `term_extractor/agent.py`, and `term_extractor/tools.py` already exposes tools. The migration unifies architecture, not introduces tools/graphs for the first time.
- `hybrid_rag/` already includes document chunking, BM25, source indexes, ranking and ContextBuilder; source RAG and multi-KG are existing selectable behavior, not wholly future.
- Tiled lookup and identity/neighborhood support already exist; the first-release requirement is a complete literature graph adapter and migration of the remaining Splash write paths.
- `fuzzy_merge_term` in the inspected extraction tool prompts over all display names. The older shortlisting discussion is not evidence that this path already has ANN.
- `rsoxs_schema.yaml` currently declares RSoXSMeasurement as a subclass of ExperimentalTechnique and includes simulations in its description. Correcting that conflicts with some old target diagrams and is a separate owner-approved H3 semantic migration.
- F2W already handles ops-docs, Materials Project citations and live Tiled lookup. These are retained compatibility behavior unless the owner separately approves a carve-out; donor experimental analysis remains out of scope.
- The front end uses legacy Splash labels/enums, including a graph-edit permission UI. Deprecating the service requires explicit wire-compatibility and editing translation, not deleting the option and claiming parity.
- CI currently targets main, not forge_merge; existing Playwright browser tests mock the backend. Both gaps must be addressed before those tests can certify the migration.

## Read this with PORT_CATALOG.md

`Bxx` identifies an actual F2W baseline seam. Donor IDs point to the full FORGE source/symbol catalog. The proposed destinations below are design, not existing F2W paths. Existing tests are candidates to run under isolated state, not claimed passes.

## B01 - Actual API and unchanged browser contract

**Actual F2W file:** `app/modules/f2w_agent/api.py`

**Verified symbols:** `create_app` (L6611-L7031); `run_api` (L7034-L7038); `AgentPipelineService` (L2479-L6600); `ChatRequest` (L124-L145); `ChatResponse` (L210-L225); `ChatActionRequest` (L228-L254); `AgentSettingsResponse` (L383-L407); `AgentSettingsUpdate` (L410-L428).

**FORGE entries:** I-01, I-02, I-03, I-04.

**Proposed destination:** `app/api/main.py + app/api/compat/ + app/modules/application.py`

Keep the existing create_app(cfg, cors_origins=...) and launcher callable as facades. Preserve all 19 HTTP method/path contracts. Extract service seams from the 7,038-line module; do not replace its workflow behavior with the FORGE server.

**Existing test candidates:** `tests/test_f2w_api.py`; `tests/test_agent_settings.py`.

## B02 - Pending actions, sessions and UI graph updates

**Actual F2W file:** `app/modules/f2w_agent/api.py`

**Verified symbols:** `AgentPipelineService.ask` (L3595-L3641); `AgentPipelineService.ask_with_progress` (L3739-L3787); `AgentPipelineService.act` (L5919-L5957); `AgentPipelineService.act_with_progress` (L5972-L6012); `AgentPipelineService._activate_session` (L2555-L2576); `AgentPipelineService._pending_payload` (L6581-L6600); `AgentPipelineService._query_graph_event` (L2908-L2914); `AgentPipelineService._response` (L6472-L6508); `AgentPipelineService._extract_rebuild_reload` (L5517-L5711).

**FORGE entries:** R-04, J-04, U-01.

**Proposed destination:** `app/modules/application.py + agent_runtime/ + jobs/`

Keep session_id/client_turn_id/parent_turn_id, pending selection indexes, active-paper follow-ups, graph_update progress and response statuses. Preserve candidate/session graph visibility while separately gating publication. Never assume a LangGraph event is already an F2W UI event.

**Existing test candidates:** `tests/test_f2w_api.py`; `ui/e2e/pending-ingest.spec.ts`; `ui/e2e/sessions.spec.ts`.

## B03 - Graph detail, editing and code-snippet contracts

**Actual F2W file:** `app/modules/f2w_agent/api.py`

**Verified symbols:** `GraphNode` (L167-L193); `GraphPayload` (L203-L207); `LinkedCodeSnippetUpdate` (L317-L326); `GraphRelationshipUpdate` (L329-L334); `AgentPipelineService.update_graph_node` (L2978-L3046); `AgentPipelineService._apply_relationship_updates` (L3069-L3129); `AgentPipelineService._apply_linked_snippet_updates` (L3131-L3332); `resolve_graph_node` (L782-L814).

**FORGE entries:** K-03, K-04, K-06.

**Proposed destination:** `app/api/compat/graph.py + app/modules/graph/`

Translate existing UI graph detail/edit results over Tiled, including _action=upsert|unlink, relationship add/remove and publication provenance. Preserve external IDs and references; do not merely disable edits when retiring Splash. Unsupported mutation semantics block the release or require a separate owner-approved exception.

**Existing test candidates:** `tests/test_f2w_api.py`; `ui/src/app/components/GraphMockup.test.ts`; `ui/e2e/kg-viewer.spec.ts`.

## B04 - Coordinator and CLI compatibility

**Actual F2W file:** `app/modules/f2w_agent/coordinator.py`

**Verified symbols:** `CoordinatorConfig` (L68-L94); `Coordinator` (L162-L654).

**FORGE entries:** R-04, R-05.

**Proposed destination:** `app/modules/f2w_agent/ facades + application.py`

Keep configuration fields, workdir/artifact paths and supported command behavior. Preserve current Academy-facing integration until caller parity is demonstrated; the scoped LangGraph supervisor replaces orchestration gradually.

**Existing test candidates:** `tests/test_f2w_coordinator.py`.

## B05 - Workflow intent routing

**Actual F2W file:** `app/modules/f2w_agent/orchestrator_agent.py`

**Verified symbols:** `WorkflowOrchestratorAgent` (L153-L516); `WorkflowOrchestratorAgent.validate` (L368-L465); `WorkflowOrchestratorAgent.decide` (L468-L516).

**FORGE entries:** R-02, R-04.

**Proposed destination:** `app/modules/agent_runtime/ routing adapters`

Retain deterministic validation, pending-action handling, active-paper state and safe fallback while moving execution into LangGraph. No new default prompt or provider stack.

**Existing test candidates:** `tests/test_workflow_orchestrator.py`.

## B06 - Evidence ranking debate agent

**Actual F2W file:** `app/modules/f2w_agent/debate_agent.py`

**Verified symbols:** `EvidenceDebateAgent` (L112-L267).

**FORGE entries:** R-04.

**Proposed destination:** `app/modules/curation/ or retained f2w_agent adapter`

Retain candidate-ranking specialist behavior. It is not replaced by a generic retrieval tool without equivalent behavior and output tests.

**Existing test candidates:** `tests/test_f2w_api.py`.

## B07 - Active-paper bounded answers

**Actual F2W file:** `app/modules/f2w_agent/paper_evidence_agent.py`

**Verified symbols:** `PaperEvidenceAgent` (L295-L458); `_eligible_pages` (L54-L80); `_citation_errors` (L88-L119); `summarize_extracted_terms` (L122-L292).

**FORGE entries:** L-09, L-10, E-03, E-04.

**Proposed destination:** `app/modules/evidence/ + retrieval/`

Preserve manifest-bounded pages and citation checking for the active extracted paper. New FORGE section/figure readers extend the evidence service, not permission to answer from unrelated corpus pages.

**Existing test candidates:** `tests/test_paper_evidence_agent.py`.

## B08 - Real retrieval, sufficiency and leeway

**Actual F2W file:** `app/modules/f2w_agent/retrieval_agent.py`

**Verified symbols:** `RetrievalAgent.query` (L1168-L1399); `RetrievalAgent._parallel_retrieve` (L676-L741); `RetrievalAgent._fanout_hits` (L941-L1006); `RetrievalAgent._build_merged_context` (L1120-L1130); `RetrievalAgent._hybrid_pack` (L1132-L1165); `RetrievalAgent._synthesize_leeway_answer` (L1449-L1485); `build_judge_prompt` (L219-L249); `leeway_system_for` (L196-L200).

**FORGE entries:** K-09, K-10, K-12.

**Proposed destination:** `app/modules/retrieval/ baseline adapter first`

Preserve lexical/semantic KG retrieval, separate graph identities, parallel paths, source_rag, existing contextual leeway and numeric-claim evidence rules. Ops/MP/live-Tiled branches already exist; retain them behind compatibility scope without adding FORGE experimental tools.

**Existing test candidates:** `tests/test_retrieval_agent.py`; `tests/test_build_context_filtering.py`; `tests/test_rag_system_prompt.py`; `tests/test_multi_kg.py`.

## B09 - KnowledgeGraph implementation and independent chat API

**Actual F2W file:** `app/modules/kg_rag_api.py`

**Verified symbols:** `KnowledgeGraph` (L778-L1266); `retrieve_nodes` (L1279-L1334); `NodeInfo.score_prp` (L734-L751); `answer` (L1640-L1690); `create_fastapi_app` (L196-L318); `run_fastapi` (L321-L330); `MissingNodeTracker` (L346-L373).

**FORGE entries:** K-03, K-09, K-10.

**Proposed destination:** `app/modules/kg_rag_api.py facade + retrieval/graph services`

Preserve both actual lexical and semantic implementations, BFS/context/evidence behaviors, knowledge-gap recording and six OpenAI/Ollama-compatible routes. Do not confuse this server with the 19-route UI agent server.

**Existing test candidates:** `tests/test_kg_rag_api.py`; `tests/test_kg_rag_api_extended.py`; `tests/test_kg_rag_api_coverage.py`; `tests/test_fastapi_endpoints.py`.

## B10 - Async F2W inference path

**Actual F2W file:** `app/modules/kg_rag_api.py`

**Verified symbols:** `ChatClient` (L1396-L1403); `OllamaClient` (L1406-L1429); `CBorgClient` (L1432-L1472); `make_chat_client` (L1475-L1482); `call_llm` (L1590-L1637).

**FORGE entries:** R-04, R-10, R-11.

**Proposed destination:** `existing classes + app/modules/llm_adapters.py`

Retain async messages-based contract and existing CBORG/Ollama configuration, timeout and tracing paths. Do not replace it with the different prompt-string synchronous client.

**Existing test candidates:** `tests/test_chat_clients.py`; `tests/test_kg_rag_api.py`.

## B11 - Synchronous F2W inference path

**Actual F2W file:** `app/modules/term_extractor/clients.py`

**Verified symbols:** `ChatClient` (L46-L55); `OllamaChatClient` (L58-L86); `CBorgChatClient` (L89-L124); `make_chat_client` (L127-L141); `_traced_chat` (L16-L43).

**FORGE entries:** E-01, U-03, U-04, U-11.

**Proposed destination:** `existing classes + app/modules/llm_adapters.py`

Keep chat(prompt, temperature, timeout, trace_label) and existing tracing/concurrency behavior. Adapt every FORGE paper inference site to a suitable existing F2W path; new multimodal/tool-call capabilities require a minimal additive reviewed extension.

**Existing test candidates:** `tests/test_chat_clients.py`; `tests/test_cborg_http.py`; `tests/test_cborg_limiter.py`.

## B12 - Existing LangGraph extraction loop

**Actual F2W file:** `app/modules/term_extractor/agent.py`

**Verified symbols:** `build_graph` (L9-L32).

**FORGE entries:** R-01, R-02, R-04.

**Proposed destination:** `app/modules/agent_runtime/ + retained term_extractor/agent.py`

LangGraph is already used: MessagesState, ToolNode, tool-call continuation and graph.compile. Adopt the shared registry/supervisor architecture, not a redundant first graph framework. Wrap current extraction before changing its behavior.

**Existing test candidates:** `tests/test_extractor_agent.py`.

## B13 - Extractor orchestration and tool-capable model construction

**Actual F2W file:** `app/modules/term_extractor/orchestrator.py`

**Verified symbols:** `Orchestrator` (L152-L652); `Orchestrator._build_llm` (L212-L229); `Orchestrator._build_tools` (L199-L210); `Orchestrator.process_page` (L235-L266); `run_extraction` (L655-L685).

**FORGE entries:** E-01, L-12, L-13.

**Proposed destination:** `app/modules/extraction/strategies/legacy_f2w.py + retained term_extractor/`

_build_llm already constructs ChatOpenAI for CBORG or Ollama /v1 and binds tools. Reuse that configuration seam without instantiating the full extractor to obtain a model. Preserve page fallback, concurrency, selected-page behavior, snippet extraction and provenance.

**Existing test candidates:** `tests/test_extractor_agent.py`; `tests/test_source_repos.py`.

## B14 - Current extraction tools and full-list merge risk

**Actual F2W file:** `app/modules/term_extractor/tools.py`

**Verified symbols:** `ToolState` (L19-L44); `build_tools` (L47-L303); `build_tools.check_existing_term` (L49-L65); `build_tools.fuzzy_merge_term` (L68-L158); `build_tools.register_term` (L230-L301).

**FORGE entries:** C-01, C-02, C-03, C-04, C-05, C-06.

**Proposed destination:** `app/modules/canonicalization/ behind existing tool wrappers`

The inspected fuzzy_merge_term builds an LLM prompt from ALL store display names; do not claim ANN shortlisting is already implemented on this path. Preserve behavior at H2; add bounded candidate search/merge safety as a measured H3-approved improvement. Keep page/source context and evidence registration.

**Existing test candidates:** `tests/test_extractor_agent.py`.

## B15 - Persistent extracted term records

**Actual F2W file:** `app/modules/term_extractor/store.py`

**Verified symbols:** `TermStore` (L14-L346); `TermStore.upsert` (L131-L208); `TermStore.add_code_snippets` (L227-L270); `TermStore.stamp_source_metadata` (L272-L294); `TermStore.save` (L78-L92).

**FORGE entries:** J-01, E-02.

**Proposed destination:** `retained term_extractor/store.py + extraction record adapter`

Preserve locking, merge semantics, progress fields, publications, code snippets and source metadata. Do not exchange TermStore for a FORGE record model by dropping unmapped fields.

**Existing test candidates:** `tests/test_extractor_agent.py`.

## B16 - Extraction schema validation

**Actual F2W file:** `app/modules/term_extractor/schema.py`

**Verified symbols:** `SchemaHelper` (L107-L345).

**FORGE entries:** E-02, K-01.

**Proposed destination:** `app/modules/extraction/validation + retained schema helper`

Retain actual category/slot correction and relation checks. The supplied RSoXS overlay still models RSoXSMeasurement as an ExperimentalTechnique subclass. Changing that is a scientific migration, not a packaging fix.

**Existing test candidates:** `tests/test_xray_schema.py`; `tests/test_extractor_agent.py`.

## B17 - Extraction record DTOs

**Actual F2W file:** `app/modules/term_extractor/models.py`

**Verified symbols:** `TermRecord` (L96-L173); `RelationRecord` (L17-L39); `ContextSnippet` (L43-L59); `PropertyRecord` (L63-L92).

**FORGE entries:** E-02, E-05.

**Proposed destination:** `app/modules/contracts/ mapping + existing models`

Map donor records to these actual fields, preserving publications, code/repository provenance and per-field uncertainty. Proposed WorkVersion/Mention models are separate H3 changes, not claimed current classes.

**Existing test candidates:** `tests/test_extractor_agent.py`.

## B18 - Existing source RAG and document indexes

**Actual F2W file:** `app/modules/hybrid_rag/pipeline.py`

**Verified symbols:** `HybridSourceRAG` (L211-L289); `HybridSourceRAG.pack` (L253-L289); `build_literature_index` (L63-L138); `build_ops_index` (L141-L208); `hybrid_rag_enabled` (L55-L60).

**FORGE entries:** L-11, L-12, E-04, K-10.

**Proposed destination:** `retain hybrid_rag/ first; later retrieval/ service facades`

Source-document RAG already exists and is toggled via source_rag/F2W_SOURCE_RAG. Preserve both literature and existing ops separation. New FORGE parsing must populate or adapt these indexes instead of building an unrelated parallel corpus.

**Existing test candidates:** `tests/test_hybrid_rag.py`.

## B19 - Actual BM25 index

**Actual F2W file:** `app/modules/hybrid_rag/index_store.py`

**Verified symbols:** `BM25Index` (L26-L62); `SourceIndex` (L65-L139); `write_index` (L142-L172); `merge_chunks` (L175-L184).

**FORGE entries:** L-11, K-10.

**Proposed destination:** `retained hybrid_rag/index_store.py or retrieval/index_store.py`

Do not describe independent lexical document retrieval as wholly future. Keep existing chunk IDs/index metadata and rebuild compatibility. Any new vector mode is additive and separately evaluated.

**Existing test candidates:** `tests/test_hybrid_rag.py`.

## B20 - Actual evidence-context builder

**Actual F2W file:** `app/modules/hybrid_rag/context.py`

**Verified symbols:** `ContextBuilder` (L37-L145); `ContextBuilder.build` (L40-L104); `_pdf_cite` (L12-L16); `_ops_cite` (L19-L26); `_kg_cite` (L29-L34).

**FORGE entries:** E-03, E-04, K-10.

**Proposed destination:** `retained hybrid_rag/context.py -> retrieval/evidence_context.py facade`

This is a verified context builder, not retrieval/models.py. Keep source-kind partitions, budgets and literal citation grammar. New figure locators extend it without changing existing citation chip rendering.

**Existing test candidates:** `tests/test_hybrid_rag.py`; `tests/test_build_context_filtering.py`.

## B21 - Hybrid evidence model and ranker

**Actual F2W file:** `app/modules/hybrid_rag/evidence.py`

**Verified symbols:** `Evidence` (L23-L64); `kg_evidence_from_hit` (L93-L115); `evidence_from_chunk` (L118-L156); `infer_intent` (L67-L90).

**FORGE entries:** E-02, E-04.

**Proposed destination:** `app/modules/evidence/ adapters + retained hybrid_rag evidence`

Keep current provenance/source_kind/source_type/graph_id tags. Reconcile richer FORGE evidence objects without relabeling operational or Materials Project evidence as a paper statement.

**Existing test candidates:** `tests/test_hybrid_rag.py`.

## B22 - Download and acquisition behavior

**Actual F2W file:** `app/modules/f2w_agent/download_agent.py`

**Verified symbols:** `DownloadAgent` (L356-L888); `DownloadAgent.search_candidates` (L820-L833); `DownloadAgent.download_selected` (L836-L868); `DownloadAgent._validate_downloaded_pdf` (L588-L636); `_append_download_record` (L330-L334).

**FORGE entries:** L-01, L-02, L-03, L-04, L-05, L-19, L-20.

**Proposed destination:** `app/modules/literature/ services under existing DownloadAgent facade`

Preserve OpenAlex/arXiv candidates, deduplication, ranking, PDF validation, preview and download manifest behavior. Port FORGE resolver/provider batching beneath that contract; do not bypass the separate candidate-choice/download/extraction UI flow.

**Existing test candidates:** `tests/test_download_agent.py`; `tests/test_download_pdfs.py`; `ui/e2e/pending-ingest.spec.ts`.

## B23 - Harvest Work/version identity and operator paths

**Actual F2W file:** `scripts/harvest_rsoxs.py`

**Verified symbols:** `collapse_near_duplicates` (L835-L874); `works_same_identity` (L1821-L1851); `harvest` (L1225-L1388); `import_pubtemp` (L1945-L2038); `harvest_pending_pdfs` (L2228-L2330).

**FORGE entries:** L-04, L-06, J-06, J-07, O-12.

**Proposed destination:** `literature/work_identity.py + manifest/operator facades`

The branch has implemented bibliographic near-duplicate/version workflows. Reuse these, with explicit H3 identity improvements; do not restart from only earlier design notes. Retain publication-list import and pending-PDF recovery.

**Existing test candidates:** `tests/test_harvest_rsoxs.py`.

## B24 - Graph construction and field retention

**Actual F2W file:** `app/modules/json2kg.py`

**Verified symbols:** `make_id` (L85-L100); `copy_passthrough_slots` (L61-L73); `normalize_publications` (L134-L181); `make_code_snippet_node` (L204-L260); `build_graph` (L263-L454); `convert_terms_to_graph` (L457-L482); `main` (L505-L533).

**FORGE entries:** K-01, J-01, J-02.

**Proposed destination:** `retain json2kg CLI facade + graph/snapshot services`

Freeze identifiers and field/edge retention. Preserve MatKG things/associations and snippet/license/source fields through Tiled projection. New revision/identity rules require explicit migration receipts; do not recreate IDs from new labels.

**Existing test candidates:** `tests/test_json2kg.py`; `tests/test_json2kg_extended.py`.

## B25 - Existing Tiled lookup path

**Actual F2W file:** `app/modules/tiled_graph.py`

**Verified symbols:** `TiledGraphConfig` (L283-L308); `TiledLookupGraph` (L338-L430); `tiled_graphql` (L748-L768); `entity_to_node` (L637-L684); `lookup_experiment_identity` (L962-L1053); `probe_tiled_status` (L225-L279); `coerce_local_tiled_uri` (L161-L175).

**FORGE entries:** K-03, K-04, K-05, K-06.

**Proposed destination:** `graph/backends/tiled.py + retained compatibility lookup`

Tiled is present as lookup/enrichment, not yet a full replacement for every Splash edit/rebuild path. Preserve live_tiled/status/identity/neighborhood behavior and local-host policy while adding the required literature adapter. No new FORGE experimental functionality.

**Existing test candidates:** `tests/test_tiled_graph.py`; `tests/test_f2w_api.py`.

## B26 - Existing snapshot promotion and simulation fixtures

**Actual F2W file:** `app/modules/tiled_sim.py`

**Verified symbols:** `push_fixture_to_tiled` (L506-L610); `promote_rsoxs_snapshot` (L941-L1005); `iter_identity_entities` (L330-L405); `iter_identity_links` (L408-L431).

**FORGE entries:** K-03, K-06, J-02.

**Proposed destination:** `existing fixture/operator path + literature publication adapters`

Keep existing F2W fixture and snapshot-promotion behavior accounted for; do not move FORGE simulators into F2W or treat synthetic runs as paper gold data. Adapt approved publication code after ownership/recovery review.

**Existing test candidates:** `tests/test_tiled_sim.py`.

## B27 - Scoped multi-graph identities

**Actual F2W file:** `app/modules/f2w_agent/multi_kg.py`

**Verified symbols:** `graph_id_for_path` (L93-L106); `default_json_graph_paths` (L169-L218); `ScoredHit` (L256-L277); `merge_hits` (L280-L310); `primary_json_graph_path` (L221-L227).

**FORGE entries:** K-09, K-10.

**Proposed destination:** `retrieval/fanout.py adapters + retained multi_kg.py`

Keep (graph_id,node id) identity, selected graph lists, primary viewer path, available_graphs/selected_graph_ids and same-label collision behavior. Do not union literature, ops or Tiled into one anonymous graph.

**Existing test candidates:** `tests/test_multi_kg.py`; `ui/src/app/components/kgCatalog.test.ts`.

## B28 - Session persistence and compression

**Actual F2W file:** `app/modules/f2w_agent/session_memory.py`

**Verified symbols:** `SessionMemory` (L211-L529); `SessionMemory.record_turn` (L395-L476); `SessionMemory.enrich_followup_question` (L381-L390); `SessionMemory.compress` (L485-L529).

**FORGE entries:** R-07, J-04.

**Proposed destination:** `agent_runtime/session bridge + existing session_memory path`

Retain current on-disk session state, topic/history rewriting and compression boundaries. Reuse source paths or migrate with receipts. Checkpoints do not replace browser localStorage or workflow_state.json automatically.

**Existing test candidates:** `tests/test_f2w_api.py`; `ui/e2e/sessions.spec.ts`.

## B29 - Persistent approval workflow

**Actual F2W file:** `app/modules/f2w_agent/workflow_state.py`

**Verified symbols:** `WorkflowStateStore` (L50-L120); `default_workflow_state` (L26-L47).

**FORGE entries:** R-07, J-04, U-01.

**Proposed destination:** `agent_runtime/checkpointer.py + workflow compatibility adapter`

Preserve current pending downloads/extractions across restarts and named-session isolation. Add explicit mapping from old workflow phases to LangGraph state. Do not replay approved effects when resuming an interrupt.

**Existing test candidates:** `tests/test_workflow_orchestrator.py`; `tests/test_f2w_api.py`.

## B30 - Existing execution trace and annotations

**Actual F2W file:** `app/modules/f2w_agent/trace.py`

**Verified symbols:** `TurnTrace` (L353-L554); `record_llm_call` (L736-L761); `record_retrieval` (L728-L729); `parse_citations` (L563-L569); `append_ui_events` (L789-L799); `append_annotation` (L802-L810); `load_detail` (L813-L823); `redact` (L217-L220).

**FORGE entries:** K-13, K-14, R-08.

**Proposed destination:** `provenance/ facade + retained trace routes`

F2W already has detailed traces and user annotations. Combine with GraphRecorder event sinks; preserve turn IDs, redaction, unclipped detail and UI feedback endpoints. Do not replace annotations with a donor-only tracing system.

**Existing test candidates:** `tests/test_trace.py`.

## B31 - Scientific provenance helpers

**Actual F2W file:** `app/modules/prov.py`

**Verified symbols:** `work_entity` (L157-L190); `extraction_activity` (L115-L154); `kg_snapshot_entity` (L193-L222); `extraction_provenance_edges` (L225-L273).

**FORGE entries:** K-13, E-02.

**Proposed destination:** `provenance/models and recorder adapters`

Preserve existing PROV vocabulary and structured extraction/snapshot links, distinct from FORGE operational execution provenance.

**Existing test candidates:** `tests/test_prov.py`.

## B32 - Configuration precedence

**Actual F2W file:** `app/modules/project_config.py`

**Verified symbols:** `get_config` (L33-L40); `config_value` (L77-L90); `secret_env` (L93-L100).

**FORGE entries:** R-05.

**Proposed destination:** `existing project_config + application instance config adapter`

Preserve environment/config.yml precedence and secret sourcing. Library import must not load arbitrary host .env or change globals; CLI remains the explicit config-loading boundary.

**Existing test candidates:** `tests/test_project_config.py`.

## B33 - Existing Materials Project evidence

**Actual F2W file:** `app/modules/f2w_agent/materials_project.py`

**Verified symbols:** `mp_context_for_question` (L255-L257); `format_mp_context` (L156-L191); `cite_token` (L194-L201).

**FORGE entries:** No donor replacement required; preserve existing behavior..

**Proposed destination:** `retained optional compatibility evidence adapter`

Already participates in F2W QA. Do not remove it merely because donor migration is literature-only. Keep [MP:] source labeling; do not broaden it into experimental analysis.

**Existing test candidates:** `tests/test_materials_project.py`; `ui/e2e/citations.spec.ts`.

## B34 - Current corpus repair

**Actual F2W file:** `scripts/repair_corpus.py`

**Verified symbols:** `repair` (L379-L478); `step_quarantine_hard_stubs` (L142-L165); `step_fix_known_bad_relations` (L197-L245); `step_merge_near_duplicates` (L248-L363).

**FORGE entries:** C-12, C-13, U-09.

**Proposed destination:** `canonicalization/repair services + operator CLI facade`

Preserve current repair utilities as read/propose adapters; characterize them before substituting FORGE campaigns. No bulk apply without source/scope/semantic review.

**Existing test candidates:** Add characterization tests before changing behavior..

## B35 - Current corpus quality checks

**Actual F2W file:** `scripts/corpus_qa.py`

**Verified symbols:** `audit` (L814-L912); `audit_duplicate_works` (L173-L278); `audit_stubs` (L281-L343); `audit_technique_measurement` (L346-L410); `audit_numeric_values` (L413-L515); `audit_relations` (L637-L735).

**FORGE entries:** Q-10, C-16.

**Proposed destination:** `evals/ corpus QA + retained operator CLI`

Retain quality checks and exact failure categories. These checks do not establish the intended Work/measurement schema is already active.

**Existing test candidates:** Add characterization tests before changing behavior..

## B36 - Existing operator invocation

**Actual F2W file:** `scripts/run.py`

**Verified symbols:** `parse_args` (L22-L48); `main` (L51-L149).

**FORGE entries:** J-05, O-06.

**Proposed destination:** `scripts/run.py facade -> retained extractor/new job service`

Preserve command options and path resolution. New library packaging cannot require all operators to switch imports simultaneously.

**Existing test candidates:** `tests/test_extractor_agent.py`.

## Supporting files and limits

All supporting schema, configuration, launch, CI and UI-lockfile hashes are in `f2w_baseline_manifest.json`. The complete non-vendored Python symbol inventory is in `f2w_source_inventory.json`. It excludes the vendored `splash_links/` Python tree, and no local databases or copyrighted PDFs are copied into this handoff.

Runtime tests, UI execution, server connectivity, dependency installation and git-object verification remain H0 work. Source inspection is not H0 approval.


## v4 implementation-policy overlay

Source paths, hashes and symbols above remain snapshot evidence. Current decisions D12-D20 govern their use: both managed/central Tiled with app-owned disposable tests; binary acceptance; preserved frontend model selection with shared all-provider limits; agent-private source documents/figures and user-safe projections; explicit schema assets/validation without automatic semantic reclassification; reusable Knowledge Operations. A source function that serves PDF/text/crops is not thereby approved as a public endpoint. See MIGRATION_PLAN.md and CONTENT_ACCESS_POLICY.md.
