# FORGE paper migration: source-to-target catalog

## Draft v5 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V5.md; source observations themselves remain unchanged. O03 is the only pending owner policy, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.


**Draft v5.0 | Actual F2W baseline anchors; proposed targets remain app/modules; API lives at app/api.**

All source paths, symbols and line spans below refer to the supplied FORGE archive recorded in source_lock.json. Targets are proposed, while receiving-source anchors B01-B36 are now statically verified against the supplied rsoxs archive. See F2W_BASELINE_AND_CROSSWALK.md; execution/parity and H0 approval remain pending. Requirements: Tiled Graph and paper figures are mandatory; all inference uses existing F2W LLM paths/classes; Python imports and FastAPI both work. No simulation/experimental analysis migration.

Transfer means preserve the capability, not blindly copy the implementation. Per-file reference-only dispositions indicate mappings or mechanisms that must be reconciled; no optional label authorizes dropping paper behavior.

## R-01 - src/forge/agents/registry.py

**Receiving-source anchors:** B12 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/registry.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `44d91d9c92dc032a08f4a60a9505811b43521bc0f1c62a3fd39ac2f9e1e1d9f6`

Use instance-scoped, explicitly bootstrapped registries; replace donor domain allowlist; forbid experimental sim/analysis/catalog tools. Freeze registration before concurrent requests. Preserve canonical tool IDs and detect served-name collisions.

**Verified symbols:**

- `ToolRegistry` - lines 56-226
- `ToolRegistry.register(self, domain: str, verb: str, object_name: str, fn: Any, schema: dict[str, Any] | None=None)` - lines 86-139
- `ToolRegistry.get(self, name: str)` - lines 141-150
- `ToolRegistry.list_tools(self)` - lines 152-158
- `ToolRegistry.list_by_domain(self, domain: str)` - lines 160-172
- `ToolRegistry.verify(self)` - lines 178-226
- `get_registry` - lines 233-235

**Donor test candidates:** `tests/agents/test_tool_registry.py`, `tests/fitness/test_tool_registry.py`

## R-02 - src/forge/agents/skills.py

**Receiving-source anchors:** B05, B12 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/skills.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `aa5c4c97cb35214ccacbd64e05d691a3e62a66cdd7f177351d7fb2eefcd0ccd0`

Retain skill-to-tool integrity and coverage; remove imported global catalog dependencies; skills provide guidance, not authorization.

**Verified symbols:**

- `Skill` - lines 69-92
- `SkillRegistry` - lines 95-311
- `SkillRegistry.register(self, skill: Skill)` - lines 124-178
- `SkillRegistry.resolve(self, name: str)` - lines 217-247
- `SkillRegistry.verify(self)` - lines 251-287
- `SkillRegistry.render_prompt_section(self, profile: str='general')` - lines 291-311
- `skills_for_profile(skills: Iterable[Skill], profile: str)` - lines 314-316
- `get_skill_registry` - lines 375-377

**Donor test candidates:** `tests/agents/test_skill_registry.py`, `tests/fitness/test_every_tool_belongs_to_a_skill.py`

## R-03 - src/forge/agents/skill_definitions.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/skill_definitions.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4c5790972c449aaae5a02438915e0e39ad138d6d253cbaa7c6ee5eb4b5bc60aa`

Retain search_literature. Adapt query_knowledge_graph and trace_provenance to literature scope. Do not bring over the entire SKILLS tuple. Composite sample-to-simulation skill stays in FORGE.

**Verified symbols:**

- `register_all_skills(registry: SkillRegistry | None=None)` - lines 507-528

**Donor test candidates:** `tests/agents/test_skill_registry.py`

## R-04 - src/forge/agents/graph.py

**Receiving-source anchors:** B02, B04, B05, B06, B10, B12 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/{state,supervisor,executor}.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `123ad976b00017a19ed5b843837e6b7b859e8d817f9576c5627425838a2518a3`

Reuse graph construction, repeated-call protection and bounded execution patterns; rewrite simulation-oriented prompts and catalog budgets. Remove legacy subagent fallback and global SKILLS imports. Add authorization/approval preflight and first-class cancellation. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `AgentState` - lines 249-273
- `_supervisor_node(state: AgentState, model_with_tools: BaseChatModel)` - lines 276-281
- `_should_continue(state: AgentState)` - lines 284-290
- `_call_key(tool_name: str, tool_input: Any)` - lines 294-299
- `_already_called(messages: list[BaseMessage])` - lines 302-332
- `_discovery_calls(messages: list[BaseMessage])` - lines 360-367
- `_over_discovery_budget(tool_name: str, prior: int)` - lines 370-377
- `_execute_tools_node(state: AgentState, tool_map: dict[str, Any], tracer: LangfuseTracer | None=None)` - lines 380-633
- `build_supervisor_graph(model: BaseChatModel, tools: list[BaseTool], system_prompt: str=DEFAULT_SYSTEM_PROMPT, checkpointer: BaseCheckpointSaver | None=None, tracer: LangfuseTracer | None=None)` - lines 636-755

**Donor test candidates:** `tests/agents/test_repeated_tool_calls.py`

## R-05 - src/forge/agents/profiles.py

**Receiving-source anchors:** B04, B32 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/profiles.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `594ab97cfe37c3244ccef7741b4966afe67c1a19a5ae819449375978624df159`

Donor profiles are general/calibration. New literature_reader, curator and maintainer policies are proposals, not existing donor profiles. Privileges must derive from authenticated context, not a model-selected profile.

**Verified symbols:**

- `AgentProfile` - lines 43-61
- `get_profile(name: str | None)` - lines 126-136

**Donor test candidates:** `tests/agents/test_profiles.py`

## R-06 - src/forge/agents/message_trimming.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/message_trimming.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `09862805ecd556effecbdd07661ebc0b7ee2cc74d91737d5a1f31edb91d51fdf`

Preserve tool-call/message pairing; replace omitted media with bounded references. Keep evidence artifacts outside chat-history trimming.

**Verified symbols:**

- `media_placeholder(block: dict[str, Any])` - lines 116-135
- `strip_media_from_content(content: Any)` - lines 151-182
- `current_turn_start(messages: Sequence[BaseMessage])` - lines 185-202
- `trim_stale_media(messages: Sequence[BaseMessage])` - lines 205-246
- `trim_stale_tool_results(messages: Sequence[BaseMessage], *, budget_chars: int=STALE_TOOL_RESULT_BUDGET_CHARS)` - lines 285-343
- `repair_orphaned_tool_calls(messages: Sequence[BaseMessage])` - lines 392-466
- `count_media_blocks(messages: Iterable[BaseMessage])` - lines 469-476

**Donor test candidates:** `tests/agents/test_message_trimming.py`, `tests/agents/test_orphaned_tool_calls.py`

## R-07 - src/forge/agents/checkpointer.py

**Receiving-source anchors:** B28, B29 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/agent_runtime/checkpointer.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6e77d1f993fff5aa55c37f92a7d93bc7dc152912c033941b2de6d78a8fd1c7fa`

Inject paths and lifecycle; isolate tenant/thread state; no destructive compaction/reset during active jobs. Test async lifecycle and process restarts against chosen LangGraph lock.

**Verified symbols:**

- `CheckpointerConfig` - lines 73-84
- `compact_checkpoint_db(db_path: Path | None=None)` - lines 125-189
- `get_checkpoint_db_path` - lines 192-208
- `get_checkpointer(config: CheckpointerConfig | None=None)` - lines 211-265
- `get_async_checkpointer` - lines 268-302
- `_open_async_checkpointer(loop_key: int)` - lines 305-337
- `_close_async_checkpointers` - lines 340-385
- `reset_checkpointer` - lines 388-406

**Donor test candidates:** `tests/agents/test_async_checkpointer_single_flight.py`, `tests/agents/test_checkpointer.py`

## R-08 - src/forge/agents/langfuse_tracer.py

**Receiving-source anchors:** B30 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/provenance/langfuse.py`
**Disposition:** optional | **Release obligation:** retain_telemetry_capability_export_disabled_unless_configured
**Source SHA-256:** `0b8055f6647c5e3518ac6c53ad979887b619faa90b38b597ee562bd977ec0ff7`

Opt-in telemetry adapter with redaction; do not make an external tracing account or raw-paper logging a requirement.

**Verified symbols:**

- `tracer_from_settings` - lines 99-143
- `LangfuseTracer` - lines 146-575

**Donor test candidates:** `tests/agents/test_langfuse_tracing.py`

## R-09 - src/forge/agents/tools/__init__.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/tools/bootstrap.py`
**Disposition:** replace-bootstrap | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `5b1f93db62cfeef389a618a8ab0de150724d2bdf8e432bb88cb7e0aaa4de9ed4`

Do not copy import-time registration of all FORGE domains. Build a fresh bootstrap from an explicit literature-only capability manifest.

**Verified symbols:**


**Donor test candidates:** `tests/fitness/test_tool_names_are_namespaced.py`

## R-10 - als_knowledge_agent/src/als_knowledge_agent/graph/orchestrator.py

**Receiving-source anchors:** B10 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/cli.py and app/modules/application.py`
**Disposition:** reference-only | **Release obligation:** required_review_not_wholesale_copy
**Source SHA-256:** `c527b9fae2adcc4c203ff3d319e0b461c7ae1be46394d6dcc419a725960e2eff`

CLI integration pattern only: the donor CLI still constructs simulation tools. Preserve F2W entrypoints and inject a literature-only tool set.

**Verified symbols:**

- `run_query(query: str, *, model: str | None=None)` - lines 113-138
- `_get_llm(model: str | None=None)` - lines 58-74
- `_cli_tools` - lines 77-110

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## L-01 - src/forge/agents/literature/tools.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/tools/literature.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `2212bfe462de260d784d58f5587f3b4b36e8be4f88cde0d543280bc2ea537c7e`

Six verified donor tool IDs; retain FORGE-facing behavior through adapters while replacing direct provider/cache globals with F2W services.

**Verified symbols:**

- `lit_search_papers(query: str, technique: str='', material: str='', year_from: int=0, year_to: int=0, max_results: int=10)` - lines 69-111
- `lit_get_papers_by_doi(dois: str)` - lines 115-135
- `lit_find_citing_papers(openalex_id: str, max_results: int=10)` - lines 139-161
- `lit_find_related_papers(paper_id: str, max_results: int=10)` - lines 165-185
- `lit_read_paper(doi: str)` - lines 189-222
- `lit_read_paper_section(doi: str, section: str)` - lines 226-245
- `build_lit_tools` - lines 248-261

**Donor test candidates:** `tests/agents/test_lit_tools.py`

## L-02 - src/forge/agents/literature/openalex.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/providers/openalex.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e8850b695691d2223402764cb82e2853cda0dc330d997b20a280e083ee83ace6`

Preserve bulk lookup, positive/negative caches, quotas and status distinctions. Inject auth/settings/http/cache; unavailable is not no-results. Review API limits at implementation time.

**Verified symbols:**

- `OpenAlexNoData` - lines 218-241
- `OpenAlexQuotaPaused` - lines 244-260
- `is_quota_paused(value: object)` - lines 263-265
- `is_not_found(value: object)` - lines 268-270
- `pause_openalex(seconds: float, reason: str)` - lines 289-302
- `openalex_pause_state` - lines 305-323
- `reset_openalex_pause` - lines 326-331
- `openalex_auth_params` - lines 437-451
- `openalex_headers` - lines 504-506
- `fetch_openalex(path: str, params: dict[str, Any] | None=None, *, ttl: float | None=None, use_cache: bool=True, timeout: float=15.0, max_attempts: int=3)` - lines 514-637
- `abstract_from_inverted_index(inv_idx: dict[str, list[int]] | None)` - lines 640-659
- `search_papers(query: str, *, technique: str='', material: str='', year_from: int=0, year_to: int=0, max_results: int=10)` - lines 707-758
- `get_papers_by_doi(dois: list[str])` - lines 761-791
- `fetch_works_by_doi(dois: Sequence[str], *, batch: int=BULK_DOI_BATCH, prime_cache: bool=True)` - lines 854-936
- `find_citing_papers(openalex_id: str, max_results: int=10)` - lines 949-978
- `find_related_papers(paper_id: str, max_results: int=10)` - lines 981-1029

**Donor test candidates:** `tests/agents/literature/test_openalex_bulk.py`, `tests/agents/literature/test_openalex_client.py`

## L-03 - src/forge/agents/literature/resolver_batch.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/resolver_batch.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `08f720a8d098d0eaa70263d6b48b4af50c38607d2cff83a2c9fc96af5ae83f52`

Preserve batching, throttling, cache and provider fallbacks. Distinguish bibliographic candidate resolution from authoritative Work identity.

**Verified symbols:**

- `normalise_doi(value: object)` - lines 115-121
- `TokenBucket` - lines 129-164
- `Resolved` - lines 173-189
- `ResolverCache` - lines 192-256
- `ReferenceLike` - lines 268-284
- `title_similarity(a: str, b: str)` - lines 295-305
- `BatchResolver` - lines 347-687

**Donor test candidates:** `tests/agents/literature/test_resolver_batch.py`

## L-04 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_resolver.py

**Receiving-source anchors:** B22, B23 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/identifier_resolver.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `b696ea5f2dcf7ae2186ab17e66ff00c4c3138893d6f40b12d88ab90615a50f11`

Retain DOI/arXiv/OpenAlex normalization; resolve metadata without treating every identifier as a new scholarly Work.

**Verified symbols:**

- `ResolverError` - lines 41-42
- `resolve(identifier: str)` - lines 45-114
- `_normalise_abstract(record: dict[str, Any])` - lines 166-177
- `_normalise_bibliographic(record: dict[str, Any])` - lines 186-251
- `_ensure_oa_fields(record: dict[str, Any])` - lines 254-273

**Donor test candidates:** `tests/kg/test_paper_resolver.py`

## L-05 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_intake.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/intake.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `57812b1aff411c938ea092a5a3b1f91ee83e2ec0590739a7d0ae35ed0d52bce5`

Retain claimed-versus-verified identity, PDF/title matching and historical filename fallbacks. Never let PDF headers override verified bibliographic records silently.

**Verified symbols:**

- `IntakeReport` - lines 123-158
- `pdf_first_pages_text(path: Path, pages: int=_DOI_SEARCH_PAGES)` - lines 161-197
- `extract_doi_candidates(text: str, *, limit: int=5)` - lines 200-223
- `filename_doi_candidates(filename: str, *, limit: int=4)` - lines 226-287
- `extract_doi_from_pdf(path: Path)` - lines 290-295
- `title_matches_pdf(title: str, text: str, *, threshold: float=0.6)` - lines 298-319
- `cache_path_for(doi: str)` - lines 322-330
- `resolve_by_filename(candidates: list[str], filename: str)` - lines 338-372
- `resolve_asserted(doi: str, *, fetch: Any=None)` - lines 375-428
- `resolve_verified(candidates: list[str], pdf_text: str, *, fetch: Any=None)` - lines 431-483
- `prepare_local_pdf(path: Path, *, apply: bool=False, force: bool=False, fetch: Any=None, verify: str='openalex', doi: str='')` - lines 553-737

**Donor test candidates:** `tests/kg/test_offline_ingest.py`, `tests/kg/test_paper_intake.py`

## L-06 - als_knowledge_agent/src/als_knowledge_agent/graph/ingested_papers.py

**Receiving-source anchors:** B23 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/ingested.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `33ccd7f27d3d9e4ed331a77990b99f049c9154067a0e025b0eb39f1535ce32e0`

Track staged/extracted/published states separately; skip decisions keyed by source+pipeline revision, not DOI alone.

**Verified symbols:**

- `IngestedLookup` - lines 51-56
- `doi_key(value: str)` - lines 59-69
- `graph_ingested_keys(store: Any)` - lines 72-85
- `staged_keys(root: Path | None)` - lines 100-115
- `IngestedPapers` - lines 118-151

**Donor test candidates:** `tests/kg/test_already_ingested.py`

## L-07 - als_knowledge_agent/src/als_knowledge_agent/graph/bibliography_backfill.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/bibliography_backfill.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `9eb2d04ad4ca867d0425a3341bde6c046c4911506995eb213580986bfe9f96af`

Keep non-destructive bibliography revisions, source assertions, provenance and dry-run diffs.

**Verified symbols:**

- `normalise_record(source: str, record: dict[str, Any])` - lines 164-178
- `record_digest(source: str, record: dict[str, Any])` - lines 181-184
- `needs_backfill(entity: dict[str, Any])` - lines 187-206
- `BackfillResult` - lines 210-223
- `backfill_publication(publication_id: str, resolved: _ResolvedLike, *, dry_run: bool=True, newest: dict[str, Any] | None=None, client: Any=None)` - lines 226-298

**Donor test candidates:** `tests/kg/test_bibliography_backfill.py`

## L-08 - src/forge/kg/bibliography.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/bibliography_projection.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `db1610236633798fdcc61a174fe96d1a9497aa9b5fafcb01a1743d3ad9caf97b`

Read effective bibliography without overwriting immutable source metadata; ensure F2W WorkVersion mapping retains originals.

**Verified symbols:**

- `is_placeholder_name(name: object, doi: object='')` - lines 75-85
- `newest_bibliography_link(rows: Iterable[dict[str, Any]], subject_id: str)` - lines 88-111
- `effective_publication(name: str, properties: dict[str, Any], record: dict[str, Any] | None)` - lines 114-144
- `author_names(authors: object)` - lines 147-161

**Donor test candidates:** `tests/kg/test_bibliography_backfill.py`

## L-09 - src/forge/agents/literature/papers.py

**Receiving-source anchors:** B07 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/papers.py`
**Disposition:** unify | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4ab9d40778e9aa3505e1e9c3bcb7aeaafc5d53979530ec5ff68a498f44d52208`

Keep public reading wrappers, but delegate download/parse to a single durable document service. Do not preserve a second conflicting temporary paper cache.

**Verified symbols:**

- `download_pdf_by_doi(doi: str, *, work: dict[str, Any] | None=None)` - lines 99-157
- `parse_pdf_to_markdown(pdf_path: Path)` - lines 160-176
- `extract_sections(markdown: str)` - lines 179-183
- `read_paper(doi: str, *, work: dict[str, Any] | None=None)` - lines 186-216
- `read_paper_section(doi: str, section: str, *, max_chars: int=8000)` - lines 219-251

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## L-10 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py

**Receiving-source anchors:** B07 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/{cache,download,parsers,papers}.py`
**Disposition:** split-adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `63719198999015c41cd2e9ad98a48b83a5daa4a4c3cd5744d4114210a1a27c5a`

Canonical durable pipeline: checksums, old filename compatibility, parser versions, figures/tables and fallback. Harden redirect/DNS checks and streaming byte limits. Do not migrate cache paths without asset-reference mapping.

**Verified symbols:**

- `sha256_of(path: Path)` - lines 74-95
- `read_manifest` - lines 176-194
- `record_in_manifest(pdf_path: Path, *, source_url: str | None=None, digest: str | None=None)` - lines 197-265
- `duplicate_stems(digest: str)` - lines 268-278
- `_cached_pdf_for(doi: str)` - lines 324-367
- `_download_by_doi(doi: str, *, work: dict | None=None)` - lines 442-555
- `_fetch_pdf(url: str, dest: Path)` - lines 568-616
- `is_pdf(path: Path)` - lines 619-637
- `_parse_pdf_with_pymupdf(pdf_path: Path, image_dir: Path)` - lines 729-769
- `PublicationParser` - lines 890-902
- `DoclingParser` - lines 922-1171
- `PyMuPDFParser` - lines 1174-1205
- `parse_pdf(pdf_path: Path, parsers: tuple[PublicationParser, ...] | None=None)` - lines 1213-1325
- `_parse_pdf_to_markdown(pdf_path: Path)` - lines 1328-1348
- `_extract_sections(markdown: str)` - lines 1351-1387

**Donor test candidates:** `tests/kg/test_docling_converter_is_reused.py`, `tests/kg/test_docling_parse.py`, `tests/kg/test_paper_cache_is_durable.py`, `tests/kg/test_paper_cache_manifest.py`

## L-11 - als_knowledge_agent/src/als_knowledge_agent/graph/parsed_document.py

**Receiving-source anchors:** B18, B19 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/parsed_document.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `dd8942b71d135cf07ab5b0ba35dd31290a6ca95f1ea4b7af7d534954518f5965`

Keep parser DTOs distinct from scientific ontology. Preserve coordinate systems, page numbering, caption/section IDs and parser metadata.

**Verified symbols:**

- `without_embedded_images(text: str)` - lines 56-68
- `BoundingBox` - lines 81-104
- `ParsedFigure` - lines 108-143
- `figure_geometry_props(figure: dict[str, Any])` - lines 146-177
- `ParsedSection` - lines 181-194
- `ParsedTable` - lines 198-211
- `ParsedDocument` - lines 215-291
- `ParsedDocument.as_legacy_dict(self)` - lines 242-291
- `document_from_dict(data: dict[str, Any])` - lines 294-314

**Donor test candidates:** `tests/kg/test_figure_geometry_is_stamped.py`, `tests/kg/test_parser_version_is_stamped.py`

## L-12 - als_knowledge_agent/src/als_knowledge_agent/graph/parser_pool.py

**Receiving-source anchors:** B13, B18 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/parser_pool.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `a2f2b093bdab14bc5b84403ec913f6c0e60642cd8807c982866a70dbdd10e02c`

Bound workers and memory; timeout/recycle stuck parsers. A subprocess in the same environment does not solve dependency conflicts; support separate executable only when needed.

**Verified symbols:**

- `ParserProcess` - lines 20-85
- `ParserPool` - lines 88-116
- `parse_isolated(pdf: Path, interpreter: str)` - lines 123-137

**Donor test candidates:** `tests/kg/test_parser_pool.py`

## L-13 - als_knowledge_agent/src/als_knowledge_agent/graph/parser_worker.py

**Receiving-source anchors:** B13 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/workers/parser_worker.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6396e4700a1f9cf3de6794b8f602e9618f6842edc09083a39b5c02e8a77bd2ac`

Version the parser process protocol; accept whitelisted parse jobs and artifact paths, not arbitrary Python commands.

**Verified symbols:**

- `serve` - lines 20-44
- `main` - lines 47-62

**Donor test candidates:** `tests/kg/test_parser_pool.py`

## L-14 - als_knowledge_agent/src/als_knowledge_agent/graph/reference_parsing.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/references.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ae86cc8311d212c4f962e7e30e0dfd0ffb6fb99481ddd495d574ec13c5f55446`

GROBID is optional; preserve reference source text and unresolved citation states.

**Verified symbols:**

- `ParsedReference` - lines 66-88
- `parse_references_tei(tei_xml: bytes | str)` - lines 157-199
- `grobid_references(pdf: Path, *, timeout: float=180.0, cache_dir: Path | None=None, http: Any=None)` - lines 208-258

**Donor test candidates:** `tests/kg/test_reference_parsing.py`

## L-15 - als_knowledge_agent/src/als_knowledge_agent/graph/section_projection.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/sections.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4b61cb04899a7fdd347a5d66e076ac40ce9ff64372db8322ddaae37639ea4797`

Persist and select parser-versioned sections through GraphPort; no hidden donor singleton.

**Verified symbols:**

- `section_records(parsed: dict[str, Any])` - lines 13-39
- `persist_sections(store: Any, pub_id: str, parsed: dict[str, Any], *, run_id: str | None=None, link: bool=True)` - lines 42-119
- `active_sections(rows: list[dict[str, Any]])` - lines 122-134

**Donor test candidates:** `tests/kg/test_sections_by_edge.py`

## L-16 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_context.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/paper_context.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `1fd19639c9704a700f49ce21a28035172799e7389be074cf48c9ea89157f8fdd`

Preserve section and caption adjacency for evidence; cap context and keep identifiers for later retrieval.

**Verified symbols:**

- `caption_figure_number(caption: str)` - lines 79-92
- `citing_sentence(number: str, sections: list[str])` - lines 95-130
- `render_paper_context(*, title: str='', abstract: str='', domain: str='', techniques: list[str] | None=None, citing: str='')` - lines 133-183
- `figure_publications(predicate: str='schema:image')` - lines 186-220
- `sections_by_publication(store: Any)` - lines 223-256

**Donor test candidates:** `tests/kg/test_paper_context.py`

## L-17 - als_knowledge_agent/src/als_knowledge_agent/graph/citation_links.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/citations.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `1caa5164a9f9a815b8de7e82a737350ead02d8384b0645452c89a157e637a8f5`

Keep text-grounded citations distinct from externally enriched references. Missing cited works remain unresolved references, not fabricated ingested papers.

**Verified symbols:**

- `dois_in_text(text: str)` - lines 50-57
- `link_citations_from_sections(store: Any, *, apply: bool=False)` - lines 60-152
- `link_citations_from_referenced_works(store: Any, *, apply: bool=False)` - lines 212-282

**Donor test candidates:** `tests/kg/test_citation_endpoints.py`, `tests/kg/test_citation_links.py`

## L-18 - als_knowledge_agent/src/als_knowledge_agent/graph/licensing.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/licensing.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `fcb1cc0f9e63555cb539000f2ff642edeef6150c6e92c49fd129c4a1ab9310b6`

Access, caching and redistribution are different permissions. Unknown license must not enable public PDF/figure redistribution.

**Verified symbols:**

- `redistribution_terms(work: dict[str, Any])` - lines 39-83
- `unknown_redistribution_terms` - lines 86-103

**Donor test candidates:** `tests/kg/test_figure_redistribution_terms.py`

## L-19 - src/forge/simulation/net_guard.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/security/network.py`
**Disposition:** extract-utility | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `db73dc21b2045183088c9d004317e8b93f497e5eddeab801e61550d324cea72d`

Exception to folder exclusion: pure network safety is useful to paper download. Revalidate every redirect, resolve addresses safely, impose time/size limits and egress policy. No simulation dependency.

**Verified symbols:**

- `is_public_host(hostname: str)` - lines 19-55
- `assert_public_http_url(url: str)` - lines 58-75

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## J-01 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_staging.py

**Receiving-source anchors:** B15, B24 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/artifacts/paper_packages.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `779af623fb9cf824e874a3c6b5d5b7b51e327d63c9217e84841d4bf31c164a27`

Preserve content-addressed package/assets hashes and atomic writes. Package completeness is stage-specific, not scientific approval. Review fcntl/O_DIRECTORY portability.

**Verified symbols:**

- `staging_directory` - lines 23-27
- `save_package(*, work: dict[str, Any], parsed: dict[str, Any], extraction: PaperExtraction | None, source: Path, model: str | None, pipeline: dict[str, Any], timings: dict[str, float], root: Path | None=None)` - lines 58-147
- `restore_extraction(data: dict[str, Any])` - lines 150-166
- `load_package(path: Path)` - lines 169-207
- `import_package(path: Path, *, publish: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]])` - lines 210-240

**Donor test candidates:** `tests/kg/test_paper_staging.py`

## J-02 - als_knowledge_agent/src/als_knowledge_agent/graph/import_staged.py

**Receiving-source anchors:** B24, B26 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/staged_import.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `cdb605d2b18d8ba9a67109805ac6ed885f0ed880f4635eb7de8a6bdf0d018c82`

Preserve explicit dry-run/apply, single writer, import receipts and resume. Add approval binding to source+schema+target+plan hash; no automatic global publication.

**Verified symbols:**

- `priority_index` - lines 24-43
- `pending_packages(root: Path, seen: dict[Path, tuple[int, int]], priorities: dict[str, tuple[int, int, int]])` - lines 46-69
- `publish(payload: dict[str, Any])` - lines 72-84
- `main(directory: Path | None=typer.Argument(None, help='Staged papers directory.'), apply: bool=typer.Option(False, '--apply/--dry-run', help='Publish to Tiled; default validates only.'), limit: int=typer.Option(0, min=0, help='Maximum packages; zero means all.'), watch: bool=typer.Option(False, help='Continue importing new completed packages.'), poll_seconds: float=typer.Option(30, min=5, help='Seconds between scans when watching.'))` - lines 88-174

**Donor test candidates:** `tests/kg/test_staged_import_queue.py`

## J-03 - als_knowledge_agent/src/als_knowledge_agent/graph/staged_import_status.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/import_status.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `9e5cb553586445d1f637adbcb957aba2ee7d9575fa77371403683894d2415e3f`

Keep import status distinct from extraction success and parser completion.

**Verified symbols:**

- `read_staged_import_status(root: Path, *, now: float | None=None)` - lines 59-93

**Donor test candidates:** `tests/kg/test_staged_import_status.py`

## J-04 - als_knowledge_agent/src/als_knowledge_agent/graph/ingest_journal.py

**Receiving-source anchors:** B02, B28, B29 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/ingest_journal.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `52368e5058354047fb602bdfb4f9a3096aea4555dd4d6cdcf02487757efc4442`

Use owned leases, heartbeats and input/pipeline fingerprints. Retries are at-least-once with idempotent effects; Tiled checkpoint adoption must not imply JSON publication.

**Verified symbols:**

- `IngestJournal` - lines 62-279
- `IngestJournal.acquire(self, config: dict[str, Any], owner: str, lease_seconds: float=120)` - lines 98-124
- `IngestJournal.heartbeat(self, key: str, owner: str)` - lines 126-133
- `IngestJournal.adopt_tiled_checkpoints(self, key: str, config: dict[str, Any])` - lines 135-164
- `IngestJournal.finish(self, key: str, owner: str, state: str)` - lines 166-171
- `IngestJournal.checkpoint(self, key: str, operation: str, stage: str)` - lines 173-179
- `IngestJournal.completed_count(self, key: str)` - lines 181-189
- `IngestJournal.completion_snapshot(self, key: str, *, recent: list[dict[str, Any]] | None=None)` - lines 191-234
- `IngestJournal.save(self, key: str, operation: str, stage: str, result: dict | None, error: str='')` - lines 236-245
- `IngestJournal.latest(self)` - lines 247-257
- `IngestJournal.recoverable(self)` - lines 259-270
- `IngestJournal.request_stop(self)` - lines 272-274
- `IngestJournal.stopped(self, key: str)` - lines 276-279

**Donor test candidates:** `tests/kg/test_concurrent_folder_ingest.py`, `tests/kg/test_ingest_checkpoint_progress.py`

## J-05 - als_knowledge_agent/src/als_knowledge_agent/graph/folder_ingest.py

**Receiving-source anchors:** B36 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/folder_ingest.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `7c97f43863d457c2fa2c3ef4efd46aae08bcd0bc2a940606d966dac908a51d2a`

Retain injected ingest_one callback and file verdicts. Enforce roots/symlinks, cancellation, bounded concurrency, per-job budgets and namespace isolation.

**Verified symbols:**

- `allowed_roots` - lines 57-77
- `require_allowed_folder(path: str)` - lines 80-101
- `FileVerdict` - lines 109-129
- `FolderReport` - lines 133-151
- `list_pdfs(folder: Path, *, glob: str='*.pdf', limit: int=0)` - lines 154-157
- `ingest_folder(folder: str, *, apply: bool=False, glob: str='*.pdf', limit: int=0, force: bool=False, offline: bool=False, ingest_one: Callable[[str], dict[str, Any]] | None=None, files: list[Path] | None=None, ingest_on_cache_conflict: bool=False, dois: dict[Path, str] | None=None, journal: Any=None, job_id: str='', concurrency: int=1, on_active: Callable[[list[str]], None] | None=None, already_ingested: IngestedLookup | None=None)` - lines 160-461

**Donor test candidates:** `tests/kg/test_folder_ingest.py`, `tests/kg/test_folder_ingest_quota_pause.py`

## J-06 - als_knowledge_agent/src/als_knowledge_agent/graph/corpus_manifest.py

**Receiving-source anchors:** B23 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/corpus_manifest.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `3148d40d599c23629cf48e26f3c90e015910483a20ab9c797ac4279f4fe64ca4`

Merge with branch-native Work/corpus semantics; configurable priority policy, not hardcoded cross-domain scattering relevance.

**Verified symbols:**

- `CorpusQueue` - lines 40-46
- `load_corpus_queue(root: str | Path, scan_path: str | Path, metadata_path: str | Path, *, priority: str='source')` - lines 55-125

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## J-07 - als_knowledge_agent/src/als_knowledge_agent/graph/corpus_index.py

**Receiving-source anchors:** B23 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/corpus_index.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0afd027cadac4563fec848a2f488b3f72ddafbee4bc6a7ac92f56aee378d1da6`

Retain local index durability and incremental scan; isolate optional scanner and do not count unavailable scans as completed.

**Verified symbols:**

- `ScannerUnavailable` - lines 85-86
- `load_scanner` - lines 108-122
- `scan_one(path: str, max_pages: int=DEFAULT_MAX_PAGES)` - lines 125-127
- `domain_catalogue` - lines 138-145
- `folder_rows(con: sqlite3.Connection, folder: Path)` - lines 158-163
- `index_folder(folder: Path, *, con: sqlite3.Connection | None=None, workers: int=4, max_pages: int=DEFAULT_MAX_PAGES, scan: Callable[[str, int], dict[str, Any]] | None=None)` - lines 181-287
- `overview(con: sqlite3.Connection, folder: Path)` - lines 304-324
- `SearchResult` - lines 351-362
- `search(con: sqlite3.Connection, folder: Path, *, query: str='', domains: Iterable[str]=(), match: Literal['any', 'all']='any', min_hits: int=1, ingested: Any=None, exclude_ingested: bool=True)` - lines 365-448

**Donor test candidates:** `tests/kg/test_corpus_index.py`, `tests/kg/test_index_durability.py`

## J-08 - als_knowledge_agent/src/als_knowledge_agent/graph/ingest_telemetry.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/telemetry.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `7df55299b5852f747932ad3e74f33f86a20cdc704c76e212345e3a628d98b5d2`

Explicit progress state includes staged, reviewed, published, failed and paused counts. No misleading one-number complete state.

**Verified symbols:**

- `collect_models(service_up: dict[str, bool], *, job_model: str='')` - lines 126-161
- `choose_job(*candidates: tuple[str, dict[str, Any] | None])` - lines 164-185
- `annotate_stall(job: dict[str, Any] | None, *, now: float | None=None)` - lines 188-204
- `read_graph_snapshot(db: Path)` - lines 307-365
- `build_telemetry(*, db: Path, jobs: tuple[tuple[str, dict[str, Any] | None], ...])` - lines 431-458

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## J-09 - als_knowledge_agent/src/als_knowledge_agent/graph/child_process.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/process_support.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `aaee302cf76918ce5a68f50cb22f3bf7a75cb02b040de31fff9a21e0451ef706`

Use only for owned worker processes; sanitize logs, allowlisted commands, bounded tails and no arbitrary shell execution.

**Verified symbols:**

- `write_text_atomic(path: Path, text: str)` - lines 27-42
- `pid_alive(pid: object)` - lines 45-62
- `pid_command(pid: int)` - lines 65-74
- `tail_lines(path: Path, n: int=12, *, max_bytes: int=TAIL_BYTES, max_chars: int=TAIL_LINE_CHARS)` - lines 77-89

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## J-10 - als_knowledge_agent/src/als_knowledge_agent/graph/corpus_refresh.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/corpus_refresh.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `046bb544a58ceb3965123ffc1cbb60670d31897eec5a9bc9a0c312b8c95ead36`

Compute scoped refresh plan first. Preserve earlier source/extraction versions; do not sweep shared FORGE entities or erase annotations.

**Verified symbols:**

- `RefreshOutcome` - lines 60-73
- `already_parsed_by(publication_entity_id: str, parser: str, entities_by_id: dict[str, Any])` - lines 76-111
- `publications_with_dois(limit: int=100000)` - lines 114-135
- `child_entity_ids(publication_entity_id: str)` - lines 138-150
- `link_child(store: Any, publication_entity_id: str, child_node_id: str, predicate: str, evidence: str='')` - lines 167-193
- `refresh_publication(store: Any, publication: dict[str, Any], *, parse: Any, extract_sections: Any, find_pdf: Any, stable_id: Any, figure_terms: Any, apply: bool=True, skip_if_parser: str='', entities_by_id: dict[str, Any] | None=None)` - lines 196-382

**Donor test candidates:** `tests/kg/test_corpus_refresh.py`

## J-11 - als_knowledge_agent/src/als_knowledge_agent/graph/enrichment_agent.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/jobs/{topic_sweep,pdf_parse}.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ef0fd37a301046ebc033b0520960a1970289ce57b8abf8e9795ea88c86bdfa8f`

Extract paper-only workflows and stop/reset controls; remove simulation/catalog enrichment and global process state.

**Verified symbols:**

- `run_topic_sweep(query: str, max_papers: int=50, year_from: int | None=None)` - lines 2221-2405
- `run_pdf_parse(top_k: int=10, query_filter: str | None=None, oa_only: bool=True, extract_entities: bool=True, model: str | None=None, journal: Any=None, job_id: str='')` - lines 2597-2884

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## J-12 - als_knowledge_agent/src/als_knowledge_agent/graph/service_health.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/application_health.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `28e911bfc56c7d085c26b8707c69a539b999881a7c9d2b862b49246df3e3f9c3`

Check only capabilities needed for the requested operation; JSON literature QA must work without FORGE reduction/Tiled/simulation services.

**Verified symbols:**

- `check_services` - lines 103-113
- `require_services(*, exit_code: int=2)` - lines 127-145

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## E-01 - als_knowledge_agent/src/als_knowledge_agent/graph/pdf_extraction.py

**Receiving-source anchors:** B11, B13 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/strategies/forge_paper.py`
**Disposition:** adapt-strategy | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `b83bc6c5bc84b25fc0be2d07ccabcc063f59a330c537305e63935474f7455a2b`

Preserve section keyword/refinement path as a strategy beside current F2W extraction. Replace donor entity labels with a validated mapping; benchmark before choosing defaults. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `Hit` - lines 40-55
- `RefinedExtraction` - lines 59-88
- `ResolvedTerm` - lines 92-114
- `SectionEntities` - lines 118-134
- `PaperExtraction` - lines 138-163
- `extract_keywords(section_text: str)` - lines 227-270
- `llm_refine(section_text: str, candidate_hits: list[Hit], model: str | None=None, *, thinking_budget: int | None=None)` - lines 316-487
- `_resolve(vocab: Vocabulary, surface: str, category: str, *, software: str | None=None, definitions: dict | None=None)` - lines 497-640
- `extract_paper(sections: dict[str, str], model: str | None=None, cache_dir: str | None=None, publication_id: str | None=None, thinking_budget: int | None=None, promote_vocabulary: bool=True)` - lines 643-1016
- `entity_cache_directory` - lines 1019-1021

**Donor test candidates:** `tests/kg/test_extraction_canonicalisation.py`, `tests/kg/test_pdf_extraction.py`

## E-02 - als_knowledge_agent/src/als_knowledge_agent/graph/scientific_records.py

**Receiving-source anchors:** B15, B16, B17, B21, B31 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/record_validation.py`
**Disposition:** adapt-contract | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `59f457926b3767f93b3ffe63bd63f9f0b20a79d94e15a34aa2952239cec7e3ac`

Source-defined module appears not wired to donor live production ingestion. Deliberately integrate and test; generic string conditions are not typed RSoXS slots. Status reported/cited/derived/assumed is separate from semantic class.

**Verified symbols:**

- `StrictRecord` - lines 10-11
- `EvidenceLocator` - lines 14-22
- `SampleRecord` - lines 25-34
- `MeasurementRecord` - lines 37-45
- `ScientificObservation` - lines 48-64
- `ExperimentChunk` - lines 67-73
- `ValidationIssue` - lines 76-82
- `ValidatedExperimentChunk` - lines 85-92
- `validate_experiment_chunk(proposal: ExperimentChunk, source: str)` - lines 101-155

**Donor test candidates:** `tests/kg/test_science_ingestion_contracts.py`, `tests/kg/test_scientific_records.py`

## E-03 - als_knowledge_agent/src/als_knowledge_agent/graph/quote_location.py

**Receiving-source anchors:** B07, B20 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/evidence/quote_location.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `743e301187142af3affe3e3f5d9525a2684099c0f2b55f3fcbba7ac58012bebb`

Record exact versus normalized match, offsets, page, parser revision and source checksum; never invent a quote match.

**Verified symbols:**

- `QuoteLocation` - lines 37-54
- `QuoteLocation.is_verbatim(self)` - lines 45-54
- `locate_quote(quote: str, section_text: str)` - lines 141-173

**Donor test candidates:** `tests/kg/test_quote_found_in_any_section.py`, `tests/kg/test_quote_location.py`

## E-04 - src/forge/agents/literature/grounding.py

**Receiving-source anchors:** B07, B18, B20, B21 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/evidence/grounding.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `df64943645e4ef16135b9bc84ce46f83348d519c88d89823abbd0a4e2f516026`

Use alongside locator, not as proof that a scientific inference is correct. Preserve distinction between lexical source support and entailment.

**Verified symbols:**

- `normalize(text: str)` - lines 12-14
- `source_support(source: str, quote: str, value: float | None=None, unit: str='', *, sample_id: str='', processing_state: str='')` - lines 17-70

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## E-05 - src/forge/agents/literature/units.py

**Receiving-source anchors:** B17 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/science/units.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `5ccdfc2727772285977b2dabe7bfc5dd46ac63bf3729d0c7d7bc2a53888c8f07`

Scope to paper-reported values and dimensional checks. Preserve raw value/unit and conversion provenance; no simulation/beamline optimization.

**Verified symbols:**

- `canonical_unit(unit: str)` - lines 131-154
- `to_nm(value: float, unit: str, where: str='')` - lines 168-170
- `to_ev(value: float, unit: str, where: str='')` - lines 173-175
- `to_deg(value: float, unit: str, where: str='')` - lines 178-180
- `length_in_nm(value: float, unit: str, where: str='')` - lines 183-195
- `q_in_nm_inv(q_value: float, q_unit: str)` - lines 198-217
- `q_to_d_nm(q_value: float, q_unit: str)` - lines 220-229
- `unit_dimension(unit: str)` - lines 270-295
- `dimension_mismatch(quantity_name: str, unit: str)` - lines 298-330

**Donor test candidates:** `tests/agents/literature/test_units.py`, `tests/analysis/figure_extraction/test_units.py`

## E-06 - src/forge/agents/literature/structured_output.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/structured_output.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `66bd785c66e7128519f11b9748dddd9cd00ab98debf4215938fcafd188b447b7`

Inject model provider and concurrency limiter; stage-owned retries, output validation and cancellation. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `vllm_extraction_options(model: str, schema: dict[str, Any], *, thinking_budget: int | None=None)` - lines 10-42
- `invoke_extraction(model: Any, prompt: Any)` - lines 57-61

**Donor test candidates:** `tests/agents/literature/test_structured_output.py`

## E-07 - src/forge/agents/literature/publication.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/publication_adapter.py`
**Disposition:** adapt-contract | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `eeb22eb0ce1429d327730295d56306a7c313e1cd2f9abae65ca0874ce5d46b14`

Translate literature values/provenance into F2W schema; no second authoritative ontology or parallel publication identity.

**Verified symbols:**

- `ExtractedQuantity` - lines 38-117
- `PublicationRecord` - lines 121-218

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## E-08 - src/forge/agents/literature/extraction.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/strategies/paper_structure.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `28d4c9342323baf1a5d59ae32f25121da584dd7da95cc458d0b862a7c9ac9b4b`

Preserve paper observations and contexts only. Remove BL733 geometry defaults and physics mapping imports; SimulationCandidate, gate_and_map and simulation feasibility remain FORGE. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `ObservationContext` - lines 101-111
- `ExtractedValue` - lines 114-134
- `ReportedPeak` - lines 137-164
- `PaperStructureExtraction` - lines 167-182
- `attributed_phase_mentions(text: str)` - lines 330-347
- `names_unsimulatable_phase(text: str)` - lines 350-397
- `extract_structure_from_text(text: str, *, title: str='', model: Any, max_chars: int=8000, max_retries: int=2)` - lines 652-768

**Donor test candidates:** `tests/agents/literature/test_extraction.py`

## E-09 - als_knowledge_agent/src/als_knowledge_agent/graph/paper_simulation.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/evidence/{records,persistence}.py`
**Disposition:** extract-paper-only | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `641c8257fe26329e8bfd25dbe3bc43a256c990093b17274deded84167e8e0b01`

Mixed-file exception: extract literature evidence persistence from the simulation module. Computed curves, fits, simulated claims and orchestration stay FORGE. Avoid importing this module in F2W.

**Verified symbols:**

- `_record_reported_quantities(store: Any, pub_path: str, extraction: Any, *, identifier: str, title: str, aliases: tuple[str, ...]=(), model_name: str='', source_text: str='')` - lines 2506-2580
- `_record_evidence(store: Any, sim_node: str | None, pub_path: str, candidate: Any, quantities: list[dict[str, Any]], *, aliases: tuple[str, ...]=())` - lines 2861-3078
- `_locate_in_any_section(store: Any, pub_path: str, quote: str, *, aliases: tuple[str, ...]=(), sections: list[dict[str, Any]] | None=None)` - lines 2771-2858
- `_section_text_for(store: Any, section_node: str | None)` - lines 2732-2750
- `_section_page_for(store: Any, section_node: str | None)` - lines 2753-2768
- `_stable_evidence_id(pub_path: str, text: str)` - lines 3081-3087

**Donor test candidates:** `tests/kg/test_evidence_holds_quotes.py`, `tests/kg/test_evidence_links_to_its_section.py`

## C-01 - als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/vocabulary.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `9350c9f2180621f465751ad7dc6f5d5d813b0fad4eaff0eb617c0873a0dc7835`

Use source vocab as versioned proposal, with one approved alias authority. Keep type restrictions and exact structural identity; stable F2W IDs must not be regenerated from labels.

**Verified symbols:**

- `entity_slug(text: str)` - lines 56-71
- `VocabEntry` - lines 75-88
- `Vocabulary` - lines 129-352
- `Vocabulary.match(self, term: str, category: str | None=None)` - lines 173-192
- `Vocabulary.match_smiles(self, smiles: str)` - lines 194-214
- `Vocabulary.match_within(self, term: str, category: str | None=None)` - lines 216-312
- `Vocabulary.entries_for(self, category: str)` - lines 314-315
- `Vocabulary.technique_profile(self, terms: Iterable[str])` - lines 317-338
- `Vocabulary.taxonomy(self)` - lines 340-352
- `load_vocabulary(path: Path | None=None)` - lines 394-410

**Donor test candidates:** `tests/kg/test_vocabulary.py`, `tests/kg/test_vocabulary_v3.py`

## C-02 - als_knowledge_agent/src/als_knowledge_agent/graph/merge_safety.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/merge_safety.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0d5d52b8e5459c7b8c886397cf8717a2c0e081af371f29f8a22636c57138a0dc`

Mandatory server-side gate for every apply path, including bulk cleanup, imports and human edits. Human approval does not bypass type/identity invariants.

**Verified symbols:**

- `MergeVerdict` - lines 136-140
- `may_merge(canonical: str, alias: str)` - lines 328-429

**Donor test candidates:** `tests/kg/test_merge_safety.py`

## C-03 - als_knowledge_agent/src/als_knowledge_agent/graph/chemical_identity.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/chemical_identity.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `2bae91ed54b8b6750aba57d307d6b84a4ad0de644ccdca43186d154b6997ba8f`

Optional RDKit/PubChem; preserve ambiguity and chemical specificity; ChEBI behavior from F2W retained. Similar structure does not imply identical entity.

**Verified symbols:**

- `canonical_smiles(smiles: str)` - lines 29-50
- `structure_slug(smiles: str)` - lines 53-56
- `pubchem_structure(name: str)` - lines 59-70
- `molecular_name_candidate(name: str)` - lines 163-172
- `formula_ambiguous(smiles: str)` - lines 216-254
- `plain_inorganic_name(label: str, smiles: str)` - lines 309-349
- `structure_identifies(label: str, smiles: str)` - lines 352-375

**Donor test candidates:** `tests/kg/test_chemical_identity_forms.py`

## C-04 - als_knowledge_agent/src/als_knowledge_agent/graph/term_embeddings.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/embeddings.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `c8da8df1f476e27e88385bca4cd541dd0d35d7f71b66e909d3a3e7aa03bf4253`

Cache by input and model/revision. Bound requests and track failure; do not convert unavailable backend into empty successful candidates. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `EmbeddingUnavailable` - lines 63-64
- `EmbeddingBatch` - lines 68-81
- `configured_model` - lines 84-86
- `embed_texts(texts: list[str], *, model: str | None=None, batch_size: int=DEFAULT_BATCH_SIZE, timeout_sec: float=300.0)` - lines 114-188
- `EmbeddingCache` - lines 243-367
- `EmbeddingCache.get_many(self, model: str, texts: list[str])` - lines 308-336
- `EmbeddingCache.put_many(self, model: str, dim: int, items: list[tuple[str, list[float]]])` - lines 338-367
- `embed_texts_cached(texts: list[str], *, model: str | None=None, cache: EmbeddingCache | None=None, batch_size: int=DEFAULT_BATCH_SIZE, timeout_sec: float=300.0)` - lines 370-462

**Donor test candidates:** `tests/kg/test_term_embedding_cache.py`, `tests/kg/test_term_embeddings.py`

## C-05 - als_knowledge_agent/src/als_knowledge_agent/graph/term_grouping.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/grouping.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0f7a6e133683ca82775fdc9f8ed7c03aab53fd37c3090e2190fad39628636d58`

Retain contextual grouping; inspect all-pairs memory scaling and cap/block/ANN candidate generation without silently changing grouping semantics.

**Verified symbols:**

- `TermGroup` - lines 59-79
- `embedding_text(surface: str, snippets: list[dict[str, str]])` - lines 82-99
- `cosine(a: list[float], b: list[float])` - lines 102-109
- `_similarity_matrix(vectors: list[list[float]])` - lines 112-129
- `group_terms(terms: list[dict[str, Any]], vectors: list[list[float]], *, threshold: float=DEFAULT_THRESHOLD)` - lines 132-229
- `summarize(groups: list[TermGroup])` - lines 232-246

**Donor test candidates:** `tests/kg/test_term_grouping.py`

## C-06 - als_knowledge_agent/src/als_knowledge_agent/graph/group_adjudication.py

**Receiving-source anchors:** B14 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/adjudication.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4b144c83d3081e163e5ddc71fc71285cc79bc5c86bbb3858706663d70666deb0`

Keep evidence and abstention; group verdict is a proposal until deterministic merge safety and permission gates pass. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `GroupVerdict` - lines 71-118
- `GroupVerdict.auto_applicable(self)` - lines 90-105
- `build_prompt(group: TermGroup, contexts: dict[str, list[str]])` - lines 121-132
- `adjudicate_group(group: TermGroup, contexts: dict[str, list[str]], *, judge: Callable[[str], str] | None=None)` - lines 148-209

**Donor test candidates:** `tests/kg/test_group_adjudication.py`

## C-07 - als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_types.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/campaign/models.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `49175f9c8a715708bed50cf76943a3f2bd10e4db7c91256a404f9dc532983491`

Preserve structured evidence/menu/verdict records with versioned IDs and explicit decision provenance.

**Verified symbols:**

- `term_key(category: str, folded: str)` - lines 61-63
- `Snippet` - lines 67-70
- `MenuEntry` - lines 74-78
- `TermEvidence` - lines 82-99
- `TermVerdict` - lines 103-125
- `Decision` - lines 129-150
- `RoundRecord` - lines 154-168

**Donor test candidates:** `tests/kg/test_term_campaign_types.py`

## C-08 - als_knowledge_agent/src/als_knowledge_agent/graph/term_evidence.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/campaign/evidence.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `9a3026cab08644ded540c0c6b96822f75cf2c67ce2f6b0a528b3dfba31556c55`

Decouple graph reads, retain source contexts, incomplete-read errors and evidence-constrained canonical choices.

**Verified symbols:**

- `snippet_around(text: str, surface: str, width: int=SNIPPET_WIDTH)` - lines 72-97
- `GraphRows` - lines 101-141
- `load_rows(store: KGStore)` - lines 191-204
- `load_rows_from_catalog(db: Path)` - lines 218-265
- `repair_surface(surface: str, vocab: Vocabulary | None=None)` - lines 283-304
- `fold_terms(rows: GraphRows)` - lines 314-333
- `support(rows: GraphRows, node_ids: list[str])` - lines 336-350
- `build_menu(vocab: Vocabulary, category: str)` - lines 364-390
- `assemble(rows: GraphRows, node_ids: list[str], vocab: Vocabulary, *, k: int=5)` - lines 405-493

**Donor test candidates:** `tests/kg/test_term_evidence.py`

## C-09 - als_knowledge_agent/src/als_knowledge_agent/graph/term_quarantine.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/quarantine.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `61c3435c7e951016a75d38d8ad43591b4f2d7750e334cdb78e9e5361f60ad3ec`

Retain rejection classifications and reason codes; never drop terms without a report.

**Verified symbols:**

- `classify_surface(surface: str, vocab: Vocabulary, *, software_extra: Iterable[str]=(), generic_stoplist: Iterable[str]=())` - lines 93-142

**Donor test candidates:** `tests/kg/test_term_quarantine.py`

## C-10 - als_knowledge_agent/src/als_knowledge_agent/graph/term_resolver.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/campaign/resolver.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `a81d20285c1e805f9a939c7f3cef40954b508d882021a7c1e0980a51b5b8efd6`

Menu-constrained responses, no free-form authoritative new IDs; injected provider/budget, preserve unresolved state. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `reasoning_fields(model: str, level: Level | None, *, thinking_budget: int | None=None)` - lines 120-158
- `menu_first_schema(menu_slugs: list[str])` - lines 161-189
- `ResolverConfig` - lines 209-216
- `StructuredTermResolver` - lines 246-560
- `StructuredTermResolver.resolve(self, evidence: TermEvidence, *, round_key: str, framing: Literal['menu_first', 'evidence_first'], level: Level | None=None, thinking_budget: int | None=None, vocab: Vocabulary | None=None)` - lines 478-560

**Donor test candidates:** `tests/kg/test_term_resolver.py`

## C-11 - als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/campaign/{runner,rounds,planning}.py`
**Disposition:** split-adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `1033ec389e6d814f57b4eb8679765c8c38cfaafaef1f837b3c5dc6ab92c01789`

Retain multi-round workflow, audits, parking, plan/review semantics. Split orchestration from algorithms; no self-approval, no unapproved paid/model CLI fallback. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `CampaignRun` - lines 375-399
- `open_run(*, work: Path | None=None, campaign_id: str | None=None, catalog: Path | None=None, apply: bool=False, limit: int=0, level: str | None='none', sample: int=100, policy_path: Path | None=None, needs_catalog: bool=False)` - lines 409-448
- `campaign_status(work: Path)` - lines 608-654
- `run_collapse(run: CampaignRun, *, rows: GraphRows | None=None, vocab: Vocabulary | None=None)` - lines 1070-1166
- `run_route(run: CampaignRun)` - lines 1198-1232
- `run_measure(run: CampaignRun, *, resolver: StructuredTermResolver | None=None, vocab: Vocabulary | None=None, rows: GraphRows | None=None, levels: str='none,low,high', gold_path: Path | None=None)` - lines 1333-1431
- `run_round1(run: CampaignRun, *, resolver: StructuredTermResolver | None=None, vocab: Vocabulary | None=None, rows: GraphRows | None=None)` - lines 1460-1531
- `Vote` - lines 1543-1550
- `decide(votes: list[Vote], *, pubs: int, min_pubs_review: int, min_pubs_paid: int | None=None)` - lines 1558-1621
- `run_round2(run: CampaignRun, *, resolver: Any=None, vocab: Vocabulary | None=None, rows: GraphRows | None=None, cluster_judge: Callable[[str], str] | None=None, embed: Callable[..., Any] | None=None, codex_request: Callable[[dict[str, Any]], httpx.Response] | None=None)` - lines 1928-2106
- `run_round3(run: CampaignRun, *, resolver: _PriorAwareResolver | None=None, store: ScientificArtifactStore | None=None, vocab: Vocabulary | None=None, rows: GraphRows | None=None, control: Callable[[], dict[str, Any]] | None=None, http_post: Callable[..., httpx.Response] | None=None)` - lines 2221-2407
- `run_park(run: CampaignRun)` - lines 2437-2465
- `run_plan(run: CampaignRun, *, vocab: Vocabulary | None=None, store: KGStore | None=None)` - lines 2487-2557
- `record_review_decision(work: Path, *, term_key: str, action: str, target_slug: str | None, quarantine: str | None, vocab: Vocabulary | None=None)` - lines 2565-2641

**Donor test candidates:** `tests/kg/test_term_campaign.py`, `tests/kg/test_term_campaign_integration.py`

## C-12 - als_knowledge_agent/src/als_knowledge_agent/graph/vocab_cleanup.py

**Receiving-source anchors:** B34 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/{quality,cleanup,review}.py`
**Disposition:** split-adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `5454d8cdb0fc37c4a36ac8f06731630cd4721fe72aca3c430c9043ddd8928b8e`

Preserve reports, proposals, review digests and application functionality; graph mutations only through scoped ChangePlan with dry-run and signed approval.

**Verified symbols:**

- `read_all_nodes(store: KGStore, entity_type: str)` - lines 161-165
- `read_all_edges(store: KGStore)` - lines 168-172
- `classify_node(node: dict[str, Any], entity_type: str, vocab: Vocabulary)` - lines 254-284
- `Adjudication` - lines 319-347
- `adjudicate_term(node: dict[str, Any], entity_type: str, vocab: Vocabulary, *, judge: Callable[[dict[str, Any], str, Vocabulary], Adjudication] | None=None)` - lines 350-422
- `quality_report(store: KGStore, *, vocab: Vocabulary | None=None, edge_limit: int | None=None, include_history: bool=True)` - lines 425-640
- `format_report(report: dict[str, Any])` - lines 643-688
- `build_llm_judge(model: str, *, chat: Any | None=None)` - lines 789-896
- `propose(store: KGStore, *, model: str | None=None, vocab: Vocabulary | None=None, limit: int=0, max_workers: int=6, judge: Callable[[dict[str, Any], str, Vocabulary], Adjudication] | None=None)` - lines 941-1117
- `write_review_file(proposal: dict[str, Any], path: Path | None=None, *, stamp: str='')` - lines 1120-1147
- `merge_novel(review_path: Path, vocab_path: Path | None=None)` - lines 1150-1239
- `apply_cleanup(store: KGStore, review_path: Path, *, apply: bool=False, limit: int=0, vocab: Vocabulary | None=None)` - lines 1386-1645
- `triage_proposal(proposal: dict[str, Any], *, min_novel_edges: int=MIN_NOVEL_EDGES, min_reject_edges: int=MIN_REJECT_EDGES)` - lines 1672-1759
- `format_review_digest(triaged: dict[str, Any], limit: int=0)` - lines 1762-1828

**Donor test candidates:** `tests/kg/test_vocab_cleanup_apply.py`, `tests/kg/test_vocab_cleanup_propose.py`, `tests/kg/test_vocab_cleanup_triage.py`, `tests/kg/test_vocab_quality_report.py`

## C-13 - als_knowledge_agent/src/als_knowledge_agent/graph/vocab_migration.py

**Receiving-source anchors:** B34 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/migration.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `eadc7e5ce0bbbd73135f20ea8b594f07ca41a215cd46b4284ed6ab4d4371f7da`

Bind approved plan hash to vocabulary/source/schema revisions; block apply when corpus changed since review.

**Verified symbols:**

- `plan_migration(store: KGStore, *, vocab: Vocabulary | None=None, decisions: dict[tuple[str, str], Decision] | None=None)` - lines 269-469
- `decisions_from_plan(reviewed_plan: dict[str, Any] | None)` - lines 472-500
- `apply_migration(store: KGStore, *, apply: bool=False, limit: int=0, vocab: Vocabulary | None=None, reviewed_plan: dict[str, Any] | None=None, methods: list[str] | None=None)` - lines 632-774
- `format_digest(plan: dict[str, Any], *, sample: int=40)` - lines 777-845

**Donor test candidates:** `tests/kg/test_vocab_migration.py`

## C-14 - als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary_growth.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/growth.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `a188086025b60c3ac393e4b76fa4287188387940f978381d30e2db23614dc313`

Keep observations and withheld proposals; promotion independent from staging and requires review.

**Verified symbols:**

- `definitions(text: str)` - lines 28-65
- `withheld_promotions(path: Path | None=None)` - lines 85-115
- `VocabularyRegistry` - lines 182-452
- `VocabularyRegistry.observe(self, *, label: str, category: str, publication: str, aliases: list[str], quote: str, document_hash: str)` - lines 198-237
- `VocabularyRegistry.promote(self, base: Vocabulary)` - lines 239-356
- `VocabularyRegistry.entries(self, base: Vocabulary)` - lines 358-452

**Donor test candidates:** `tests/kg/test_vocabulary_growth.py`

## C-15 - als_knowledge_agent/src/als_knowledge_agent/graph/vocabulary_audit.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/audit.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4452b49b2193e582fdbed46a962bfaddf935266204f115c143d5d6dd7ce874fa`

Detect cross-entry collisions and unsafe aliases before vocabulary activation.

**Verified symbols:**

- `AliasVerdict` - lines 63-81
- `load_review(path: Path | None=None)` - lines 91-96
- `audit_aliases(vocab: Vocabulary, *, review: dict[str, Any] | None=None)` - lines 123-158
- `audit_cross_entry(vocab: Vocabulary, *, review: dict[str, Any] | None=None)` - lines 161-194
- `audit_vocabulary(vocab: Vocabulary, *, review: dict[str, Any] | None=None, cross_entry: bool=True)` - lines 209-228

**Donor test candidates:** `tests/kg/test_vocabulary_audit.py`

## C-16 - als_knowledge_agent/src/als_knowledge_agent/graph/vocab_quality_snapshot.py

**Receiving-source anchors:** B35 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/quality_snapshots.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `1ffbf3d2c8e3e8ee91d7b76a7d012bf39fbaaecad1dd73491663cfb0eb6e0012`

Version reports and limit worker footprint; report candidate counts separately from canonical entity counts.

**Verified symbols:**

- `summarise(report: dict[str, Any], plan: dict[str, Any] | None, audit: dict[str, Any] | None)` - lines 62-104
- `write_snapshot(store: Any, *, directory: Path | None=None, vocab: Any | None=None, with_migration: bool=False)` - lines 107-146
- `snapshot_history(directory: Path | None=None, *, limit: int=30)` - lines 149-178
- `snapshot_worker(stop: asyncio.Event, *, store_factory: Callable[[], Any], interval_s: float=SNAPSHOT_INTERVAL_S, directory: Path | None=None, poll_s: float=600.0)` - lines 189-216

**Donor test candidates:** `tests/kg/test_vocab_quality_snapshot.py`

## U-01 - als_knowledge_agent/src/als_knowledge_agent/graph/scientific_artifacts.py

**Receiving-source anchors:** B02, B29 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/{store,models,budgets}.py`
**Disposition:** split-adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `74e2b32f983203512cd1b67d4a8a4d030079d708c12eb5487059a3d16865a827`

Retain leases, retries and budget reservation/settlement. This SQLite store is operational state, not a competing scientific KG. Ensure tenant scoping and recovery after reservation/worker failure.

**Verified symbols:**

- `artifact_database_path` - lines 41-48
- `UncertaintyCase` - lines 56-80
- `ResolutionAttempt` - lines 84-97
- `ScientificArtifactStore` - lines 100-722
- `ScientificArtifactStore.register_artifact(self, *, publication_id: str, document_hash: str, kind: str, locator: str, content_hash: str, path: str='', metadata: dict[str, Any] | None=None)` - lines 206-220
- `ScientificArtifactStore.enqueue(self, case: UncertaintyCase)` - lines 222-234
- `ScientificArtifactStore.claim(self, owner: str, *, limit: int=1, lease_seconds: float=300, route: str | None=None)` - lines 236-279
- `ScientificArtifactStore.lease_cases(self, case_ids: list[str], owner: str, *, lease_seconds: float=300)` - lines 281-320
- `ScientificArtifactStore.route_case(self, case_id: str, owner: str, route: str, group_key: str, *, state: CaseState='pending')` - lines 322-340
- `ScientificArtifactStore.claim_group(self, owner: str, *, limit: int=8, route: str='cborg')` - lines 370-416
- `ScientificArtifactStore.claim_review_batch(self, owner: str, *, limit: int=16, max_chars: int=64000)` - lines 440-474
- `ScientificArtifactStore.release(self, case_id: str, owner: str, *, state: CaseState='retry_wait', retry_after: float=0, reason: str='')` - lines 504-518
- `ScientificArtifactStore.record_attempt(self, attempt: ResolutionAttempt, owner: str, *, next_state: CaseState | None=None, route: str | None=None, group_key: str | None=None)` - lines 520-561
- `ScientificArtifactStore.attempt_count(self, case_id: str, *, provider: str | None=None)` - lines 563-570
- `ScientificArtifactStore.reserve_budget(self, case_id: str, model: str, amount_usd: float, cap_usd: float, *, scope: str=DEFAULT_BUDGET_SCOPE, scope_cap_usd: float | None=None)` - lines 572-609
- `ScientificArtifactStore.settle_budget(self, reservation: str, actual_usd: float | None, *, cancelled: bool=False)` - lines 611-649
- `ScientificArtifactStore.summary(self)` - lines 651-694
- `ScientificArtifactStore.list_cases(self, *, state: str | None=None, limit: int=100, route: str | None=None)` - lines 696-716

**Donor test candidates:** `tests/kg/test_scientific_artifacts.py`

## U-02 - als_knowledge_agent/src/als_knowledge_agent/graph/thread_sqlite.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/infra/sqlite.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `026bb3c709db0893921bd755ae14d8d8da0964702a69244172874e49b88c5c6f`

Own connection lifecycle; configured WAL/busy timeout and thread-safe usage; no shared production DB in tests.

**Verified symbols:**

- `thread_connection(path: str | Path, *, timeout: float=30.0, setup: Callable[[sqlite3.Connection], None] | None=None)` - lines 29-57
- `forget_thread_connections` - lines 60-70

**Donor test candidates:** `tests/fitness/test_sqlite_openers_wait_for_the_lock.py`

## U-03 - als_knowledge_agent/src/als_knowledge_agent/graph/resolution_routing.py

**Receiving-source anchors:** B11 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/routing.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `f250d7174f65fcbf35fb490bf4b1a1c9819d38a9624d6391ae72ee19a6c9e1f5`

Keep cheap deterministic/structural resolution before model escalation and explicit outcome states.

**Verified symbols:**

- `source_quote(evidence: str, surface: str)` - lines 33-39
- `terminology_group(case: dict[str, Any], vocabulary: Vocabulary | None=None)` - lines 42-54
- `LocalResolver` - lines 57-172
- `LocalResolver.triage_once(self, limit: int=100)` - lines 99-105
- `LocalResolver.structural_once(self)` - lines 136-172
- `local_resolution_worker(stop: asyncio.Event)` - lines 175-189

**Donor test candidates:** `tests/kg/test_resolution_routing.py`

## U-04 - als_knowledge_agent/src/als_knowledge_agent/graph/local_llm_resolution.py

**Receiving-source anchors:** B11 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/local_resolution.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0b47160565ee9a24ad8336974d9786f6d7e2c922b92faa9ec7c154da182e8c14`

Bound background resolution, support pause/cancel; use approved model configuration, not implicit globals. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `local_llm_worker(stop: asyncio.Event, *, limit: int=0, case_limit: int=0)` - lines 44-155
- `read_status` - lines 31-41

**Donor test candidates:** `tests/kg/test_local_llm_resolution.py`

## U-05 - als_knowledge_agent/src/als_knowledge_agent/graph/cborg_approval.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/approval.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `92fbec663949e7c1492ca57e04071527a402422a8c00741b5d65ee2eed273f34`

Preserve explicit cost approval but authenticate approval creation. Bind amount/models/scope/expiry; agents cannot grant their own authority.

**Verified symbols:**

- `CBorgNotApproved` - lines 61-62
- `write_approval(*, stage: ApprovalStage, cap_usd: float, hours: float, paths: list[str], approved_by: str, campaign_id: str='')` - lines 93-156
- `read_approval(now: float | None=None)` - lines 185-208
- `revoke_approval(*, used: bool=False)` - lines 211-233
- `spent_since(store: ScientificArtifactStore, since: float)` - lines 236-249
- `check_approval(scope: str, amount_usd: float, *, store: ScientificArtifactStore | None=None, now: float | None=None)` - lines 252-282

**Donor test candidates:** `tests/kg/test_cborg_approval.py`

## U-06 - als_knowledge_agent/src/als_knowledge_agent/graph/cborg_ledger.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/cost_ledger.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `3065cd3e8c8d110d40eeec5d227c3a5a39bb045f57212f31ecb433566c178f59`

Reconcile estimate/reservation/actual cost once per request; missing cost metadata is unknown, not free.

**Verified symbols:**

- `BudgetExceeded` - lines 41-42
- `parse_cost_header(raw: str | None)` - lines 45-62
- `settle_from_response(store: ScientificArtifactStore, reservation: str, response: httpx.Response)` - lines 65-85
- `ledgered_http_client(store: ScientificArtifactStore, *, scope: str, cap_usd: float, scope_cap_usd: float | None=None, reservation_usd: float | None=None, timeout: float=180.0, transport: httpx.BaseTransport | None=None)` - lines 126-183

**Donor test candidates:** `tests/kg/test_cborg_ledger.py`

## U-07 - als_knowledge_agent/src/als_knowledge_agent/graph/cborg_resolution.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/remote_resolution.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `72a8af75c4e90fe3d4cbe514969ea0d92921d51ef67780fe14f71ca0b9b89756`

Only after approved cost and outbound-content policy. Provider selection injectable; preserve validation/review rather than automatic authority. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `validate_resolution(case: dict[str, Any], answer: dict[str, Any])` - lines 42-85
- `CBorgResolver` - lines 88-436
- `CBorgResolver.resolve_case(self, case: dict[str, Any])` - lines 164-166
- `CBorgResolver.resolve_cases(self, cases: list[dict[str, Any]])` - lines 193-393
- `CBorgResolver.run_once(self, *, case_limit: int | None=None)` - lines 422-436
- `resolution_worker(stop: asyncio.Event)` - lines 439-454

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## U-08 - als_knowledge_agent/src/als_knowledge_agent/graph/cborg_control.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/control.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ba2aea9d737068973825e78290ac54b33595e6a549666e921f5c40d1f7eee363`

Preserve control schema and persistence. Replace apply_control_environment with immutable per-job settings; no cross-request os.environ mutation.

**Verified symbols:**

- `read_control(path: Path | None=None)` - lines 18-40
- `write_control(enabled: bool, budget_cap_usd: float, path: Path | None=None)` - lines 43-57
- `apply_control_environment(control: dict[str, Any])` - lines 60-63

**Donor test candidates:** `tests/kg/test_cborg_control.py`

## U-09 - als_knowledge_agent/src/als_knowledge_agent/graph/chunk_reextraction.py

**Receiving-source anchors:** B34 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/targeted_repair.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `5957a5fdb8df8b33bf436fe03a553c97843a051aa1e6c6cba10fd61276d70139`

Plan affected chunks, source versions and cost; reuse valid caches. Do not rerun entire corpus by default.

**Verified symbols:**

- `plan_reextraction(artifacts: ScientificArtifactStore, store: KGStore, *, case_types: tuple[str, ...]=('incomplete_extraction',), model: str='vllm:glm-5.3-flash', thinking_budget: int=0, max_tokens: int=8192, limit: int=0, reasons: tuple[str, ...]=(), probe: bool=False)` - lines 223-358
- `apply_reextraction(store: KGStore, *, apply: bool=False, limit: int=0, reviewed_plan: dict[str, Any])` - lines 514-732
- `_record_provenance(store: KGStore, plan: dict[str, Any], report: dict[str, Any], *, run_started: float)` - lines 735-778

**Donor test candidates:** `tests/kg/test_chunk_reextraction.py`

## U-10 - als_knowledge_agent/src/als_knowledge_agent/graph/term_provenance.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/provenance/canonicalization.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ddcd0a3e4b3ca93c6b19adacb274f7c6770d59252c58c4dddaff9cb216b2334e`

Immutable plans and activities plus source-to-canonical links; imported code commit and F2W commit both recorded.

**Verified symbols:**

- `ResolutionPlan` - lines 71-101
- `write_plan(store: KGStore, plan: ResolutionPlan)` - lines 154-182
- `write_activity(store: KGStore, *, round_key: str, plan: ResolutionPlan, started: float, finished: float, counts: dict[str, int], cost_usd: float, input_tokens: int, output_tokens: int)` - lines 185-235
- `link_resolved_terms(store: KGStore, activity_id: str, targets: dict[str, int])` - lines 238-258
- `git_sha` - lines 261-270

**Donor test candidates:** `tests/kg/test_term_provenance.py`

## V-01 - als_knowledge_agent/src/als_knowledge_agent/graph/classification_units.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/classification_units.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `884dc6e57e4e7c41eda5240de903c1c53d5b2e27edd58a06219592b2850461ba`

Figure-versus-panel identity and reading order must remain explicit. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `Unit` - lines 45-67
- `verdict_classes(verdict: dict[str, Any])` - lines 70-86
- `panel_parents(client: Any=None)` - lines 89-125
- `resolve_units(verdicts: dict[str, dict[str, Any]], parents: dict[str, str], *, include_superseded: bool=False, panel_order: dict[str, int] | None=None)` - lines 128-192
- `panel_order_map(panels: list[dict[str, Any]] | None=None)` - lines 195-221

**Donor test candidates:** `tests/kg/test_classification_units.py`, `tests/kg/test_orientation_prefers_panels.py`

## V-02 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_classification.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/classification.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `2d113cc80c3c3a14a0381fb8aab45d0e20178464adf0e605d5f516097ccbf447`

Paper-only figure taxonomy and caption context; classify is not experimental model inference. Scope writes to selected paper version. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `record_classification(figure_entity_id: str, verdict: dict[str, Any], anchors: dict[str, str], *, classified_at: str | None=None)` - lines 112-222
- `caption_clauses(caption: str)` - lines 268-292
- `panel_context(label: str, figure_caption: str, *, limit: int=300)` - lines 295-320
- `PaperContexts` - lines 453-511
- `build_paper_contexts(store: Any=None)` - lines 514-556
- `classify_unclassified_figures(*, limit: int=100000, model: str | None=None, reclassify: bool=False, concurrency: int | None=None)` - lines 559-724
- `classify_unclassified_panels(*, limit: int=100000, model: str | None=None, reclassify: bool=False, concurrency: int | None=None)` - lines 745-838

**Donor test candidates:** `tests/kg/test_figure_class_v4.py`, `tests/kg/test_panel_classification.py`

## V-03 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_extraction.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/figure_extraction.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `3a60243c3d28fc400dc498a002e7de87be1bfb900635aece7cab269c318fdbac`

Preserve extraction geometry/asset identity and thresholds; enforce licensing and source coordinates. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `panel_path(figure_entity_id: str, index: int, version: str=EXTRACTION_VERSION)` - lines 97-104
- `extract_figure(figure: dict[str, Any], *, dry_run: bool=True, use_ocr: bool=True)` - lines 354-534
- `extract_unextracted_figures(*, limit: int=200, reextract: bool=False, dry_run: bool=True, use_ocr: bool=True)` - lines 557-642
- `sweep_panels_of(figure_id: str, *, apply: bool=False)` - lines 645-673

**Donor test candidates:** `tests/kg/test_figure_extraction.py`, `tests/kg/test_figure_extraction_quality.py`

## V-04 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_text_layer.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/text_layer.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `6317e71c1b1f83bf83b48bab647809751cfcb68269819672ae665bba34c1a773`

Prefer original PDF text layer; preserve page/bbox coordinate transforms. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `FigureTextLayer` - lines 84-107
- `figure_page(filename: str)` - lines 110-113
- `pdf_for_figure(image_path: str | Path)` - lines 116-129
- `locate_figure_on_page(image_path: str | Path, document: Any, page_index: int)` - lines 148-201
- `text_layer_for(figure: dict[str, Any], *, size_px: tuple[int, int] | None=None)` - lines 319-416

**Donor test candidates:** `tests/kg/test_figure_text_layer.py`

## V-05 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_ocr.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/ocr.py`
**Disposition:** required-fallback-capability | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `5fc112ecfdc4fb4dc26ed09d15f483a4521e3a6a8144849d9c0187f30dc43064`

Fallback only after usable embedded text is absent; log OCR method/confidence and preserve image evidence. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `detect(image: Any, *, engine: Any=None)` - lines 117-142
- `read_bar_labels(image: Any, frame: FigureFrame, *, engine: Any=None, detections: tuple[Detection, ...] | None=None)` - lines 145-186
- `read_figure_text(image: Any, frame: FigureFrame, *, engine: Any=None, detections: tuple[Detection, ...] | None=None)` - lines 207-246

**Donor test candidates:** `tests/kg/test_figure_ocr.py`

## V-06 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_scale.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/scale.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `f3b36b6120d466dcacd96d09e1c654d45e6d0be5fb5946b12c0571a195055fb3`

Retain reported scale, unit, panel context and evidence; no inferred scale treated as measurement. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `record_figure_scale(figure_entity_id: str, extent_nm: float, *, source: str='human', reviewer: str='', note: str='', needs_review: bool=False, recorded_at: str | None=None)` - lines 117-204
- `newest_scales(limit: int=100000, client: Any=None)` - lines 207-218

**Donor test candidates:** `tests/kg/test_figure_scale.py`

## V-07 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_qrange.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/qrange.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `b72be91a3c5a891bbfbcd8774bb62327fdf8a2258af2ccf3e2874e64c24968ce`

Retain reciprocal-unit handling, q-axis context and source support; avoid cross-panel label mixing. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `record_figure_qrange(entity_id: str, q_min_nm_inv: float, q_max_nm_inv: float, *, unit_source: str, axis_kind: str='linear', n_ticks: int=0, confidence: float=0.0, note: str='', extraction_version: str='', needs_review: bool=False, recorded_at: str | None=None)` - lines 154-241
- `newest_qranges(limit: int=100000, client: Any=None)` - lines 244-253

**Donor test candidates:** `tests/kg/test_figure_qrange.py`

## V-08 - als_knowledge_agent/src/als_knowledge_agent/graph/figure_orientation.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/orientation.py`
**Disposition:** adapt | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `db9745fd0383df545806962ae930af3d1a395ab45b1a585cf448b42c24bfa804`

Record supported literature statements separately from model-inferred orientation; uncertain interpretations remain review candidates. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral. Route inference through adapters over existing F2W LLM paths/classes. Preserve provider/model selection, prompt/QA constraints, retries and supported streaming. Do not import donor model defaults or add independent direct inference transports.

**Verified symbols:**

- `parse_orientation_answer(raw: str)` - lines 81-119
- `candidate_subjects(publication_id: str, *, verdicts: dict[str, dict[str, Any]] | None=None, parents: dict[str, str] | None=None, figures: list[dict[str, Any]] | None=None, panels: list[dict[str, Any]] | None=None)` - lines 163-282
- `read_lamellar_orientation(publication_id: str, *, model: str | None=None)` - lines 291-335

**Donor test candidates:** `tests/kg/test_figure_orientation.py`

## V-09 - src/forge/analysis/figure_extraction/types.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/types.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `7997300c1b989dac9f3f9b30b9f02dc913d49d38d69019aac2984c226863f010`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `TextSpan` - lines 31-56
- `PanelBox` - lines 60-91
- `ScaleReading` - lines 95-137
- `QAxisReading` - lines 141-188

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## V-10 - src/forge/analysis/figure_extraction/geometry.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/geometry.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `bb360d1acc17be4bc64ebbb717f1f98caef5fc8197e537f5f13b14c1260171c9`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `FigureFrame` - lines 40-142
- `span_to_pixels(span: TextSpan, frame: FigureFrame)` - lines 145-163
- `spans_inside(spans: list[TextSpan], frame: FigureFrame, region: tuple[float, float, float, float])` - lines 166-193

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## V-11 - src/forge/analysis/figure_extraction/panels.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/panels.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `8e071fa9f331aed6eb7accbc39121fb516efa3515ee99ef72e058539f9231542`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `panel_labels(caption: str)` - lines 87-103
- `caption_names_multiple_panels(caption: str)` - lines 106-119
- `ink_mask(image: NDArray[np.floating[Any]] | NDArray[np.integer[Any]])` - lines 197-219
- `segment_panels(image: NDArray[np.floating[Any]] | NDArray[np.integer[Any]])` - lines 318-397
- `consecutive_from_a(labels: Iterable[str])` - lines 424-455
- `reconcile_panels(boxes: list[PanelBox], labels: Iterable[str], shape: tuple[int, int])` - lines 458-496
- `label_panels_from_spans(boxes: list[PanelBox], spans: list[TextSpan], frame: FigureFrame, allowed: Iterable[str]=())` - lines 506-575

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## V-12 - src/forge/analysis/figure_extraction/qaxis.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/qaxis.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `33892b09b27eec9e136c542c4da4dbbfcb616de65d52cd9f5c9eafc560257a45`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `AxisCandidate` - lines 71-102
- `find_axis_candidates(spans: list[TextSpan], *, tolerance_px: float=4.0)` - lines 147-183
- `read_q_axis(spans: list[TextSpan], *, caption_unit: str='', document_unit: str='')` - lines 186-308

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## V-13 - src/forge/analysis/figure_extraction/scalebar.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/scalebar.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `9049d3cd1696cf2c16f324bf32e623a7d518591f1b2bf7ffe25900ad1098e1c1`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `caption_bar_lengths(caption: str, *, window: int=BAR_CONTEXT_CHARS)` - lines 98-138
- `pair_lengths_to_panels(caption: str)` - lines 141-179
- `read_panel_scale(image: object, panel: PanelBox, *, caption_length_nm: float | None=None, length_spans: list[TextSpan] | None=None, saturation: int=250)` - lines 182-274

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## V-14 - src/forge/analysis/figure_extraction/units.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/vision/kernels/units.py`
**Disposition:** extract-paper-only | **Release obligation:** required_before_H4_and_H6
**Source SHA-256:** `e929dfd76812762ba9600b1439a48059fe999756d45703de398dc5ffcb315caa`

Pure paper-figure kernels are a deliberate exception to excluding experimental analysis. Audit each import; do not pull pyFAI, simulation or measured-frame workflows. Paper figure capability is required for the first migrated release. An optional install extra is packaging isolation, not release deferral.

**Verified symbols:**

- `normalise_unit(text: str)` - lines 81-106
- `fold_length(value: float, unit: str)` - lines 109-126
- `fold_inverse_length(value: float, unit: str)` - lines 129-145
- `caption_inverse_unit(caption: str)` - lines 171-197
- `document_inverse_unit(texts: list[str] | tuple[str, ...])` - lines 200-235
- `iter_lengths(text: str)` - lines 238-255

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## K-01 - als_knowledge_agent/src/als_knowledge_agent/graph/kg_writer.py

**Receiving-source anchors:** B16, B24 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/literature_writer.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `7136fbef9084771461ad1ff80819352ea0f9375472aa2a026f0e4f5057135e18`

Translate onto F2W semantic model. upsert_analysis describes methods in papers; it must not execute analyses. Separate candidate entities from published records.

**Verified symbols:**

- `upsert_technique(store: KGStore, slug: str, name: str, *, source_pub_id: str | None=None, confidence: float=0.9)` - lines 64-103
- `upsert_analysis(store: KGStore, slug: str, method: str, *, software: str | None=None, source_pub_id: str | None=None, confidence: float=0.9)` - lines 106-144
- `upsert_instrument(store: KGStore, slug: str, name: str, *, source_pub_id: str | None=None)` - lines 147-179
- `upsert_publication_full(store: KGStore, work: dict[str, Any])` - lines 243-346
- `upsert_authors(store: KGStore, pub_id: str, authors_list: list[dict[str, Any]], *, cited_as: str='', known: dict[str, int] | None=None)` - lines 377-456
- `upsert_material(store: KGStore, slug: str, name: str, *, source_pub_id: str | None=None, confidence: float=0.9)` - lines 464-508
- `upsert_candidate_term(store: KGStore, term: ResolvedTerm, *, source_pub_id: str | None=None, model_used: str | None=None)` - lines 511-548
- `current_vocabulary_version` - lines 557-587
- `link_section_entities(store: KGStore, pub_id: str, section_id: str, entities: SectionEntities, model_used: str | None=None, *, llm_ok: bool=False)` - lines 590-732
- `term_edges_for_sections(store: KGStore, section_ids: set[str])` - lines 737-761
- `term_edge_status(store: KGStore, *, version: int=EXTRACTION_VERSION, vocabulary_version: int | None=None, require_llm: bool=False)` - lines 764-844

**Donor test candidates:** `tests/kg/test_kg_writer.py`

## K-02 - als_knowledge_agent/src/als_knowledge_agent/graph/kg_store.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/ports.py`
**Disposition:** interface-reference | **Release obligation:** required_review_not_wholesale_copy
**Source SHA-256:** `90740c5930f829c03b11103a0e0fc0c264b5ec3a7dd310c645c5bf2a3d16045e`

Do not add the unsupported donor SQLite KG as another authoritative store. Define narrow capability-aware GraphPort and preserve real F2W backends.

**Verified symbols:**

- `KGStore` - lines 70-668
- `get_store(db_path: str | Path | None=None)` - lines 675-713

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## K-03 - als_knowledge_agent/src/als_knowledge_agent/graph/tiled_store.py

**Receiving-source anchors:** B03, B09, B25, B26 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/backends/tiled.py`
**Disposition:** required-adapter | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `2fc869d8796416e0344793eb35008e80bbf3f3ae013f348fe8fa7477360e9f57`

Retain batch and restart-read semantics only for literature-owned graph data. Do not infer in-place mutable entity properties from an upsert method name. Mandatory first-release Tiled literature graph adapter; no new Splash backend. F2W owns literature publication; FORGE consumes F2W agents and published literature graphs. JSON remains a portable staging/snapshot format.

**Verified symbols:**

- `finite_properties(properties: dict[str, Any])` - lines 256-295
- `relation_to_predicate(relation_type: str)` - lines 298-313
- `node_id_to_path(node_id: str, entity_type: str='', properties: dict[str, Any] | None=None)` - lines 316-401
- `TiledKGStore` - lines 421-1653

**Donor test candidates:** `tests/kg/test_tiled_store.py`, `tests/kg/test_tiled_store_reads_after_restart.py`

## K-04 - src/forge/kg/client.py

**Receiving-source anchors:** B03, B25 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/backends/tiled_client.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0d6a389624c68fc80f39227461163ccec716c662b8648240ab511aad0842558f`

Inject identity/settings/http; distinguish unauthenticated/partial/unavailable from empty graph; no global credential cache across users.

**Verified symbols:**

- `graphql_endpoint_for(base_uri: str)` - lines 47-66
- `GraphQLClient` - lines 128-282
- `GraphQLClient.query(self, query_string: str, variables: dict[str, Any] | None=None)` - lines 172-240
- `GraphQLClient.mutation(self, mutation_string: str, variables: dict[str, Any] | None=None)` - lines 242-259
- `GraphQLClient.mutations(self)` - lines 280-282
- `get_client(graph_uri: str | None=None, api_key: str | None=None)` - lines 285-343
- `clear_client_cache` - lines 346-352

**Donor test candidates:** `tests/kg/test_graphql_client.py`

## K-05 - src/forge/kg/paging.py

**Receiving-source anchors:** B25 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/paging.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `c02f8fd0d72470499db6726ad9aaeb19bfd308c65827c8d16499694179780886`

A truncation warning is not complete pagination. Reads used for mutation plans must prove completeness or stop.

**Verified symbols:**

- `warn_if_truncated(rows: list[T], limit: int, reader: str, what: str='rows')` - lines 44-73

**Donor test candidates:** `tests/kg/test_load_truncation.py`, `tests/kg/test_paging_truncation.py`

## K-06 - src/forge/kg/mutations.py

**Receiving-source anchors:** B03, B25, B26 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/backends/tiled_mutations.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0576238e4fe2c5eb1f5a381c2fe895f3d6310d57c316808c8b1e0ccb1357730e`

No global resets. Translate F2W change sets, enforce namespace/ownership and explicit capabilities; destructive helpers require separate approved plan.

**Verified symbols:**

- `ensure_namespaces(client: GraphQLClient | None=None)` - lines 144-170
- `resolve_node_id(path: str, client: GraphQLClient | None=None)` - lines 183-206
- `find_entity_id(path: str, entity_type: EntityType | None=None, client: GraphQLClient | None=None)` - lines 292-381
- `upsert_entity(entity: Entity, client: GraphQLClient | None=None)` - lines 402-411
- `upsert_entity_id(entity: Entity, client: GraphQLClient | None=None, *, assume_new: bool=False, resolve_node: bool=True)` - lines 414-548
- `delete_entity_by_id(entity_id: str, client: GraphQLClient | None=None)` - lines 551-588
- `create_link(source_path: str, target_path: str, relation_type: LinkType, metadata: LinkMetadata | None=None, client: GraphQLClient | None=None)` - lines 620-669
- `link_ids(subject_id: str, predicate: str, object_id: str, *, properties: dict[str, Any] | None=None, client: GraphQLClient | None=None, label: str='')` - lines 672-728
- `find_links(subject_id: str='', *, predicate: str='', object_id: str='', client: GraphQLClient | None=None)` - lines 731-841
- `delete_link(link_id: str, client: GraphQLClient | None=None)` - lines 844-878
- `record_provenance(result: _RunResult, request: _RunRequest, client: GraphQLClient | None=None)` - lines 881-991

**Donor test candidates:** `tests/kg/test_edge_idempotency.py`

## K-07 - src/forge/kg/paths.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/identity_adapter.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6bc8802283592ea3b34cde8b8c25969224dbc2850447b11036c14e8000675661`

Maintain identity translation ledger; avoid replacing existing F2W IDs with donor label slugs; map versions and source artifacts separately.

**Verified symbols:**

- `slug(text: str, *, max_length: int=80)` - lines 67-94
- `publication_path(openalex_id: str='', doi: str='')` - lines 97-129
- `author_path(openalex_id: str='', name: str='')` - lines 181-200
- `material_path(name: str)` - lines 203-222
- `document_path(sha256: str)` - lines 237-245

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## K-08 - src/forge/kg/ontology.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/predicate_mapping.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6b7d2e92019c430deaeb31463ae9782bd8ee8ee1d1e4f571932761c7a6913f0e`

Selected provenance predicates/normalization only. LinkML remains scientific authority; preserve unsupported donor predicates in quarantine/migration report.

**Verified symbols:**

- `compact_predicate(curie: str)` - lines 569-585

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## K-09 - src/forge/kg/entities.py

**Receiving-source anchors:** B08, B09, B27 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/record_mapping.py`
**Disposition:** mapping-reference | **Release obligation:** required_review_not_wholesale_copy
**Source SHA-256:** `c3839c512ac083a0989418405daf3fc3b4b45c596662dc67782ad23811d9531a`

Use types to preserve donor fields at the boundary, not copy a competing scientific ontology. Parse DTOs and API models may remain Pydantic.

**Verified symbols:**

- `Publication` - lines 205-226
- `Author` - lines 369-382
- `PaperSection` - lines 385-400
- `PaperFigure` - lines 403-416
- `Evidence` - lines 519-547
- `Quantity` - lines 562-615
- `Claim` - lines 618-705

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## K-10 - src/forge/kg/paper_subgraph.py

**Receiving-source anchors:** B08, B09, B18, B19, B20, B27 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/retrieval/paper_subgraph.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4b2e12eac74696edfe0763554d2c034b71ef4d7d3a634c2caccf2c5c69ced0ee`

Restrict to authorized paper-derived subgraph; experimental and simulated neighbors returned as scoped references only when explicitly permitted.

**Verified symbols:**

- `paper_subgraph(publication_id: str, *, depth: int=2, mentions: str='typed', text_chars: int=0, max_nodes: int=DEFAULT_MAX_NODES, client: GraphQLClient | None=None)` - lines 236-300

**Donor test candidates:** `tests/kg/test_paper_subgraph.py`

## K-11 - src/forge/kg/views.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/{queries,projections}.py`
**Disposition:** split-adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `44d0306d2093946627711b9d687c08b87d1dbc8c28bfa105e68efcce383a3cb2`

Retain bounded traversal and freshness projections. Filter literature ownership; sample_context and simulation recipes stay FORGE.

**Verified symbols:**

- `KnowledgeGraphUnavailable` - lines 63-64
- `iter_entities(entity_type: str, limit: int=100000)` - lines 509-564
- `reduce_newest(rows: Iterable[dict[str, Any]], *, timestamp_key: str='recorded_at')` - lines 576-616
- `newest_link_properties(predicate: str, *, limit: int=100000, timestamp_key: str='recorded_at', client: GraphQLClient | None=None)` - lines 619-690
- `iter_links(limit: int=_DEFAULT_LINK_PAGES)` - lines 693-739
- `walk(start_ids: Sequence[str], steps: Sequence[WalkStep], *, max_frontier: int=_MAX_FRONTIER, hydrate: bool=True, client: GraphQLClient | None=None)` - lines 877-1011
- `find_entities(name_contains: str='', entity_type: str='', limit: int=50)` - lines 1014-1090
- `graph_recent(since: str='')` - lines 1093-1126
- `graph_snapshot` - lines 1152-1197
- `graph_stats` - lines 1204-1251
- `resolve_fast(ref: str, client: GraphQLClient | None=None)` - lines 1636-1717
- `graph_lineage(name: str)` - lines 1826-1911
- `graph_connections(ref: str)` - lines 2035-2192

**Donor test candidates:** `tests/kg/test_lineage_reads_only_what_it_walks.py`, `tests/kg/test_views_paging_strict.py`

## K-12 - src/forge/agents/kg/tools.py

**Receiving-source anchors:** B08 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/tools/graph.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `8148154f090ec7e671f69aecb8f8c4f925764b7087d683561d74a2a50ecc0fc8`

Five verified donor entrypoints are eligible with literature scope. Omit dataset runs/sample contexts/prior simulations. Preserve aliases through FORGE wrappers.

**Verified symbols:**

- `kg_find_entities(query: str='', entity_type: str='', limit: int=20)` - lines 174-208
- `kg_get_summary` - lines 131-170
- `kg_read_paper(ref: str, section: str='')` - lines 344-480
- `kg_get_lineage(name: str)` - lines 212-234
- `kg_get_connections(ref: str)` - lines 238-274

**Donor test candidates:** `tests/agents/test_kg_tools.py`, `tests/kg/test_kg_read_paper.py`, `tests/kg/test_kg_summary_tool_is_cheap.py`

## K-13 - src/forge/kg/recorder.py

**Receiving-source anchors:** B30, B31 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/provenance/{events,recorder}.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `41c25a08b16653c2251f15c0169c5c129366d636868d5b1ba5657dc6ecfb53b9`

Event sink protocol from first runtime slice; durable approval/write audit independent of optional graph projection. Redact secrets and bound payloads.

**Verified symbols:**

- `provenance_writes_allowed` - lines 113-151
- `GraphRecorder` - lines 154-1145
- `GraphRecorder.start(self, *, question: str='', provider: str | None=None, model: str | None=None, source: str | None=None, **properties: Any)` - lines 428-479
- `GraphRecorder.finish(self, *, status: str='completed', error: str | None=None)` - lines 481-550
- `GraphRecorder.llm_turn(self, *, text: str='', model: str | None=None, **props: Any)` - lines 554-574
- `GraphRecorder.tool_call(self, *, tool: str, arguments: dict[str, Any] | None=None, output: str='', failed: bool=False, used_paths: list[str] | None=None, **props: Any)` - lines 576-621
- `GraphRecorder.decision(self, *, requested_capabilities: list[str], selected_tools: list[str], selected_agents: list[str], summary: str='', after_step: str | None=None)` - lines 702-767
- `GraphRecorder.stage(self, *, name: str, status: str='completed', duration_s: float | None=None, **props: Any)` - lines 769-822
- `GraphRecorder.record_plan(self, activity_path: str, *, name: str, version: str, text: str='', **properties: Any)` - lines 863-906
- `GraphRecorder.generated(self, *, path: str, entity_type: EntityType=EntityType.DATASET, by_step: str | None=None, derived_from: list[str] | None=None, **properties: Any)` - lines 929-958
- `GraphRecorder.summary(self)` - lines 962-978
- `GraphRecorder.on_message(self, message: Any)` - lines 982-1078
- `GraphRecorder.note_langfuse_trace(self, trace_id: str | None)` - lines 1080-1094
- `GraphRecorder.note_tool_context(self, *, owners: dict[str, str] | None=None, skills: dict[str, list[str]] | None=None)` - lines 1096-1121
- `GraphRecorder.note_tool_times(self, tool_times: dict[str, float] | None)` - lines 1123-1145

**Donor test candidates:** `tests/kg/test_decisions_and_attribution.py`, `tests/kg/test_recorder.py`, `tests/kg/test_recorder_defects.py`

## K-14 - src/forge/kg/recorder_queue.py

**Receiving-source anchors:** B30 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/provenance/queue.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6871c75600756c1a8422fd420c0f2aa155b5ab6db0e6723b076c8dd5943390e9`

Drain/flush and report failures; never silently drop critical mutation audit while reporting success.

**Verified symbols:**

- `QueuedRecorder` - lines 48-127
- `QueuedRecorder.start(self, *args: Any, **kwargs: Any)` - lines 95-96
- `QueuedRecorder.on_message(self, message: Any)` - lines 98-99
- `QueuedRecorder.finish(self, **kwargs: Any)` - lines 107-117

**Donor test candidates:** `tests/kg/test_recorder_queue.py`

## K-15 - src/forge/kg/repair.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/repair.py`
**Disposition:** selective | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `80711cc5e0130b73ec92888cd9838d14ff6c7a89aea81a9c1963f765dce4caa9`

Scoped dry-run repair only after backup and approval; abort incomplete reads; preserve referenced experimental entities and user annotations.

**Verified symbols:**

- `TruncatedGraphRead` - lines 83-89
- `find_identifier_duplicates(entities: list[dict[str, Any]], links: list[dict[str, Any]])` - lines 588-674
- `repair_identifier_duplicates(*, apply: bool=False, client: GraphQLClient | None=None)` - lines 677-789
- `dedupe_figures(*, keep_stem: str='', apply: bool=False, client: GraphQLClient | None=None)` - lines 1232-1320
- `dedupe_links(predicate: str='', *, apply: bool=False, client: GraphQLClient | None=None)` - lines 1522-1621

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-01 - als_knowledge_agent/src/als_knowledge_agent/server.py

**Receiving-source anchors:** B01 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/application.py + app/api/routes/* + FORGE paper adapters`
**Disposition:** split-facade | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `9dedc0c0df05e0e7d56f67b1e64dc60e15a63a05ca66d4b0f0186640f80a3236`

Do not copy the 5k-line server. Move orchestration to public F2W services and inject dependencies; preserve FORGE HTTP contracts through adapters. Exclude sim/reduction/dual-fit/live-session/global-reset routes. Route inventory and public/SDK behavior mapping are mandatory; all paper endpoints have a disposition, not just the initial shortlist. Shared graph/read/review endpoints must be scoped to literature and separated from dual-fit or live-run branches.

**Verified symbols:**

- `list_models` - lines 206-244
- `health` - lines 248-249
- `graph_summary` - lines 269-290
- `vocab_quality` - lines 294-314
- `list_entities(entity_type: str=Query(default='', description='Filter by entity type'), name_contains: str=Query(default='', description='Filter by name substring'), limit: int=Query(default=50, description='Max results'))` - lines 318-332
- `graph_full(include_text: bool=Query(default=True, description="Include each node's `text` body. The graph viewer never renders it -- it lands in the adapter's metadata blob and nothing reads it -- but it is ~40 MB of a ~69 MB response and pushed the fetch past the browser's abort timeout, which the UI showed as a dead graph. The UI now asks for false. The default stays true so existing callers see no change."))` - lines 336-360
- `graph_growth` - lines 364-377
- `graph_recent(since: str=Query(description='ISO timestamp — return nodes/edges created after this'))` - lines 381-386
- `graph_node_edges(node_id: str=Query(description='KG node id'))` - lines 390-411
- `AddPublicationRequest` - lines 897-933
- `paper_ingest_status(progress_id: str=Query(..., pattern='^[A-Za-z0-9_-]{1,64}$'))` - lines 967-983
- `add_publication(body: AddPublicationRequest)` - lines 987-1019
- `_process_publication(body: AddPublicationRequest, *, prepared: dict[str, Any] | None=None)` - lines 1022-1436
- `UploadPaperResponse` - lines 1476-1500
- `upload_paper(file: UploadFile=File(...), parse_pdf: bool=Form(True), extract_entities: bool=Form(True), model: str | None=Form(None), doi: str | None=Form(None), output: Literal['tiled', 'json']=Form('tiled'), thinking_budget: int | None=Form(default=None, ge=0, le=4096), progress_id: str | None=Form(default=None, pattern='^[A-Za-z0-9_-]{1,64}$'))` - lines 1504-1652
- `add_annotation(body: AddAnnotationRequest)` - lines 1752-1863
- `graph_admin_status` - lines 1888-1899
- `vocab_cleanup_propose(body: VocabCleanupProposeRequest)` - lines 1950-1994
- `vocab_cleanup_apply(body: VocabCleanupApplyRequest)` - lines 2012-2046
- `backfill_extractions(body: BackfillExtractionsRequest)` - lines 2050-2371
- `start_enrichment(limit: int=Query(default=5000, description='Max datasets to scan'), chem_threshold: float=Query(default=0.5, description='Tanimoto threshold'), max_pubs: int=Query(default=3, description='Max publications per material'), max_combos: int=Query(default=500, description='Max material+technique combos for pub search'), include_vision: bool=Query(default=False, description='Enable vision-based pattern linking'), vision_limit: int=Query(default=20, description='Max datasets for vision analysis'), vision_delay_sec: float=Query(default=8.0, ge=0.0, le=120.0, description='Seconds to wait between vision model calls (0 = no pause; higher = gentler on GPU)'), incremental: bool=Query(default=True, description='If true, skip prior datasets and limit pair stages (faster, thinner graph)'), model: str | None=Query(default=None, description="Ollama model name override (e.g. 'llama3.2:8b'). Defaults to OLLAMA_MODEL env var."), include_simulation: bool=Query(default=True, description='Extract structures from papers and simulate the ones this engine can express'), max_sims: int=Query(default=10, ge=0, le=25, description='Cap on SUCCESSFUL simulations. Each is an LLM call plus a full render.'), sim_timeout_sec: float=Query(default=120.0, ge=10.0, le=600.0), paths: list[str] | None=Query(default=None, description='Catalog paths to walk INSTEAD of the whole tree, repeated once per path. This is how a saved Selection scopes a build: until now the only selector was the process env var TILED_PATH, which a request cannot set, so every build swept all 110 samples. Omit for the full sweep. If none of the given paths resolve the run REFUSES rather than falling back to everything.'), min_confidence: float=Query(default=0.0, ge=0.0, le=1.0, description="Extraction confidence floor. 0 (the default) disables it: the extractor's self-reported score is unstable on identical input, so it is recorded on each decision rather than used as a gate."))` - lines 2706-2764
- `enrichment_status` - lines 2768-2770
- `stop_enrichment` - lines 2774-2778
- `start_topic_sweep(query: str=Query(description='Search query for OpenAlex'), max_papers: int=Query(default=50, ge=1, le=2000, description='Maximum papers to ingest'), year_from: int | None=Query(default=None, description='Only papers published from this year onward'))` - lines 2832-2842
- `topic_sweep_status` - lines 2846-2848
- `stop_topic_sweep` - lines 2852-2856
- `_ingest_pipeline_revision` - lines 3048-3060
- `_resume_interrupted_folder_ingest` - lines 3064-3100
- `_run_folder_ingest_background(folder: str, *, apply: bool, glob: str, limit: int, force: bool, offline: bool, extract_entities: bool, simulate: bool=False, corpus_manifest: bool=False, priority: Literal['source', 'scattering']='source', model: str | None=None, job_id: str='', owner: str='', concurrency: int=1, thinking_budget: int | None=None, output: str='tiled')` - lines 3167-3334
- `start_folder_ingest(folder: str=Query(..., description='Directory of PDFs, inside ALS_INGEST_DIRS'), apply: bool=Query(default=False, description='Actually seed and ingest. Default is a dry-run scan.'), glob: str=Query(default='*.pdf'), limit: int=Query(default=0, ge=0, le=100000, description='0 means all'), force: bool=Query(default=False), offline: bool=Query(default=False), extract_entities: bool=Query(default=True), simulate: bool=Query(default=False, description='Simulate inline. Bulk corpus runs should use the later gated sweep.'), corpus_manifest: bool=Query(default=False, description='Use the reviewed, content-deduplicated ALS corpus manifest.'), priority: Literal['source', 'scattering'] | None=Query(default=None), model: str | None=Query(default=None), concurrency: int | None=Query(default=None, ge=1, le=16), thinking_budget: int | None=Query(default=None, ge=0, le=4096), output: str | None=Query(default=None, description='json stages results for later Tiled import'))` - lines 3338-3472
- `ingest_telemetry` - lines 3476-3516
- `folder_ingest_status` - lines 3520-3539
- `staged_import_status` - lines 3543-3548
- `scientific_uncertainty_summary` - lines 3552-3591
- `set_cborg_control(body: CBorgControlRequest)` - lines 3602-3632
- `local_review_status` - lines 3647-3662
- `start_local_review(body: LocalReviewRequest)` - lines 3666-3696
- `stop_local_review` - lines 3700-3717
- `scientific_uncertainty_cases(state: str | None=Query(default=None), limit: int=Query(default=100, ge=1, le=1000), route: str | None=Query(default=None))` - lines 3724-3733
- `stop_folder_ingest` - lines 3737-3741
- `start_pdf_parse(top_k: int=Query(default=10, ge=1, le=100000, description='Number of top-cited papers to parse'), query_filter: str | None=Query(default=None, description='Optional keyword filter on title/abstract'), oa_only: bool=Query(default=True, description='Only parse open-access papers'), extract_entities: bool=Query(default=True, description='Run entity extraction after parsing'), model: str | None=Query(default=None, description='Ollama model for LLM entity extraction'))` - lines 3745-3793
- `pdf_parse_status` - lines 3797-3804
- `stop_pdf_parse` - lines 3808-3813
- `may_parse_pdf(*, parse_requested: bool, is_oa: bool, have_local_pdf: bool)` - lines 3820-3847
- `_cache_contained_path(raw: str)` - lines 3904-3926
- `table_csv(entity_id: str)` - lines 3930-3948
- `publication_figures(publication_id: str)` - lines 4031-4075
- `figure_image(entity_id: str)` - lines 4079-4108
- `figure_classifications(publication_id: str='', limit: int=100000)` - lines 4207-4244
- `parse_status(parser: str='docling', limit: int=5000)` - lines 4248-4342
- `review_resolve(body: ReviewResolution)` - lines 4360-4441
- `review_resolve_bulk(body: BulkReviewResolution)` - lines 4461-4573
- `figure_scale_record(body: FigureScaleReading)` - lines 4592-4655
- `review_queue(publication_id: str='', limit: int=100000)` - lines 4743-4915
- `paper_extraction(publication_id: str)` - lines 4954-5054
- `classify_figures(body: ClassifyFiguresRequest)` - lines 5089-5122
- `paper_subgraph_route(publication_id: str, depth: int=2, mentions: str='typed', text_chars: int=0, max_nodes: int=600)` - lines 5126-5190
- `extract_figures(body: ExtractFiguresRequest)` - lines 5209-5243
- `analyze_figure(body: AnalyzeFigureRequest)` - lines 5263-5387

**Donor test candidates:** `tests/kg/test_add_publication_endpoint.py`, `tests/kg/test_external_ingest_visibility.py`, `tests/kg/test_upload_paper_route.py`

## I-02 - als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py

**Receiving-source anchors:** B01 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/routes/ingest_folders_routes.py`
**Disposition:** adapt-route | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `7a54df28909ca83d47869dd6e16af8fbeeb36f80218010083e671bcddf5abade`

Route boundary only; public application service owns operation. Preserve payload/error/event adapters where FORGE UI still consumes them.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-03 - als_knowledge_agent/src/als_knowledge_agent/graph/staged_import_routes.py

**Receiving-source anchors:** B01 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/routes/staged_import_routes.py`
**Disposition:** adapt-route | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `1f8e0ba056fa8db5114ecafc08d2d24dbac3a8e881c3ec42aced5dfb32260d9c`

Route boundary only; public application service owns operation. Preserve payload/error/event adapters where FORGE UI still consumes them.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-04 - als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py

**Receiving-source anchors:** B01 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/routes/term_campaign_routes.py`
**Disposition:** adapt-route | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `cc9911009e1d3d5939d50fffdb206c5b31e99a03d8e5467500a7ddd6b068c18b`

Route boundary only; public application service owns operation. Preserve payload/error/event adapters where FORGE UI still consumes them.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-05 - src/forge/api/upload_limits.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/upload_limits.py`
**Disposition:** adapt | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `422ec9bcad5dae5eb2085ce40882b831a522a0026a5f72c73b604e15ddaec9eb`

Enforce total body bound before multipart parsing and bounded stream reads afterward; validate PDF type and safe storage path.

**Verified symbols:**

- `read_upload_limited(file: UploadFile, max_bytes: int | None=None)` - lines 43-85
- `MaxBodySizeMiddleware` - lines 88-145

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-06 - src/forge/api/auth.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/auth.py`
**Disposition:** reference-adapt | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `9fbfe760a309ce3fd544d8007382b68cfc622a29f747b3626c01617ee38a0f5c`

No inherited development bypass for write routes. Inject host principal/capabilities; public Python client and worker enforce the same authorization boundary.

**Verified symbols:**

- `require_admin_key(x_api_key: str | None=Header(default=None))` - lines 132-157
- `require_admin_key_strict(x_api_key: str | None=Header(default=None))` - lines 160-179
- `admin_gate_status` - lines 82-90

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## I-07 - src/forge/api/origin_gate.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/api/origin_gate.py`
**Disposition:** adapt | **Release obligation:** required_FastAPI_and_SDK_parity
**Source SHA-256:** `194231579bee8871ccec202bb815175f8dccdbf4d49e584e0cb7aea97f71ae1c`

Origin checks supplement authentication; they are not a replacement for authorization or CSRF protections.

**Verified symbols:**

- `OriginGateMiddleware` - lines 53-147

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-01 - als_knowledge_agent/src/als_knowledge_agent/gold/annotate.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/annotate.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `95dbb81e9f75a16adcc949a21cec66e8573990e6fc3119ed31c82f1fb2de83aa`

Human named review, immutable draft/adjudicated labels; agent preannotation never constitutes gold approval.

**Verified symbols:**

- `find_offsets(text: str, surface: str, occurrence: int=1)` - lines 34-43
- `span_for_sentence(texts: dict[str, str], sentence: str)` - lines 46-49
- `save_annotation(bundle: Bundle, root: Path, annotator: str, *, overlap: bool)` - lines 61-107
- `adjudicate(root: Path, adjudicator: str, *, source: Path | None=None, edited: Bundle | None=None)` - lines 110-151

**Donor test candidates:** `tests/kg/test_gold_annotate.py`

## Q-02 - als_knowledge_agent/src/als_knowledge_agent/gold/bundle.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/bundle.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `b8f4601f7f8869b4bf474f4ea10a2c71c4094139fd8f2d54a3ccb98cadeeebc2`

Preserve bundles, source/version/checksum integrity and annotation status. Redistribution rules apply to included assets.

**Verified symbols:**

- `Bundle` - lines 74-96
- `BundleError` - lines 112-113
- `load_bundle(path: Path)` - lines 144-161
- `write_bundle(bundle: Bundle, path: Path | None=None)` - lines 170-184
- `iter_bundle_dirs(root: Path)` - lines 187-191
- `Partition` - lines 196-199
- `partition_by_status(root: Path)` - lines 202-217
- `draft_of(bundle: Bundle)` - lines 220-225
- `Issue` - lines 230-236
- `validate_bundle(path: Path)` - lines 252-258
- `validate_loaded(bundle: Bundle)` - lines 261-396

**Donor test candidates:** `tests/kg/test_gold_bundle.py`

## Q-03 - als_knowledge_agent/src/als_knowledge_agent/gold/harness.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/harness.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `cc74442a7902e149e9a329fbe48438eb6f0db0a009d24450b218bd201808be5a`

Separate baseline, candidate predictions and adjudicated references; report unscored/missing cases.

**Verified symbols:**

- `common_args(parser: argparse.ArgumentParser)` - lines 45-75
- `references(root: Path | None, *, include_drafts: bool)` - lines 85-89
- `predictions_for(bundle: Bundle, pred_root: Path | None)` - lines 92-99
- `baseline_for(bundle: Bundle, baseline_root: Path | None)` - lines 102-103
- `mean_of(rows: list[dict[str, Any]], path: str)` - lines 110-122
- `score_block(refs: list[Bundle], *, pred_root: Path | None, baseline_root: Path | None, score: Scorer, aggregate: Aggregator)` - lines 125-157
- `run_stage(stage: str, args: argparse.Namespace, *, score: Scorer, aggregate: Aggregator, extra: dict[str, Any] | None=None)` - lines 160-219
- `print_report(report: dict[str, Any])` - lines 230-250

**Donor test candidates:** `tests/kg/test_gold_harnesses.py`

## Q-04 - als_knowledge_agent/src/als_knowledge_agent/gold/metrics.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/metrics.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `158c78b167016a7612f5a7378e2bb6bbd1fd8025273eeef9374b064f182dbf2c`

Use paper extraction/grounding/canonicalization metrics, not simulation recovery as a literature-release gate. Keep metric assumptions and sample counts visible.

**Verified symbols:**

- `heading_prf(pred: Sequence[dict], gold: Sequence[dict], *, sim: float=0.85, level_tol: int=1)` - lines 132-158
- `box_match(pred: Sequence[PBox | None], gold: Sequence[PBox | None], *, iou_min: float=0.5)` - lines 188-221
- `caption_accuracy(pred: Sequence[str], gold: Sequence[str])` - lines 224-246
- `table_cell_f1(pred: Sequence[Sequence[str]], gold: Sequence[Sequence[str]])` - lines 260-278
- `table_teds(pred_html: str, gold_html: str)` - lines 321-356
- `citation_scores(pred: Sequence[dict], gold: Sequence[dict], *, title_sim: float=0.9)` - lines 371-392
- `mention_prf(pred: Sequence[dict], gold: Sequence[dict], *, mode: str='exact', role_aware: bool=False)` - lines 406-431
- `role_kappa(pred: Sequence[dict], gold: Sequence[dict])` - lines 434-443
- `relation_prf(pred: Sequence[dict], gold: Sequence[dict], *, pred_mentions: Sequence[dict], gold_mentions: Sequence[dict], texts: dict[str, str])` - lines 458-485
- `quote_grounding(pred_quotes: Sequence[str], texts: dict[str, str], gold_spans: Sequence[dict]=(), *, offset_tol: int=5)` - lines 511-551
- `quantity_scores(pred: Sequence[dict], gold: Sequence[dict], *, rel_tol: float=0.02)` - lines 609-635
- `figure_reading_scores(pred: Sequence[dict], gold: Sequence[dict], *, peak_tol: float=0.03)` - lines 755-849
- `ece(confidences: Sequence[float], correct: Sequence[bool], *, bins: int=10)` - lines 853-871
- `cohen_kappa(a: Sequence[Any], b: Sequence[Any])` - lines 875-891
- `krippendorff_alpha_interval(pairs: Sequence[tuple[float, float]], *, rel_tol: float=0.0)` - lines 894-924
- `pairwise_f1(a: Iterable[Any], b: Iterable[Any])` - lines 927-934

**Donor test candidates:** `tests/kg/test_gold_metrics.py`

## Q-05 - als_knowledge_agent/src/als_knowledge_agent/gold/parsers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/parsers.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e2d3d5e01ba0b699d79e6048b056f1ff08fdcc771dd348acc5f423cc845377ea`

Optional parser bake-off; preserve one normalized output contract and coordinate system. External CLI execution requires explicit configuration.

**Verified symbols:**

- `Parsed` - lines 56-82
- `Parser` - lines 85-92
- `DoclingCachedAdapter` - lines 186-203
- `DoclingConfiguredAdapter` - lines 206-221
- `PyMuPDF4LLMAdapter` - lines 224-272
- `GrobidAdapter` - lines 275-293
- `VlmReaderAdapter` - lines 296-308
- `CliAdapter` - lines 311-325
- `score_structure(parsed: Parsed, gold: dict[str, Any], *, gold_text: str='', gold_references: list[dict[str, Any]] | None=None)` - lines 398-465
- `bundle_pdf(bundle_dir: Path, meta: dict[str, Any])` - lines 468-479
- `run_parser(name: str, pdfs: list[Path], *, in_process: bool=False)` - lines 499-541
- `run_bench(gold: Path | None, parser_names: list[str], *, include_drafts: bool=False, in_process: bool=False)` - lines 577-647
- `print_bench(report: dict[str, Any])` - lines 650-689

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-06 - als_knowledge_agent/src/als_knowledge_agent/gold/preannotate.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/preannotate.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e87edefcc40c98e6e1274647903bc7a11bf4300a569f3236f2877fa428cefa0a`

Machine annotations remain draft until independent human adjudication.

**Verified symbols:**

- `build_draft(artifact_dir: Path, *, doi: str, title: str='', stratum: str='', licence: str='', redistributable: bool | None=None, pdf: Path | None=None, out_dir: Path | None=None)` - lines 569-676
- `write_draft(bundle: Bundle, out_dir: Path, *, artifact_dir: Path, force: bool=False)` - lines 679-712

**Donor test candidates:** `tests/kg/test_gold_preannotate.py`

## Q-07 - als_knowledge_agent/src/als_knowledge_agent/gold/schema.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/schema.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4b86a8ad63654a1033ad7686dcf514297b1c08621bd50c4d76fb0774bff3d4ad`

Benchmark schema is not production scientific ontology. Map annotation concepts to F2W without changing gold after inspecting held-out predictions.

**Verified symbols:**

- `Span` - lines 146-157
- `Box` - lines 160-172
- `SourceParse` - lines 176-183
- `BundleMeta` - lines 186-232
- `SectionText` - lines 236-246
- `Heading` - lines 250-253
- `Panel` - lines 256-260
- `FigureBox` - lines 263-269
- `TableBox` - lines 272-280
- `Structure` - lines 283-290
- `Reference` - lines 294-299
- `Mention` - lines 303-314
- `Relation` - lines 317-328
- `Sample` - lines 332-337
- `Measured` - lines 341-349
- `RsoxsSpec` - lines 441-446
- `AxisGold` - lines 470-477
- `PeakGold` - lines 480-485
- `PanelGold` - lines 488-495
- `QuoteGold` - lines 499-520
- `json_schemas` - lines 540-546

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-08 - als_knowledge_agent/src/als_knowledge_agent/gold/selection.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/selection.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ba2b80f90ae6c19d256d1dd464f76f81a722f696a7c2e75d390d779b5d9b87ff`

Stratify independent Work families, parser challenges and measurement types; keep versions in same split. Proposed manifests are not approved gold.

**Verified symbols:**

- `title_key(title: str)` - lines 129-130
- `features_from_text(*, title: str, abstract: str, sections: Iterable[tuple[str, str]], captions: Iterable[str], limit_chars: int=400000)` - lines 133-176
- `Constraint` - lines 181-184
- `Stratum` - lines 188-195
- `paper_constraints(paper: dict[str, Any])` - lines 263-304
- `eligible_strata(paper: dict[str, Any])` - lines 307-329
- `licence_rank(licence: str)` - lines 332-339
- `select(papers: list[dict[str, Any]], *, seed: str='als-gold-v1', n_alternates: int=3)` - lines 355-495
- `public_manifest(manifest: dict[str, Any])` - lines 516-531
- `corpus_features(db: Path, *, limit_chars: int=400000)` - lines 535-638
- `link_preprints(papers: list[dict[str, Any]], *, min_similarity: float=0.9)` - lines 641-661

**Donor test candidates:** `tests/kg/test_gold_selection.py`

## Q-09 - als_knowledge_agent/src/als_knowledge_agent/gold/stages.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold/stages.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `f9a98dd4eb566f5cf3f6439aa025093d32879627327ca87bf96bdc2828a911ac`

Retain structure/citation/mention/figure evaluations; distinguish inferred outputs from source-reported quantities.

**Verified symbols:**

- `score_structure(pred: Bundle, ref: Bundle)` - lines 29-59
- `aggregate_structure(rows: list[dict[str, Any]])` - lines 62-79
- `score_citations(pred: Bundle, ref: Bundle)` - lines 83-84
- `aggregate_citations(rows: list[dict[str, Any]])` - lines 87-90
- `score_mentions(pred: Bundle, ref: Bundle)` - lines 94-134
- `aggregate_mentions(rows: list[dict[str, Any]])` - lines 137-145
- `score_figure_reading(pred: Bundle, ref: Bundle)` - lines 170-171
- `aggregate_figure_reading(rows: list[dict[str, Any]])` - lines 174-180
- `agreement(a: Bundle, b: Bundle)` - lines 193-290

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-10 - als_knowledge_agent/evals/build_scientific_benchmark.py

**Receiving-source anchors:** B35 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/build_scientific_benchmark.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `67ca7e86b2743669a71dbea250e56876fd4609be591030c48f28ebe6e09acf69`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-11 - als_knowledge_agent/evals/build_term_gold.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/build_term_gold.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `7225ba10758b611e3251b3b03e95d83fcd3d55e30f9dffec76a6dd88af877477`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-12 - als_knowledge_agent/evals/gold_annotator.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/gold_annotator.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `deb22caa693f65ddb062b7edb5f8dedb862f0dade6e4b4f62d6dc45df546a883`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-13 - als_knowledge_agent/evals/preannotate_gold.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/preannotate_gold.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `278282cac6ad60394a542765fa9e2b5b5ddc681b298bdd7235cfc48c1bd7d30c`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-14 - als_knowledge_agent/evals/select_gold_papers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/select_gold_papers.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `c4ad64c06be096e5b7874af80154df0a651bdadbf48d7bfd9c98df3acbc4f536`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-15 - als_knowledge_agent/evals/validate_gold.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/validate_gold.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `2d0204694be44efc12f214419bc303b50e350e8f7baf0fb937eb314bdfcd3b83`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-16 - als_knowledge_agent/evals/run_citations.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_citations.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `d3f847cc71b0f303d26aa91843d079893f5822bda30959372e143d6e80e633cd`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-17 - als_knowledge_agent/evals/run_end_to_end.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_end_to_end.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `353271fd6256c43a721eaaf558e10d85d76b5bac72ebb6bccef675985098a942`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-18 - als_knowledge_agent/evals/run_extraction_accuracy.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_extraction_accuracy.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e8fa62f222b35a2fb30e6afe94a773612b2c277d0439ab32cea713eb375658a5`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-19 - als_knowledge_agent/evals/run_figure_reading.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_figure_reading.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `d424c23ec3ae655e668a4da022739bf8f61c0105f626317d7e33f3ddc75322c9`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-20 - als_knowledge_agent/evals/run_golden_papers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_golden_papers.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `3e9ee6cd712d0f4d908352de3a1d313c94a2a8f8e8656491511a586800aa02c4`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-21 - als_knowledge_agent/evals/run_mentions.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_mentions.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e751f66c68b52e266f0f9f180145e7f108077e0120927d55a74eaf458f294c2f`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-22 - als_knowledge_agent/evals/run_paper_recall.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_paper_recall.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `96449a308f149308da8b4d49e2a39f08556e11a6cf9b5805e82cf0d2c059ef1d`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-23 - als_knowledge_agent/evals/run_structure.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_structure.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `270960391804247ea73578ff979f9dc2bcbde579a6213d13bffe6eb6346473f5`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## Q-24 - als_knowledge_agent/evals/run_term_canonicalization.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/run_term_canonicalization.py`
**Disposition:** adapt-runner | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `7cc2a36b203db889c31609aeb92d4c73946012c65d87676dbf3314d9ce1855e2`

Retain literature-specific harness behavior; replace donor app/import assumptions and baseline paths. Inspect run_end_to_end and run_structure for experimental portions; do not copy simulation tests into F2W.

**Verified symbols:**


**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## L-20 - als_knowledge_agent/src/als_knowledge_agent/graph/web_search.py

**Receiving-source anchors:** B22 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/providers/web_search.py`
**Disposition:** adapt | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `b333b397cfcadf53c61f760aacb80a82251a37fce9198960230b2d3c6aae25a6`

Retain search_all, Crossref/arXiv/DDG provider coverage and attribution as discovery only. Preserve partial-provider error details rather than disguising total outage as no results. F2W QA policy controls whether/how external uningested results appear. No implicit download, extraction or promotion.

**Verified symbols:**

- `_duckduckgo_results(query: str, max_results: int)` - lines 34-67
- `_do_web_search(query: str, max_results: int)` - lines 70-75
- `_do_crossref_search(query: str, rows: int)` - lines 86-128
- `_do_arxiv_search(query: str, max_results: int)` - lines 140-188
- `search_all(query: str, max_results: int=5)` - lines 197-209

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## R-11 - als_knowledge_agent/src/als_knowledge_agent/graph/llm_defaults.py

**Receiving-source anchors:** B10 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/llm_adapters.py`
**Disposition:** reference-only | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e702bcc986e3296a90801fcaabc9ca3f4a7cb2086c300c7699ee444e5eedc4cb`

Audit default_ollama_model and import-time settings hazards. Do not port DEFAULT_OLLAMA_MODEL or replace F2W provider settings. Existing F2W classes/configuration remain authority; capability adapters are new thin code, not a new inference stack.

**Verified symbols:**

- `default_ollama_model` - lines 41-53

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## U-11 - als_knowledge_agent/src/als_knowledge_agent/graph/codex_sol_resolution.py

**Receiving-source anchors:** B11 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/curation/review_schema.py`
**Disposition:** extract-contract-replace-transport | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `fa39f4f0628095417aecce7401c56d604246f99a9b71e1eb39170ba37cda3ae6`

Preserve answer_schema and strict review-result contract; adapt review workflow through the existing F2W LLM interface. Record CodexSolResolver as a donor transport that is replaced, not silently inherited. Do not install/invoke Codex, use account credentials, copy hardcoded model names or enable an alternate provider. No loss of review/lease/evidence behavior; provider-specific diagnostic compatibility belongs in an explicit migration note.

**Verified symbols:**

- `answer_schema(grouped: bool)` - lines 19-45
- `CodexSolResolver` - lines 48-173
- `CodexSolResolver._request(self, payload: dict[str, Any])` - lines 62-173

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-01 - scripts/audit_paper_cache.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/cache_audit.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `6bfc0ae6d1d91923029b7be418cf831e9f5bc7df2ab30e5953e78962f5b45e8b`

Hash/duplicate reports and cache manifest; do not equate duplicate bytes with canonical Work identity. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `main` - lines 50-121

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-02 - scripts/backfill_bibliography.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/bibliography_backfill.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `f6d819490d2871ae0752fe750b5a85f4ef9bad56faeab2aa4cf7fd55a0b93517`

Retain candidate selection and resumable metadata backfill; writes use scoped Tiled graph ports and approved publication. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `select_candidates(publications: list[dict[str, Any]], newest: dict[str, dict[str, Any]], *, refresh: bool=False)` - lines 60-85
- `run(publications: list[dict[str, Any]], newest: dict[str, dict[str, Any]], resolver: BatchResolver, *, apply: bool=False, refresh: bool=False, limit: int | None=None, client: Any=None)` - lines 88-145
- `main(argv: list[str] | None=None)` - lines 148-179

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-03 - scripts/bench_parsers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `evals/bench_parsers.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `532dc8ffe182a637355e69ef461fa76707c862877501fb95e6f7ca6ddc11cbe4`

Retain parser structure/time/memory benchmarking and gold-vs-draft separation. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `main(argv: list[str] | None=None)` - lines 37-71

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-04 - scripts/build_vocabulary_v3.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/vocabulary_build.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `c4c60db690873a6d838ed75e6d1ecd72d5c4875fb9c190c943846ccc67a443c6`

Retain all deterministic merge gates and vocabulary proposal outputs. No automatic vocabulary version activation. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `slugify(text: str)` - lines 53-70
- `gated_merges(proposal: Path)` - lines 73-96
- `merge_into(vocab: dict[str, Any], merges: list[dict[str, Any]])` - lines 99-145
- `main(argv: list[str] | None=None)` - lines 148-180

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-05 - scripts/cluster_candidate_terms.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/campaign/proposals.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `ba684c6e80bf1bf52a653d225b6c65960a249926c0735cca12bf848c29b316fc`

Preserve grouping/adjudication reports; replace raw catalog SQL and direct Ollama/vLLM transports with GraphPort and existing F2W inference adapters. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `read_terms(db: Path, *, limit: int=0)` - lines 66-134
- `main(argv: list[str] | None=None)` - lines 137-283

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-06 - scripts/ingest_paper_folder.py

**Receiving-source anchors:** B36 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/cli.py + app/modules/jobs/folder_ingest.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `0a2760b5ec8aebac0ddb4f5447a2ab14939a3a023fded4ab700f3c4aac4f781a`

Preserve folder intake, status reporting, resume and local-file checks. Local CLI calls application service directly; remote operator mode may use supported FastAPI client. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `check_server(url: str, *, timeout: float=30.0)` - lines 69-91
- `add_publication(url: str, doi: str, *, parse_pdf: bool=True, extract_entities: bool=True, model: str | None=None, timeout: float=600.0, allow_without_openalex: bool=False)` - lines 94-137
- `main(argv: list[str] | None=None)` - lines 140-318

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-07 - scripts/ingestion_monitor.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/operations/ingestion_monitor.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `fbbecd07e30af62964834634b61c06f4a731781e1a916610aef3e7fd7aa2c4cd`

Preserve monitoring/status/last-good response and explicitly authorized stop controls. Keep optional Streamlit frontend separate from core imports; existing F2W UI/API can consume the same DTOs. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `_headers` - lines 35-36
- `_get(base: str, path: str, *, params: dict[str, Any] | None=None)` - lines 39-42
- `_post(base: str, path: str)` - lines 45-48
- `_fetch_cases(base: str, state: str, limit: int, route: str | None=None)` - lines 52-61
- `_safe_fetch(base: str, name: str, path: str)` - lines 64-68
- `fetch_dashboard(base: str)` - lines 71-92
- `_duration(seconds: float | int | None)` - lines 95-101
- `_verdict_rows(verdicts: list[dict[str, Any]])` - lines 104-131
- `_evidence_excerpt(evidence: str, surface: str, width: int=280)` - lines 134-146
- `_cborg_examples(cases: list[dict[str, Any]], limit: int=8)` - lines 149-176
- `_status_header(status: dict[str, Any], errors: dict[str, str])` - lines 179-192
- `_overview(status: dict[str, Any], telemetry: dict[str, Any])` - lines 195-270
- `_papers(status: dict[str, Any])` - lines 273-304
- `_uncertainty(base: str, summary: dict[str, Any], ingest: dict[str, Any])` - lines 307-431
- `_inspector(base: str)` - lines 434-468
- `_services(telemetry: dict[str, Any], status: dict[str, Any])` - lines 471-490
- `render_dashboard` - lines 513-540

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-08 - scripts/paper_graph_audit.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/graph/paper_audit.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `45fe99720c81131651a2a85b41373462be5db788d961803156b4af9a16617447`

Audit missing publication/sections/mentions after interrupted ingest. Replace direct catalog database access with complete literature-scoped graph queries; PDF-cache presence alone is not successful ingestion. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `_fold(value: str)` - lines 53-54
- `_doi(value: Any)` - lines 57-58
- `audit(catalog: Path, cache_dir: Path, candidates: Path | None=None)` - lines 61-136
- `main(argv: list[str] | None=None)` - lines 139-162

**Donor test candidates:** `tests/scripts/test_paper_graph_audit.py`

## O-09 - scripts/reextract_failed_papers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/extraction/failed_repair.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `938a096a54bbbeda02d227e022e8c193d03427044688c9db0112bfd4de11098f`

Preserve targeted failed-extraction repair without redoing complete parses. Use existing F2W classes, job API and reviewed plans; do not preserve hardcoded service/model endpoints. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `audit(db: Path)` - lines 67-129
- `main(argv: list[str] | None=None)` - lines 132-206

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-10 - scripts/refetch_paper_cache.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/cache_recovery.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `cc44a345b5005abfffe6267592574557e86e6f692e9d4e18136e7f51a2c45ded`

Preserve manifest-based source recovery, per-paper results, resumability and download verification. Fetching is separate from ingestion/publication. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `load_dois(path: Path)` - lines 63-82
- `fetch_one(entry: dict[str, Any], *, apply: bool)` - lines 85-118
- `main` - lines 121-179
- `_tally(results: list[dict[str, Any]])` - lines 182-187

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-11 - scripts/reresolve_candidate_terms.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/canonicalization/reresolve.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `be51a3afed2e89ac96f4b89cfdf2b230a30f39af88506f4a4bd864a571ab9ae3`

Preserve propose/summarize and reviewed apply behavior. No raw SQL writes; retain relation/evidence references, prevent stale approvals and canonical identity collisions. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `load_candidates(db: Path, limit: int=0)` - lines 57-90
- `propose(candidates: list[dict[str, Any]])` - lines 93-128
- `summarize(candidates: list[dict[str, Any]], proposed: list[dict[str, Any]])` - lines 131-141
- `_canonical_index(store: Any, entity_type: str)` - lines 144-166
- `apply_proposal(proposed: list[dict[str, Any]], store: Any=None)` - lines 169-261
- `main(argv: list[str] | None=None)` - lines 264-294

**Donor test candidates:** No mapped test candidate; add characterization/contract tests.

## O-12 - scripts/resolve_corpus_metadata.py

**Receiving-source anchors:** B23 See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/corpus_metadata.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `e8a2c94c858615284e5de4a762dce357c76a8f422b89a67973c1a1803f7389bd`

Preserve title-agreement checks, lookup planning, batched metadata enrichment and quota accounting; metadata remains separately attributed. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `_content_words(text: str)` - lines 72-78
- `title_agrees(title: str, head: str, needed: int=_TITLE_WORDS_NEEDED)` - lines 81-111
- `plan_lookups(papers: list[dict[str, Any]])` - lines 114-139
- `_record(paper: dict[str, Any], work: dict[str, Any] | None, status: str)` - lines 142-183
- `resolve_papers(papers: list[dict[str, Any]], *, batch: int=BULK_DOI_BATCH)` - lines 186-245
- `summarize(rows: list[dict[str, Any]])` - lines 248-265
- `main(argv: list[str] | None=None)` - lines 268-288

**Donor test candidates:** `tests/scripts/test_resolve_corpus_metadata.py`

## O-13 - scripts/scan_corpus.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/corpus_scan.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `4a6cc784c9a796f77af01973c1b2a1c143f21e6d66d5230a92baa91d033b0d34`

Preserve broad technique profiling, DOI candidates, dedupe and scan reports. No forced scattering-only corpus; read scanned files within allowed roots. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `_load_scattering_scanner` - lines 65-81
- `_build_pattern` - lines 229-282
- `doi_candidates_from_stem(stem: str)` - lines 312-347
- `profile_text(text: str)` - lines 350-369
- `scan_pdf(path: Path, max_pages: int=DEFAULT_MAX_PAGES)` - lines 372-434
- `_scan_one(args: tuple[Path, int])` - lines 437-440
- `dedupe(records: list[dict[str, Any]])` - lines 443-465
- `scan_directory(root: Path, max_pages: int=DEFAULT_MAX_PAGES, limit: int | None=None, workers: int=8)` - lines 468-510
- `summarize(records: list[dict[str, Any]])` - lines 513-533
- `main(argv: list[str] | None=None)` - lines 536-560

**Donor test candidates:** `tests/scripts/test_scan_corpus.py`

## O-14 - scripts/scan_scattering_papers.py

**Receiving-source anchors:** New/incoming paper capability; no direct baseline replacement selected. See F2W_BASELINE_AND_CROSSWALK.md. Proposed filename existence is separately recorded in port_manifest.json; a matching filename does not prove behavioral equivalence.

**Target:** `app/modules/literature/scattering_profile.py`
**Disposition:** adapt-operator-workflow | **Release obligation:** required_paper_capability_or_supporting_contract
**Source SHA-256:** `d98dbd60e3b4b1d5af9f929f4147516afb40fc571fddc706fa7e15b4d16b6e27`

Retain scattering-specific content scoring as optional corpus profiling, not exclusionary default for F2W literature and not experimental analysis. Thin CLI/UI wrappers remain under scripts/ where appropriate. Validate exact branch destinations at H0; reuse existing F2W equivalents rather than duplicate.

**Verified symbols:**

- `fold(value: object)` - lines 83-89
- `recover_doi(text: str, stem: str)` - lines 92-119
- `scan_pdf(path: Path, max_pages: int=DEFAULT_MAX_PAGES)` - lines 122-174
- `scan_directory(root: Path, max_pages: int=DEFAULT_MAX_PAGES, limit: int | None=None)` - lines 177-205
- `summarize(records: list[dict[str, Any]])` - lines 208-220
- `main(argv: list[str] | None=None)` - lines 223-241

**Donor test candidates:** `tests/scripts/test_scan_scattering_papers.py`



## v4 implementation-policy overlay

Source paths, hashes and symbols above remain snapshot evidence. Current decisions D12-D20 govern their use: both managed/central Tiled with app-owned disposable tests; binary acceptance; preserved frontend model selection with shared all-provider limits; agent-private source documents/figures and user-safe projections; explicit schema assets/validation without automatic semantic reclassification; reusable Knowledge Operations. A source function that serves PDF/text/crops is not thereby approved as a public endpoint. See MIGRATION_PLAN.md and CONTENT_ACCESS_POLICY.md.
