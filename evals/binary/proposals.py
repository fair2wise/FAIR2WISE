"""Proposed oracle bindings and owner-decision updates for the 108 companion cases.

Everything here is a PROPOSAL for H3. Nothing in this module approves a case. Two data
sets feed ``import_companion.py``:

* ``ORACLES``: how each case could be executed today (existing test selectors), which
  cases are ``blocked`` (with the missing capability or task), and which need a
  model rubric (``needs_h3_rubric``) or an owner-supplied definition;
* ``CHANGES``: the only expectation changes, each citing a recorded owner decision.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

# --- test files used as deterministic oracles -------------------------------------------------
SF = "tests/security/test_security_fastapi.py"
SG = "tests/security/test_security_guards_routes.py"
SR = "tests/security/test_security_settings_resolver.py"
ST = "tests/security/test_security_tiled_adapter.py"
SW = "tests/security/test_security_workers_approvals.py"
WA = "tests/api_wiring/test_wiring_als_guards.py"
WO = "tests/api_wiring/test_wiring_opaque_paths.py"
WQ = "tests/api_wiring/test_wiring_quotes.py"
WS = "tests/api_wiring/test_wiring_startup.py"
CT = "tests/contracts/test_shared_contracts.py"
CU = "tests/contracts/legacy_ui/test_legacy_ui_contract.py"
JK = "tests/contracts/json_kg/test_json_kg_contract.py"
DA = "tests/disclosure/test_disclosure_access.py"
DP = "tests/disclosure/test_disclosure_payloads.py"
DQ = "tests/disclosure/test_disclosure_quotes.py"
DR = "tests/disclosure/test_disclosure_reconstruction.py"
EV = "tests/evidence/test_evidence.py"
XF = "tests/extraction/test_forge_strategies.py"
XL = "tests/extraction/test_legacy_parity.py"
XV = "tests/extraction/test_validation_stage.py"
GJ = "tests/graph/test_graph_json_backend.py"
GC = "tests/graph/test_graph_tiled_client.py"
GS = "tests/graph/test_graph_tiled_server.py"
GI = "tests/graph/test_graph_identity.py"
OS = "tests/operations/test_paper_summary_service.py"
OT = "tests/operations/test_paper_summary_tool.py"
OR = "tests/operations/test_operation_reports.py"
OC = "tests/operations/test_corpus_audit.py"
OW = "tests/operations/test_work_identity.py"
OG = "tests/operations/test_graph_operations.py"
GO = "tests/graph/test_graph_operations_tiled.py"
OSP = "tests/operations/test_scale_population.py"
OSC = "tests/operations/test_scale_completeness.py"
OST = "tests/operations/test_scale_tiled.py"
ORW = "tests/operations/test_repeated_works.py"
OFE = "tests/operations/test_figure_evidence.py"
ONC = "tests/operations/test_no_code_generation.py"
OND = "tests/operations/test_corpus_near_duplicates.py"
RPP = "tests/read_path/test_read_path_parity.py"
RPQ = "tests/read_path/test_qa_route.py"
RPT = "tests/read_path/test_read_tool.py"
RPA = "tests/read_path/test_application.py"
TD = "tests/tiled/test_tiled_deploy_checks.py"
TC = "tests/tiled/test_tiled_deploy_config.py"
TL = "tests/tiled/test_tiled_server_lifecycle.py"
TK = "tests/tiled/test_tiled_server_cli.py"
TX = "tests/tiled/test_tiled_capabilities_0218.py"
FP = "tests/vision/test_figure_pipeline.py"
VK = "tests/vision/test_kernels.py"
VO = "tests/vision/test_orientation_tables.py"
VT = "tests/vision/test_tools.py"
SN = "tests/science/test_normalize.py"
PS = "tests/settings/test_personal_settings.py"
LA = "tests/test_llm_admission.py"
CL = "tests/test_cborg_limiter.py"
RA = "tests/test_retrieval_agent.py"
PE = "tests/test_paper_evidence_agent.py"
F2 = "tests/test_f2w_api.py"
RS = "tests/test_rag_system_prompt.py"
RX = "tests/evals/test_route_regex.py"
XS = "tests/test_xray_schema.py"
PV = "tests/test_prov.py"
J2 = "tests/test_json2kg.py"
J2E = "tests/test_json2kg_extended.py"
RTA = "tests/runtime/test_runtime_approvals.py"
RTE = "tests/runtime/test_runtime_executor.py"
RTS = "tests/runtime/test_runtime_supervisor.py"
PK = "tests/packaging/test_public_api.py"
PR = "tests/packaging/test_resources.py"
LS = "tests/literature/test_staging.py"
CO = "tests/canonicalization/test_canon_online.py"
RLH = "tests/releases/test_release_happy_path.py"
RLR = "tests/releases/test_release_refusals.py"
RLE = "tests/releases/test_release_evidence_and_cli.py"
RLP = "tests/releases/test_release_no_push.py"
# Landed after the H3 packet (rebound 2026-10-29, plan_addenda A5 AUD-07/A5-2):
GIF = "tests/graph_import/test_import_flow.py"  # T08B
GIG = "tests/graph_import/test_import_guards.py"  # T08B
GIT = "tests/graph_import/test_import_t22c_node_types.py"  # T08B/T22C
JBC = "tests/jobs/test_crash_matrix.py"  # T10
JBP = "tests/jobs/test_pipeline.py"  # T10
JBU = "tests/jobs/test_publication.py"  # T10
JBW = "tests/jobs/test_workers.py"  # T10
SNR = "tests/snapshots/test_release_verification.py"  # T23B
SNC = "tests/snapshots/test_snapshot_create.py"  # T23B
IMC = "tests/images/test_images_context.py"  # T24B
IMD = "tests/images/test_images_dockerfile.py"  # T24B
IMP = "tests/images/test_images_preflight.py"  # T24B
IMF = "tests/images/test_images_profiles.py"  # T24B
IMT = "tests/images/test_images_tiled_service.py"  # T24B
IMS = "tests/images/test_images_supervisor.py"  # T24B
GWP = "tests/gateway/test_gateway_prefix.py"  # T24C
GWM = "tests/gateway/test_mkdocs_image_config.py"  # T24C
CIW = "tests/ci/test_workflows.py"  # T24D
CIR = "tests/ci/test_release_scripts.py"  # T24D
SLA = "tests/security_live/test_live_als_app.py"  # T25C
SLI = "tests/security_live/test_live_identity.py"  # T25C
SLL = "tests/security_live/test_live_local_no_login.py"  # T25C
SLU = "tests/security_live/test_live_units.py"  # T25C
SLW = "tests/security_live/test_live_writer.py"  # T25C
V1A = "tests/api_v1/test_v1_als.py"  # T14
V1C = "tests/api_v1/test_v1_cli_units.py"  # T14
V1L = "tests/api_v1/test_v1_local.py"  # T14
DST = "tests/deployment/test_deploy_static.py"  # T24E: Ansible handoff, no container
DSA = "tests/deployment/test_deploy_state_archive.py"  # T24E: restore/rollback harness
V1T = "tests/api_v1_live/test_v1_live_tiled.py"  # T14
# T19E operation surfaces (agent Tool, supervisor route, /v1/operations, SDK, CLI, Tiled):
OKS = "tests/operations/test_knowledge_surfaces.py"  # T19E
V1O = "tests/api_v1/test_v1_operations.py"  # T19E
GKT = "tests/graph/test_knowledge_surfaces_tiled.py"  # T19E
SCF = "tests/schema/test_schema_fingerprint.py"  # T22B
SCR = "tests/schema/test_schema_registry.py"  # T22B
CLA = "tests/contracts/legacy_ui/closure/test_closure_api.py"  # T18C
CLT = "tests/contracts/legacy_ui/closure/test_closure_tiled.py"  # T18C
TILED_FIXTURE = ["tests/fixtures/tiled_server.py"]


def n(file: str, *names: str) -> List[str]:
    """Selectors ``file::name`` for each name (a name covers all parametrised variants)."""
    return [f"{file}::{name}" for name in names]


def whole(*files: str) -> List[str]:
    """Selectors that cover every test in the given files."""
    return list(files)


def oracle(
    selectors: Sequence[str],
    coverage: str,
    covers: str,
    uncovered: str = "",
    supporting: Sequence[str] = (),
    fixtures: Sequence[str] = (),
) -> Dict[str, Any]:
    """An executable deterministic oracle built from existing tests."""
    return {
        "readiness": "executable",
        "oracle_type": "deterministic_assertion",
        "coverage": coverage,
        "selectors": list(selectors),
        "covers": covers,
        "uncovered": uncovered,
        "supporting": list(supporting),
        "blocked_by": [],
        "fixtures": list(fixtures),
    }


def blocked(blocked_by: Sequence[str], supporting: Sequence[str] = (), note: str = "") -> Dict[str, Any]:
    """A case that cannot run until the named capability/task exists."""
    return {
        "readiness": "blocked",
        "oracle_type": "deterministic_assertion",
        "coverage": "none",
        "selectors": [],
        "covers": "",
        "uncovered": note or "oracle not yet implementable",
        "supporting": list(supporting),
        "blocked_by": list(blocked_by),
        "fixtures": [],
    }


def rubric_case(
    rubric: str, blocked_by: Sequence[str], supporting: Sequence[str] = (), note: str = ""
) -> Dict[str, Any]:
    """A semantic case that needs an owner-approved rubric (draft in rubrics.json)."""
    return {
        "readiness": "needs_h3_rubric",
        "oracle_type": "deterministic_plus_rubric",
        "coverage": "none",
        "selectors": [],
        "covers": "",
        "uncovered": note or "semantic judgment needs the H3-approved rubric",
        "supporting": list(supporting),
        "blocked_by": list(blocked_by),
        "fixtures": [],
        "rubric_ref": rubric,
    }


T08B = "T08B (Tiled importer over saved JSON, receipts, writes)"
T23B = "T23B (immutable JSON snapshot service)"
T10 = "T10 (durable jobs and curation)"
T22B = "T22B (schema registry, fingerprint and revision binding)"
T24B = "T24B (build shared runtime and the three image finals)"
T24C = "T24C (prefix-correct gateway and existing UI/API deployment)"
T24D = "T24D (GitHub Actions build/publication and protected tags)"
T24E = "T24E (three-profile container, Ansible and restore integration)"
NOTE_D311 = "the running-container part waits on owner decision D3.11 (tests/deployment/README.md lists the command)"
T26B = "T26B (committed artifact validation and export)"
T26C = "T26C (exact Git snapshot promotion and rollback rehearsal)"
T25C = "T25C (no-login local workflows and real ALS Tiled role enforcement)"
T13 = "T13 (publication and rollback rehearsal)"
T14 = "T14 (FastAPI plus CLI/UI compatibility and job UX)"
T15 = "T15 (FORGE imports F2W agents)"
T18C = "T18C (full frozen-frontend parity on actual Tiled)"
O03 = "O03 (owner approval of the job-settings rule)"
H3GOLD = "H3 (owner-approved gold fixtures and source truth)"
T11B = "T11B (live/fake model execution of approved cases)"

GATES_PUBLIC = n(WA, "test_anonymous_is_refused_except_health", "test_api_docs_stay_public_on_als")

ORACLES: Dict[str, Dict[str, Any]] = {
    # ---- extraction ------------------------------------------------------------------------
    "EXT-01": rubric_case(
        "RUB-EXT",
        [H3GOLD, T11B],
        n(
            XL,
            "test_f2w_specific_fields_survive_normalization",
            "test_default_strategy_is_the_existing_f2w_extractor",
            "test_wrapped_output_equals_direct_output_on_the_synthetic_fixture",
        )
        + n(XF, "test_forge_paper_strategy_returns_the_normalized_contract"),
    ),
    "EXT-02": rubric_case(
        "RUB-EXT",
        [H3GOLD, T11B],
        whole(SN)
        + n(
            EV,
            "test_normalize_quantity_keeps_raw_and_conversion_provenance",
            "test_units_refuse_rather_than_guess",
            "test_record_reported_quantities_keeps_uncertainty_confidence_and_raw_units_apart",
        ),
    ),
    "EXT-03": rubric_case(
        "RUB-EXT",
        [H3GOLD, T11B],
        n(
            EV,
            "test_unlocated_and_label_only_evidence_carry_missing_markers_not_invented_spans",
            "test_a_miss_has_no_offsets",
            "test_section_page_unknown_is_none_not_zero",
        )
        + n(XF, "test_structure_unknown_is_none_not_negative")
        + n(FP, "test_unit_missing_everywhere_is_withheld_not_defaulted"),
    ),
    "EXT-04": oracle(
        n(
            EV,
            "test_build_evidence_ties_a_quote_to_source_version_and_location",
            "test_exact_quote_has_offsets_into_the_stored_text",
            "test_a_miss_has_no_offsets",
            "test_locate_in_any_section_prefers_exact_then_shortest_and_flags_ties",
        )
        + n(
            XV,
            "test_every_retained_assertion_traces_to_source_version_and_location",
            "test_document_validation_attaches_evidence_refs_and_withholds_lexical_failures",
        ),
        "partial",
        "Locator and Work/source-version resolution on synthetic fixtures",
        "Gold Work/locator pairs on authorized real papers (H3 fixtures)",
        fixtures=["tests/evidence/evidence_fixtures.py"],
    ),
    "EXT-05": oracle(
        n(XL, "test_completeness_is_reported_as_unknown_not_success")
        + n(XV, "test_withheld_cases_never_reach_the_sink", "test_h3_gates_are_closed_and_enforced")
        + n(LS, "test_incomplete_extraction_is_never_importable")
        + n(
            JBU,
            "test_incomplete_packages_are_quarantined_with_a_reason_and_block_publication",
            "test_staged_package_check_reads_real_paper_json_headers",
        )
        + n(JBC, "test_case_02_crash_during_extraction_retains_chunks_and_incomplete_coverage_blocks_publication")
        + n(RLR, "test_validation_json_must_be_present_and_agree"),
        "full",
        "Incomplete extraction is reported unknown, is not importable from a staged package, is quarantined by"
        " the publication job with a reason, and a snapshot whose validation does not agree is refused before"
        " commit (T10 jobs and T26B release checks landed after the H3 packet)",
    ),
    # ---- chat -----------------------------------------------------------------------------------
    "QA-01": rubric_case(
        "RUB-QA",
        [H3GOLD, T11B],
        whole(RPP)
        + n(
            PE,
            "test_invalid_claim_citations_get_one_repair_pass",
            "test_out_of_bounds_or_missing_citations_fail_closed",
        )
        + whole(RS),
        note="Whether each answer's claims are supported by its cited evidence is semantic",
    ),
    "QA-02": oracle(
        n(
            OT,
            "test_no_active_paper_is_not_found_not_empty",
            "test_pinned_scope_wins_and_reaches_the_async_worker_thread",
        )
        + n(
            PE,
            "test_targeted_query_reads_only_manifest_selected_pages",
            "test_full_query_sends_at_most_eight_ranked_pages",
        )
        + n(RPT, "test_selected_without_hits_uses_the_single_loaded_graph")
        + n(GJ, "test_scope_blocks_other_corpora"),
        "partial",
        "Active-paper and graph scope bind retrieval and tool reads to eligible sources",
        "Chat answers on gold questions staying within the cited active paper (needs rubric)",
    ),
    "QA-03": rubric_case(
        "RUB-QA",
        [H3GOLD, T11B],
        whole(RX)
        + n(
            RA,
            "test_numeric_claim_re_does_not_match_beamline",
            "test_judge_system_distinguishes_conceptual_from_numeric",
        )
        + n(
            EV,
            "test_source_support_checks_qualifiers_inside_the_quote",
            "test_source_support_rejects_a_quote_absent_from_the_source",
        ),
        note="Whether a numeric claim is entailed by its evidence is semantic; lexical support is only a screen",
    ),
    "QA-04": rubric_case(
        "RUB-QA",
        [H3GOLD, T11B],
        n(
            RA,
            "test_query_conceptual_insufficient_judge_uses_leeway",
            "test_is_conceptual_question_examples",
            "test_query_conceptual_on_topic_nodes_still_call_judge",
        )
        + n(RPP, "test_leeway_path_is_preserved")
        + n(RPQ, "test_planner_failure_or_no_tool_call_falls_back_to_the_direct_call")
        + n(
            F2,
            "test_json_insufficient_graph_offers_download_cta",
            "test_json_off_topic_dogs_offers_ingest_not_topic_refusal",
        ),
    ),
    "QA-05": oracle(
        n(
            F2,
            "test_agentic_path_prompts_before_download_and_respects_decline",
            "test_agentic_path_downloads_only_approved_candidate_then_answers",
            "test_json_insufficient_graph_offers_download_cta",
            "test_json_insufficient_opv_offers_ingest_not_leeway",
        )
        + n(SG, "test_user_chat_action_yes_denied_before_job_creation")
        + n(CU, "test_side_effects_only_after_approval"),
        "partial",
        "A weak or empty retrieval only offers ingestion; effects need an explicit approval and, on ALS, an admin",
        "Publication half of the rule (covered by AUTH-04/JSON-06 once T08B exists)",
    ),
    # ---- figures --------------------------------------------------------------------------------
    "FIG-01": rubric_case(
        "RUB-FIG",
        [H3GOLD],
        n(
            FP,
            "test_multi_panel_geometry_is_origin_independent",
            "test_evidence_covers_figure_panels_and_reported_values",
            "test_unlocated_figure_is_explicit",
            "test_per_panel_q_ranges_do_not_mix_and_angstrom_is_converted",
        )
        + n(VT, "test_each_reading_tool_returns_panel_locators")
        + n(VK, "test_frame_round_trip_and_degenerate", "test_label_panels_from_spans_by_position"),
        note="Correct figure/panel/coordinates on real authorized figures needs owner-labelled gold",
    ),
    "FIG-02": rubric_case(
        "RUB-FIG",
        [H3GOLD],
        n(
            VK,
            "test_q_axis_reads_text_layer_unit_and_converts_angstrom",
            "test_bar_measure_and_scale_reading",
            "test_q_axis_without_unit_is_not_determined",
        )
        + n(
            FP,
            "test_scale_bar_reported_only_where_measured_and_lengths_assigned_by_panel",
            "test_axis_not_identified_as_q_is_withheld",
        ),
        note="Source-supported scale/q-axis/unit on real authorized figures needs owner-labelled gold",
    ),
    "FIG-03": oracle(
        n(
            VK,
            "test_ambiguous_bars_refused",
            "test_caption_unit_ambiguity_is_withheld",
            "test_q_axis_ocr_unit_never_believed",
        )
        + n(
            FP,
            "test_caption_unit_used_when_panel_states_none_and_ambiguous_units_withheld",
            "test_panel_count_mismatch_blocks_all_values_and_is_surfaced",
            "test_rotated_page_withholds_everything",
            "test_ocr_fallback_spans_are_tagged_and_their_units_never_believed",
        )
        + n(
            VO,
            "test_hedged_negated_or_conflicting_statements_are_withheld",
            "test_inference_candidate_never_becomes_a_reported_observation",
        ),
        "partial",
        "Ambiguous scale, unit, panel count, rotation and OCR readings are withheld, never guessed",
        "Withholding on gold ambiguous figures from real papers (H3 fixtures)",
    ),
    "FIG-04": oracle(
        n(FP, "test_results_carry_no_image_bytes_paths_or_urls")
        + n(
            VT,
            "test_extract_figures_result_locates_figure_and_panels_without_image_material",
            "test_access_denied_without_scope_or_authority_and_unknown_inputs",
            "test_expired_scope_is_denied",
            "test_stored_paper_source_requires_a_stored_verified_pdf",
        )
        + n(
            DA,
            "test_trusted_internal_read_succeeds_for_every_source_kind",
            "test_internal_reads_work_for_every_profile_runtime",
        )
        + n(DP, "test_no_frozen_legacy_route_is_source_serving"),
        "full",
        "Figure material is readable by trusted internal agents only; results and public routes carry no image"
        " bytes, paths or URLs",
    ),
    # ---- operations -----------------------------------------------------------------------------
    "OPS-01": oracle(
        n(
            OS,
            "test_complete_display_when_everything_is_listed",
            "test_same_output_as_the_baseline_function",
            "test_query_scoped_omitted_note_is_not_read_as_the_total",
        )
        + n(
            OC,
            "test_synthetic_corpus_matches_corpus_qa_and_counts_distinct_works",
            "test_committed_corpora_match_the_cli_baseline",
            "test_xray_corpus_histogram_overcount_is_disclosed_and_corrected",
        )
        + n(
            OG,
            "test_statistics_counting_units_duplicates_dangling_and_works",
            "test_legacy_block_matches_analyze_kgs_on_committed_graphs",
            "test_growth_series_matches_analyze_kgs_growth_with_work_denominators",
            "test_empty_graph_is_a_complete_zero_population",
        )
        + n(GO, "test_statistics_match_json_and_exclude_forge_records")
        + n(
            OSP,
            "test_populations_are_far_larger_than_a_model_context",
            "test_graph_statistics_equal_ground_truth_over_the_whole_graph",
            "test_paper_subgraph_totals_equal_ground_truth_while_the_preview_is_cut",
            "test_growth_between_two_large_versions_equals_set_arithmetic",
            "test_corpus_audit_equals_ground_truth_over_every_term_entry",
            "test_paper_summary_counts_every_page_grounded_record",
            "test_every_operation_stays_within_time_and_memory_bounds",
        )
        + n(OST, "test_live_statistics_equal_frozen_json_over_the_whole_graph",
            "test_live_growth_and_subgraph_equal_frozen_json")
        + n(
            OFE,
            "test_summary_counts_figure_and_table_records_once_under_page_eligibility",
            "test_graph_counts_figure_and_table_derived_entities_once",
            "test_corpus_counts_figure_and_table_entries_once",
        )
        + n(OND, "test_pruned_pair_loop_equals_corpus_qa")
        + n(
            ONC,
            "test_no_code_execution_primitive_in_the_operations_or_their_read_path",
            "test_the_wrapped_legacy_summary_runs_no_generated_code",
            "test_operations_over_the_large_populations_touch_no_code_primitive",
            "test_code_shaped_inputs_are_data_never_code",
            "test_specs_are_read_only_and_take_no_code",
            "test_no_code_runner_tool_exists_in_the_application",
            "test_job_dispatch_runs_only_reviewed_operations",
        )
        + n(OFE, "test_no_source_material_in_any_output_or_artifact")
        + n(OKS, "test_supervisor_route_matches_direct_call_on_every_question",
            "test_no_source_material_leaves_any_agent_surface")
        + n(V1O, "test_reads_return_the_agent_tools_bounded_result", "test_statistics_counts_match_the_t19c_units",
            "test_sdk_methods_forward_to_the_same_operations", "test_cli_ops_subcommands_print_the_same_bounded_results",
            "test_no_source_material_leaves_the_routes")
        + n(GKT, "test_statistics_and_recent_through_the_routes_match_the_direct_computation",
            "test_sdk_agent_and_supervisor_agree_with_the_routes_on_tiled"),
        "partial",
        "Population counts of the baseline summary function, the corpus audit (corpus_qa parity) and graph"
        " statistics (analyze_kgs parity, JSON and Tiled) on synthetic and committed populations; T19D: every"
        " operation's totals equal independently computed ground truth on synthetic populations 2-12x a 1M-token"
        " context (60k entities / 191k edges, 60k term entries, 120k session records; 15k live Tiled entities in"
        " parity with frozen JSON), computed by code with no model within measured time and memory bounds;"
        " figure/table-derived (T09) records count exactly once; no output carries source material (D19); the"
        " operations never generate, compile or run code (static AST, runtime tripwires, read-only specs); T19E: the"
        " same counts come back unchanged through the agent Tool, the supervisor route, /v1/operations, the SDK and"
        " the CLI, on JSON and on live Tiled, with no source material on any surface",
        "Known complete-population count on an owner-approved real population (H3 oracle)",
        fixtures=["tests/operations/fixtures/", "tests/operations/scale_population.py"],
    ),
    "OPS-02": oracle(
        n(
            OW,
            "test_repeated_work_references_are_one_work",
            "test_pairwise_rule_matches_harvest_works_same_identity",
            "test_two_journal_dois_are_never_merged_even_with_equal_titles",
        )
        + n(OC, "test_synthetic_corpus_matches_corpus_qa_and_counts_distinct_works")
        + n(OG, "test_statistics_counting_units_duplicates_dangling_and_works")
        + n(
            ORW,
            "test_preprint_versions_and_published_paper_are_one_work_in_a_graph",
            "test_preprint_published_pair_by_title_is_one_work_in_the_corpus_audit",
            "test_different_journal_dois_with_one_title_stay_two_works",
            "test_one_file_under_several_directories_is_one_key_and_one_work",
            "test_renamed_byte_identical_copy_with_the_same_metadata_is_one_work",
            "test_renamed_copy_without_any_identifier_is_disclosed_not_merged",
            "test_byte_identical_duplicate_records_collapse_by_their_unit",
            "test_a_work_reappearing_as_its_preprint_is_not_a_new_work",
            "test_repeated_reference_population_at_scale_counts_each_work_once",
            "test_renamed_copy_with_the_same_content_hash_is_one_work",
            "test_work_identity_2_agrees_with_1_on_a_population_without_hashes",
        ),
        "partial",
        "Repeated Work references (journal PDF, arXiv preprint, path copies) count as one distinct Work under"
        " work-identity/2 (H3 K1 + D1.9); duplicate triples collapse; the legacy histogram overcount is disclosed; T19D:"
        " executable on synthetic repeated-paper populations (preprint/published pairs by DOI label or by title"
        " and first author, later arXiv versions, the 10.48550 stem, one file under several directories, renamed"
        " byte-identical copies with identical metadata, byte-identical duplicate records, a Work reappearing in a"
        " later graph version) in graph statistics, subgraphs, growth and the corpus audit, at 60k scale; a"
        " renamed copy with no identifier stays a disclosed key-only Work unless its metadata carries the file sha256"
        " (content-hash step, work-identity/2, owner decision D1.9); work-identity/2 agrees with /1 on a population"
        " without hashes (parity test)",
        "Owner review of work-identity/2 on a real repeated-paper population (waits for the H3 B4 pilot labels)",
        supporting=n(OS, "test_duplicate_labels_count_as_separate_records"),
        fixtures=["tests/operations/scale_population.py"],
    ),
    "OPS-03": oracle(
        n(
            OS,
            "test_capped_display_is_partial_never_exhaustive",
            "test_query_scoped_omitted_note_is_not_read_as_the_total",
        )
        + n(GJ, "test_find_entities_flags_partial_results")
        + n(GS, "test_partial_scan_is_flagged_not_absent")
        + n(OR, "test_partial_scan_is_never_ok_and_says_so", "test_completeness_follows_the_scan_and_cannot_be_set_independently")
        + n(OC, "test_capped_scan_is_partial_and_names_the_bound", "test_tool_partial_unknown_denied_and_invalid")
        + n(
            OG,
            "test_capped_scan_is_partial_never_complete",
            "test_growth_stops_partial_and_lists_unread_graphs",
            "test_unknown_paper_is_not_found_but_a_partial_scan_is_unknown",
            "test_tool_partial_scope_unknown_and_invalid",
        )
        + n(GO, "test_capped_tiled_scan_is_partial_not_complete")
        + n(OKS, "test_partial_scan_stays_partial_through_direct_service_and_supervisor")
        + n(V1O, "test_partial_inline_result_versus_complete_job")
        + n(GKT, "test_capped_live_scan_is_partial_on_the_routes_and_complete_as_a_job")
        + n(
            OSC,
            "test_inline_bound_below_the_population_is_partial",
            "test_bound_equal_to_the_population_is_complete",
            "test_inline_tool_bound_is_partial_never_ok",
            "test_deadline_stops_a_large_scan_as_partial",
            "test_absent_work_after_a_partial_scan_is_unknown_not_absent",
            "test_capped_corpus_audit_is_partial_and_says_how_much",
            "test_job_covers_the_population_the_inline_bound_cannot",
        )
        + n(OST, "test_capped_live_scan_is_partial"),
        "full",
        "A capped or partial scan is flagged partial/non-exhaustive, never reported as complete; T19D: record"
        " bounds and deadlines below a 60k-entity population give partial/PARTIAL with the exact count read and"
        " the reason, also on live Tiled, and the same operation as a job covers the whole population; T19E: PARTIAL"
        " survives the supervisor route, /v1/operations, the SDK and the CLI while the knowledge_operation job of the"
        " same call is complete",
    ),
    "OPS-04": oracle(
        n(
            OS,
            "test_capped_display_is_partial_never_exhaustive",
            "test_result_is_bounded_with_long_definitions",
            "test_same_output_as_the_baseline_function",
        )
        + n(OT, "test_ok_result_is_typed_bounded_and_cites_eligible_pages")
        + n(OR, "test_display_truncation_keeps_totals_and_completeness")
        + n(OG, "test_paper_subgraph_by_key_doi_and_arxiv")
        + n(
            OSC,
            "test_smaller_result_bounds_shorten_previews_only",
            "test_preview_examples_are_not_the_totals",
            "test_subgraph_node_cap_changes_the_preview_only",
            "test_summary_display_cap_never_changes_term_count",
            "test_query_scoped_summary_keeps_the_full_count",
        )
        + n(
            OSP,
            "test_statistics_tool_result_is_bounded_and_complete",
            "test_corpus_tool_result_is_bounded_and_complete",
            "test_paper_summary_tool_result_is_bounded",
        )
        + n(OST, "test_recent_entities_count_every_live_entity_while_the_preview_is_cut",
            "test_live_tool_result_is_bounded_and_marked_unisolated")
        + n(OFE, "test_disclosure_projection_keeps_every_total")
        + n(GKT, "test_statistics_and_recent_through_the_routes_match_the_direct_computation")
        + n(V1O, "test_reads_return_the_agent_tools_bounded_result"),
        "partial",
        "Display bounding keeps the same output as the baseline and never changes totals or completeness; T19D:"
        " on 60k-entity populations and live Tiled, result bounds, node caps and max_terms shorten previews only,"
        " metrics/coverage/completeness are unchanged, previews are not totals, and the user projection keeps"
        " every total; T19E: the bounded result (display_truncated, full totals) is what /v1/operations, the SDK"
        " and the agent Tool return, on live Tiled too",
        "Totals unchanged under truncation on a gold population (H3 oracle)",
        fixtures=["tests/operations/scale_population.py"],
    ),
    "OPS-05": oracle(
        n(
            OS,
            "test_same_output_as_the_baseline_function",
            "test_page_eligibility_is_the_baseline_rule_including_d10",
        )
        + n(
            OT,
            "test_ok_result_is_typed_bounded_and_cites_eligible_pages",
            "test_spec_is_read_only_free_and_has_no_path_inputs",
        )
        + whole("tests/operations/test_summary_legacy_ui.py"),
        "full",
        "The deterministic paper summary keeps the baseline output and its eligible-page citations",
    ),
    # ---- tiled deployment -------------------------------------------------------------------------
    "TIL-01": oracle(
        n(
            TL,
            "test_stop_retains_data_and_restart_reads_it_back",
            "test_recovery_after_killed_server",
            "test_launch_health_and_capabilities",
        )
        + n(TK, "test_cli_start_check_stop_keeps_data")
        + n(TD, "test_stop_never_signals_an_unowned_pid", "test_status_reports_stale_manifest"),
        "full",
        "Managed Tiled stop/restart preserves catalog data and never signals an unowned process",
        "",
        fixtures=["tests/fixtures/tiled_server.py"],
    ),
    "TIL-02": oracle(
        n(
            TD,
            "test_external_mode_does_not_import_lifecycle",
            "test_connect_external_reads_secret_file_and_never_manages",
            "test_probe_environment_rejects_wrong_version_and_app_env",
            "test_unsupported_version_is_a_clear_error",
            "test_outage_and_5xx_are_target_unavailable",
        )
        + n(TL, "test_external_outage_never_starts_a_server", "test_external_connect_to_fixture_without_lifecycle")
        + n(TK, "test_cli_rejects_lifecycle_on_external_profile"),
        "full",
        "External mode never provisions or stops a server, and fails clearly on version mismatch or outage",
        fixtures=["tests/fixtures/tiled_server.py"],
    ),
    "TIL-03": oracle(
        n(
            TX,
            "test_acl_variant_tag_policy_scopes_graph_reads_and_writes",
            "test_auth_variant_admin_and_reader_principals",
        )
        + whole(ST)
        + n(GS, "test_scoping_and_forge_sentinel_isolation")
        + n(SG, "test_tiled_uri_editable_on_local_only"),
        "partial",
        "Server reader/owner ACL behaviour on the disposable server and the admin-principal parsing of the"
        " application guard",
        "Application scope guard on write paths (T08B writes are not merged)",
        fixtures=["tests/fixtures/tiled_server.py"],
    ),
    "TIL-04": oracle(
        n(
            TX,
            "test_acl_variant_tag_policy_scopes_graph_reads_and_writes",
            "test_auth_variant_reader_can_write_without_access_policy",
        ),
        "partial",
        "Server-side tag policy: a principal writes only its own tagged graph on the disposable 0.2.18 server",
        "Application-level owner write path (T08B)",
        fixtures=["tests/fixtures/tiled_server.py"],
    ),
    "TIL-05": oracle(
        n(
            F2,
            "test_patch_graph_node_updates_label_description_pubs_and_snippets",
            "test_relationship_updates_are_directed_idempotent_and_persisted",
        )
        + n(CLT, "test_json_to_tiled_import_retains_fields_ids_evidence_and_reconciles_edits")
        + n(SLL, "test_local_graph_edit_and_tiled_uri_need_no_identity")
        + n(GIG, "test_acknowledged_edit_is_never_lost_by_an_import", "test_edit_journal_is_durable_append_only_and_ordered"),
        "partial",
        "Node, relationship and snippet edits made through the UI routes are read back; edits are journalled"
        " (append-only, ordered) and reconciled through the Tiled import on a disposable 0.2.18 server (T08B,"
        " T18C, T25C)",
        "A Tiled-admin edit from the frozen browser UI with non-admin denial (D30) on the deployed ALS stack"
        " (T24E)",
        fixtures=TILED_FIXTURE,
    ),
    "TIL-06": blocked(
        [T13],
        n(GS, "test_scoping_and_forge_sentinel_isolation")
        + n(GI, "test_ownership_requires_owner_uri_and_properties_to_agree")
        + n(GIF, "test_changed_snapshot_uses_create_new_revision_and_retires_old")
        + n(JBU, "test_rollback_restores_previous_pointer_and_keeps_candidate_history"),
        "Import ledger writes, revision retirement and pointer rollback are covered in-process (T08B, T10"
        " landed); rollback of an F2W import on a shared Tiled needs the rehearsal (T13)",
    ),
    "TIL-07": oracle(
        n(TC, "test_pin_is_exactly_0218", "test_version_override_must_match_pin")
        + n(TD, "test_probe_environment_rejects_wrong_version_and_app_env")
        + whole(TX),
        "full",
        "The exact interim pin (0.2.18, wheel/sdist hashes) and the capability contract observed on it are"
        " recorded and enforced",
        fixtures=["tests/fixtures/tiled_server.py"],
    ),
    # ---- inference ---------------------------------------------------------------------------------
    "LLM-01": oracle(
        n(
            PS,
            "test_two_principals_keep_independent_settings_and_turns_use_their_own",
            "test_local_settings_stay_server_wide",
        )
        + n(
            RPA,
            "test_delegated_run_uses_f2w_prompt_skill_and_admission",
            "test_default_admission_is_the_shared_provider_admission",
        )
        + n(SLA, "test_reader_personal_settings_are_their_own")
        + n(CLA, "test_ui25_concurrent_sessions_keep_their_own_state"),
        "partial",
        "Per-principal provider/model settings are what a turn uses (also against a real 0.2.18 identity server);"
        " local settings stay server-wide; concurrent frozen-UI sessions keep their own state",
        "The selection made in the frozen browser UI on the deployed ALS stack (T24E)",
        fixtures=TILED_FIXTURE,
    ),
    "LLM-02": oracle(
        n(LA, "test_cborg_clients_fail_closed_without_key", "test_require_cborg_api_key_refuses_openai_fallback")
        + n(CO, "test_chat_judge_uses_existing_client_signature_and_has_no_default")
        + n(XF, "test_extraction_package_constructs_no_provider_and_imports_no_donor_code")
        + n(RPA, "test_delegate_needs_an_injected_chat_model"),
        "full",
        "No automatic donor or paid fallback: clients fail closed and runtime modules construct no provider",
    ),
    "LLM-03": oracle(
        n(
            LA,
            "test_sync_and_async_callers_share_one_cap",
            "test_provider_admission_facade_uses_shared_controller",
            "test_cborg_limiter_routes_to_shared_controller_when_flag_on",
        )
        + whole(CL)
        + n(RTE, "test_call_and_cost_budgets"),
        "partial",
        "Sync and async callers share one provider cap",
        "Background worker/job callers join the same quota (T10)",
    ),
    "LLM-04": oracle(
        n(
            LA,
            "test_rate_limit_starts_cooldown_for_every_caller",
            "test_cancelled_async_waiter_does_not_leak_a_slot",
        )
        + n(
            RTE,
            "test_async_cancellation_propagates",
            "test_transient_failure_retried_then_succeeds",
            "test_deterministic_failures_not_retried",
        ),
        "partial",
        "Rate-limit cooldown, cancellation and no-retry-on-deterministic-failure behaviour in admission and"
        " executor",
        "Provider backoff on real responses and worker cancel (T10, live provider)",
    ),
    "LLM-05": blocked(
        [O03],
        n(JBP, "test_resume_after_input_change_reruns_only_affected_stages"),
        "Durable jobs exist (T10 landed); the owner settings rule for live job settings revisions (O03) is"
        " pending, so the expectation cannot be stated",
    ),
    "LLM-06": blocked(
        [O03],
        n(JBP, "test_unit_keys_change_with_extractor_so_stale_chunks_are_not_reused"),
        "Draft expectation pending O03; durable jobs with stage keys exist (T10 landed), the resolved-model"
        " snapshot rule awaits the owner",
    ),
    "LLM-07": blocked(
        [O03],
        n(JBP, "test_changed_vocabulary_invalidates_only_canonicalization"),
        "Draft expectation pending O03; changed-model resume semantics await the owner rule (T10 landed)",
    ),
    "LLM-08": oracle(
        n(SW, "test_als_worker_stops_on_downgrade_expiry_revocation")
        + n(
            JBW,
            "test_workers_share_quota_with_chat_and_evaluations",
            "test_provider_calls_share_one_admission_controller_across_jobs",
        )
        + n(JBP, "test_authority_revocation_stops_job_and_notifies_scope_hooks")
        + n(V1A, "test_worker_reauthorizes_and_stops_when_the_initiator_lost_admin")
        + n(V1T, "test_real_role_downgrade_stops_the_worker_before_the_effect"),
        "partial",
        "Running jobs draw on the one provider admission controller shared with chat (a tightened cap binds their"
        " next call) and stop before their next effect when the initiator's role is downgraded or revoked, on a"
        " real 0.2.18 role check (T10, T14)",
        "Default production wiring of the worker authority into JobService.register (audit AUD-01, T28A) and a"
        " live provider tightening its limit under a running job (paid run, owner go)",
        fixtures=TILED_FIXTURE,
    ),
    # ---- disclosure ------------------------------------------------------------------------------
    "SEC-01": oracle(
        n(DP, "test_no_frozen_legacy_route_is_source_serving", "test_guessed_source_urls_are_denied")
        + n(
            DA,
            "test_caller_flags_never_grant_source_read",
            "test_full_access_profiles_hold_no_user_surface_source_access",
        )
        + n(SG, "test_route_policy_matches_contract_section_7"),
        "full",
        "No public route serves source PDFs, full text or crops; guessed source URLs are denied",
    ),
    "SEC-02": oracle(
        n(
            DA,
            "test_trusted_internal_read_succeeds_for_every_source_kind",
            "test_internal_reads_work_for_every_profile_runtime",
            "test_grant_expiry_and_revocation",
            "test_scope_limits_kind_corpus_and_work",
        ),
        "full",
        "Trusted internal agent reads of every source kind succeed inside their granted scope",
    ),
    "SEC-03": oracle(
        n(
            DA,
            "test_caller_flags_never_grant_source_read",
            "test_forged_or_tampered_grants_fail_the_seal",
            "test_request_shaped_context_cannot_obtain_a_grant",
            "test_viewer_comes_only_from_server_context",
            "test_source_read_is_not_a_request_capability",
        )
        + n(SF, "test_forged_trust_claims_never_elevate")
        + n(CT, "test_source_read_is_not_a_requestable_capability"),
        "full",
        "A caller-supplied flag, claim or request-shaped context cannot confer source-read authority",
    ),
    "SEC-04": oracle(
        n(
            DP,
            "test_progress_events_drop_private_data_and_keep_graph_shape",
            "test_sse_complete_and_error_events_are_projected",
            "test_trace_projection_has_no_prompt_or_page_text",
            "test_error_messages_are_typed_or_path_free",
            "test_project_exception_never_returns_raw_str_exc",
            "test_trace_route_gives_admins_and_local_raw_and_others_the_summary",
        )
        + n(WA, "test_user_trace_is_projected_and_admin_trace_is_raw")
        + n(WS, "test_local_trace_is_raw_and_annotator_is_the_local_operator"),
        "full",
        "SSE events, traces and errors carry no source bodies to ordinary users; admins/local operators see the"
        " raw trace as decided",
    ),
    "SEC-05": oracle(
        n(
            DP,
            "test_graph_export_keeps_facts_and_moves_snippets_to_private_evidence",
            "test_graph_node_private_properties_and_artifact_urls_removed",
            "test_graph_payload_and_neighborhood_strip_edge_evidence",
            "test_guessed_source_urls_are_denied",
            "test_settings_and_health_paths_become_opaque",
        )
        + whole(DR)
        + n(WO, "test_opaque_ids_resolve_only_inside_allowed_roots"),
        "partial",
        "Graph payloads/exports strip private evidence and artifact URLs; reconstruction is capped per actor",
        "Report/export artifacts produced by later tasks (T19D/T26B)",
    ),
    # ---- schemas ---------------------------------------------------------------------------------
    "SCH-01": oracle(
        n(
            XS,
            "test_extract_schema_imports_overlay",
            "test_imports_matkg_not_a_fork",
            "test_xray_extract_schema_loads_with_linkml",
        )
        + n(
            PR,
            "test_schema_defaults_resolve_from_any_working_directory",
            "test_schema_helper_loads_default_schema_outside_repo",
        )
        + n(
            SCR,
            "test_every_inventoried_yaml_asset_is_registered_in_place_with_unchanged_bytes",
            "test_rsoxs_and_extract_overlay_are_distinct_registry_keys",
            "test_inheritance_is_preserved_for_every_registered_overlay",
        )
        + n(SCF, "test_closure_covers_the_resolved_imports"),
        "full",
        "Current overlay/import structure loads and resolves; every inventoried schema asset is registered in"
        " place with unchanged bytes, the overlays stay distinct keys and the fingerprint closure covers the"
        " resolved imports (T22B)",
    ),
    "SCH-02": oracle(
        n(
            PV,
            "TestBl1101SchemaProvVocabulary::test_bl1101_schema_loads_without_prov_vocabulary",
            "TestRsoxsSchemaProvVocabulary::test_prov_vocabulary_key_exists",
        )
        + n(XS, "test_no_prov_vocabulary_top_level"),
        "full",
        "The PROV vocabulary is a reference block, not loaded as LinkML schema content",
    ),
    "SCH-03": oracle(
        n(XV, "test_h3_gates_are_closed_and_enforced")
        + n(
            SCF,
            "test_binding_verified_legacy_stale_and_unknown",
            "test_any_import_byte_change_changes_dependent_fingerprints",
        )
        + n(
            SNC,
            "test_schema_selection_and_fingerprint_binding",
            "test_manifest_binds_graph_schema_extraction_corpus_and_validation",
        )
        + n(
            RLR,
            "test_missing_schema_fingerprint_is_refused",
            "test_mismatched_schema_fingerprint_is_refused",
            "test_schema_file_changed_in_the_repository_is_refused",
        )
        + n(JBU, "test_approval_arriving_after_schema_or_vocabulary_change_is_stale"),
        "full",
        "The schema closure fingerprint is bound into every snapshot manifest (result), a missing, mismatched or"
        " changed fingerprint is refused before commit, and a job approval goes stale when the schema changes"
        " (T22B, T23B, T26B, T10)",
    ),
    "SCH-04": oracle(
        n(CT, "test_apply_plan_rejects_stale_approval")
        + n(CO, "test_approval_must_name_the_exact_revision")
        + n(
            JBU,
            "test_approval_arriving_after_schema_or_vocabulary_change_is_stale",
            "test_approval_for_a_different_plan_is_stale",
        )
        + n(JBC, "test_case_06_approval_after_candidate_schema_or_vocabulary_changed_is_stale")
        + n(GIT, "test_default_publish_set_refuses_the_pending_schema")
        + n(RLR, "test_pending_review_schema_set_binds_only_with_explicit_selection"),
        "full",
        "A plan approved against one schema revision is stale after the schema or vocabulary changes, the"
        " importer refuses a snapshot bound to a schema outside the supported set, and a pending-review schema"
        " binds only by explicit selection (T22B, T10, T08B, T26B)",
    ),
    "SCH-05": oracle(
        n(CO, "test_different_classes_never_merge_even_when_one_inherits_the_other")
        + n(XS, "test_does_not_redefine_rsoxs_measurement")
        + n(
            SCR,
            "test_rsoxs_measurement_is_still_an_experimental_technique",
            "test_inheritance_is_preserved_for_every_registered_overlay",
        )
        + n(SCF, "test_pre_s4_fingerprint_is_reconstructed_and_still_binds"),
        "full",
        "RSoXS measurement inheritance is not redefined, inherited classes are not merged, inheritance is"
        " preserved for every registered overlay and the pre-S4 fingerprint still binds (T22B, T22C)",
    ),
    # ---- JSON graph publication ----------------------------------------------------------------------
    "JSON-01": oracle(
        n(J2, "test_main_writes_graph_for_valid_input")
        + n(JK, "test_repeated_builds_from_fresh_input_are_identical")
        + n(
            SNC,
            "test_build_runs_with_tiled_absent_network_blocked_and_no_shell",
            "test_a_fresh_interpreter_builds_without_importing_tiled_or_the_tiled_backends",
            "test_snapshot_modules_contain_no_tiled_or_shell_use",
            "test_cli_creates_a_snapshot_and_reports_json",
            "test_graph_bytes_equal_the_frozen_json2kg_fixture",
        )
        + n(SLL, "test_local_json_build_approval_publication_and_graph_read"),
        "full",
        "json2kg output is unchanged and the versioned snapshot service builds it with Tiled absent, the network"
        " blocked and no shell, from the CLI and from the local no-login API path (T23B, T25C)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-02": oracle(
        whole(JK, J2, J2E),
        "full",
        "json2kg field identity, serialization and CLI behaviour match the frozen baseline",
        fixtures=["tests/contracts/json_kg/"],
    ),
    "JSON-03": oracle(
        n(
            JK,
            "test_example_manifest_binds_exact_fixture_bytes_and_rejects_bad_paths",
            "test_fixture_files_match_manifest_hashes",
        )
        + n(GJ, "test_manifest_hash_is_checked_and_cache_reloads_explicitly")
        + n(
            SNC,
            "test_existing_snapshot_is_never_overwritten",
            "test_tampering_after_freezing_is_detected_in_the_working_tree",
            "test_a_working_file_is_never_an_approved_artifact",
            "test_manifest_validates_against_the_normative_json_schema",
            "test_legacy_flat_files_are_never_overwritten_or_reused",
        )
        + n(
            SNR,
            "test_a_pinned_release_verifies_and_returns_the_exact_bytes",
            "test_wrong_hashes_are_rejected",
            "test_manifest_and_compat_disagreements_are_rejected",
            "test_working_tree_edits_do_not_change_what_a_commit_selects",
        ),
        "full",
        "A frozen snapshot is never overwritten or reused, tampering after freezing is detected, the manifest"
        " validates against the normative schema and its hashes must agree with the committed bytes (T23B)",
    ),
    "JSON-04": oracle(
        n(GJ, "test_round_trip_preserves_every_node_edge_and_bytes")
        + n(GIG, "test_importer_never_parses_calls_a_model_or_downloads")
        + n(GIF, "test_end_to_end_import_receipt_and_second_import_is_a_noop"),
        "full",
        "The importer makes zero parser, model or download calls (json2kg and requests are trapped, the module"
        " source is scanned) and a second import of the same snapshot is a no-op (T08B)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-05": oracle(
        n(GI, "test_edge_ids_follow_triple_identity", "test_ledger_never_rebinds_and_round_trips")
        + n(
            GIF,
            "test_end_to_end_import_receipt_and_second_import_is_a_noop",
            "test_replay_after_injected_crash_has_no_duplicates",
            "test_supersession_replays_from_every_intermediate_state",
        ),
        "full",
        "Replaying the same snapshot to the same target, including after a crash at every stage, creates no"
        " duplicate facts or links (T08B)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-06": oracle(
        n(
            SW,
            "test_local_approval_never_authorizes_an_als_target",
            "test_staging_approval_does_not_authorize_prod",
        )
        + n(
            GIG,
            "test_local_approval_cannot_authorize_an_als_target",
            "test_receipt_is_a_separate_hashed_immutable_artifact",
        )
        + n(JBU, "test_pointer_only_moves_to_a_verified_receipt_for_its_own_target")
        + n(SLA, "test_local_approval_cannot_authorize_the_als_target"),
        "full",
        "An import receipt is a separate hashed artifact bound to its target; an approval or receipt for one"
        " target never authorizes or activates another, also against a real 0.2.18 ALS-shaped server (T08B,"
        " T10, T25C)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-07": oracle(
        n(GJ, "test_manifest_hash_is_checked_and_cache_reloads_explicitly")
        + n(
            GIG,
            "test_no_approval_wrong_plan_expired_and_modified_plans_are_refused_without_writes",
            "test_stale_base_revision_is_refused_without_writes",
        )
        + n(GIT, "test_default_publish_set_refuses_the_pending_schema")
        + n(SNC, "test_tampering_after_freezing_is_detected_in_the_working_tree")
        + n(SNR, "test_wrong_hashes_are_rejected", "test_manifest_and_compat_disagreements_are_rejected")
        + n(
            JBU,
            "test_snapshot_changed_after_planning_is_quarantined_not_imported",
            "test_candidate_changed_after_approval_invalidates_it_before_import",
        ),
        "full",
        "Tampered bytes, wrong hashes, a schema outside the supported set and a missing, wrong, expired or"
        " modified approval are all refused without writes (T23B, T08B, T10)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-08": oracle(
        n(
            GJ,
            "test_round_trip_preserves_every_node_edge_and_bytes",
            "test_records_are_graph_scoped_and_keep_old_fields",
        )
        + n(GI, "test_uri_round_trip")
        + n(CLT, "test_json_to_tiled_import_retains_fields_ids_evidence_and_reconciles_edits")
        + n(GIG, "test_forge_sentinels_and_links_survive_and_t08a_reader_sees_the_import")
        + n(GIT, "test_new_node_types_round_trip_through_the_tiled_import"),
        "full",
        "The Tiled read port over an imported graph equals the JSON read port: ids, fields, edge evidence and"
        " code links survive, FORGE sentinels are untouched, new T22C node types round-trip (T08B, T18C)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-09": oracle(
        n(
            GIG,
            "test_unavailable_tiled_leaves_json_intact_and_import_retryable",
            "test_server_outage_mid_import_is_retryable_and_resumes",
        )
        + n(GIF, "test_replay_after_injected_crash_has_no_duplicates")
        + n(
            JBU,
            "test_readback_verification_failure_quarantines_and_issues_no_receipt_or_pointer",
            "test_index_failure_leaves_previous_pair_active",
            "test_failure_of_the_step_never_invalidates_the_snapshot",
        )
        + n(
            JBC,
            "test_case_04_crash_after_graph_entities_before_links_and_receipt",
            "test_case_05_crash_after_publication_before_active_index_switch",
        ),
        "full",
        "An interrupted import (outage, crash between entities and links, failed readback or index switch)"
        " leaves the JSON intact, issues no receipt or pointer, keeps the previous graph active and resumes"
        " without duplicates (T08B, T10)",
        fixtures=TILED_FIXTURE,
    ),
    "JSON-10": oracle(
        n(
            GIG,
            "test_acknowledged_edit_is_never_lost_by_an_import",
            "test_edit_journal_is_durable_append_only_and_ordered",
        )
        + n(GIF, "test_changed_snapshot_uses_create_new_revision_and_retires_old")
        + n(CLT, "test_json_to_tiled_import_retains_fields_ids_evidence_and_reconciles_edits"),
        "full",
        "Acknowledged edits live in a durable append-only journal, a plan that would lose one is refused, a"
        " changed snapshot becomes a new revision that retires the old, and edits are reconciled across a"
        " re-import on a disposable 0.2.18 server (T08B, T18C; D30 admin-only writeback is the decided policy)",
        fixtures=TILED_FIXTURE,
    ),
    # ---- quotes -----------------------------------------------------------------------------------
    "QUOTE-01": oracle(
        n(
            DQ,
            "test_relevant_exact_quote_survives_byte_identical_with_citation",
            "test_long_relevant_quote_is_shown_in_full_by_default",
            "test_many_relevant_quotes_from_different_pages_are_all_kept_by_default",
            "test_full_page_echo_is_clipped",
            "test_structured_quote_carries_locator_and_citation",
            "test_same_projection_for_every_role",
        )
        + n(
            WQ,
            "test_als_user_gets_verified_quotes",
            "test_verbatim_quote_of_a_retrieved_chunk_keeps_its_quotes",
            "test_answer_echoing_most_of_a_region_is_clipped",
        ),
        "full",
        "Relevant exact quotations with their citation stay visible with no count limits; a page dump is clipped",
    ),
    "QUOTE-02": oracle(
        n(
            DQ,
            "test_paraphrase_is_never_verbatim_even_when_text_matches",
            "test_unverifiable_quote_is_downgraded_to_cited_statement",
            "test_structured_exact_claim_failing_check_is_not_verbatim",
            "test_normalized_match_is_verbatim_only_under_recorded_profile",
        )
        + n(WQ, "test_paraphrased_quote_is_downgraded_to_a_cited_statement")
        + n(EV, "test_normalization_profile_is_not_the_disclosure_verbatim_profile"),
        "full",
        "A paraphrase or changed-source text is never labelled an exact quote",
    ),
    # ---- deployment (images, gateway, CI) ---------------------------------------------------------------
    "DEP-01": oracle(
        n(
            IMD,
            "test_stage_layout",
            "test_t24c_stages_are_kept",
            "test_finals_bake_their_own_fixed_profile",
            "test_runtime_has_no_runtime_pip_or_seed_steps",
        )
        + n(IMC, "test_final_images_keep_code_schemas_and_tracked_kg_json")
        + n(IMF, "test_services_and_order", "test_api_worker_gateway_wiring")
        + n(CIR, "test_manifest_records_commit_digests_locks_tiled_and_ui_tree")
        + n(CIW, "test_build_builds_all_three_variants_from_one_commit", "test_every_variant_gets_the_post_build_checks"),
        "partial",
        "The three finals derive from one runtime base that carries the app, UI bundle, worker and gateway; each"
        " bakes only its own profile; the release manifest records one commit, the dependency locks, the Tiled"
        " pin and the UI tree for all three (T24B, T24D)",
        "Inspection of images actually built by build-app.yml (no CI build has run yet; local builds count as"
        " untested until then, audit PC-4; T24E/T28C)",
    ),
    "DEP-02": oracle(
        n(TC, "test_local_profile_is_managed_on_loopback_8765", "test_pin_is_exactly_0218")
        + n(TL, "test_launch_health_and_capabilities")
        + n(IMD, "test_only_local_bundles_the_managed_tiled_server", "test_tiled_server_pin_is_the_owner_pin_and_hash_checked")
        + n(IMT, "test_managed_tiled_starts_supervised_and_stops_on_sigterm_keeping_data")
        + n(IMP, "test_local_without_the_tiled_interpreter_is_refused")
        + n(IMF, "test_mounts"),
        "partial",
        "Only :local bundles the hash-checked 0.2.18 server venv; the supervisor starts it on loopback 8765 and"
        " stops it keeping its mounted data; preflight refuses a :local without the Tiled interpreter (T24B)",
        "The built :local image running its supervised Tiled in a container (T24E deployment test)",
        fixtures=TILED_FIXTURE,
    ),
    "DEP-03": oracle(
        n(
            TC,
            "test_als_profiles_are_external_with_fixed_url",
            "test_als_profile_rejects_other_url_and_managed_mode",
        )
        + n(TD, "test_external_mode_does_not_import_lifecycle")
        + n(TL, "test_external_outage_never_starts_a_server")
        + n(TK, "test_cli_rejects_lifecycle_on_external_profile")
        + n(GC, "test_als_hosts_are_never_rewritten_to_localhost"),
        "partial",
        "ALS profiles are external-only with no managed or fallback server in code",
        "Inspection of the built :als-dev/:als-prod images (T24B)",
    ),
    "DEP-04": oracle(
        n(
            TC,
            "test_als_profiles_are_external_with_fixed_url",
            "test_als_profile_rejects_other_url_and_managed_mode",
        )
        + n(SR, "test_als_target_is_fixed_per_profile", "test_unknown_or_empty_profile_refuses_startup")
        + n(
            WS,
            "test_als_refuses_an_identity_port_for_another_tiled",
            "test_profile_defaults_to_local_when_unset",
            "test_local_tiled_uri_stays_editable",
        )
        + n(WA, "test_tiled_uri_is_not_writable_on_als")
        + n(SG, "test_tiled_uri_editable_on_local_only"),
        "partial",
        "ALS targets are fixed per profile and never runtime-writable; local defaults and edits behave as decided",
        "Image environment defaults in the built variants (T24B/T24E)",
    ),
    "DEP-05": oracle(
        n(
            GWP,
            "test_ui_assets_docs_and_deep_links",
            "test_not_found_never_becomes_html",
            "test_index_is_not_cached_and_assets_are_immutable",
            "test_static_ui_and_api_share_one_origin",
        )
        + n(GWM, "test_strict_build_from_the_docs_stage_layout")
        + n(IMD, "test_build_checks_the_ui_prefix"),
        "partial",
        "UI assets, deep links and the MkDocs site resolve beneath /fair2wise from one origin, a missing asset"
        " is 404 not HTML, index is uncached and assets immutable, the image build checks the UI prefix (T24C,"
        " T24B)",
        "The built bundle through the gateway (tests/gateway::test_built_ui_and_docs_through_the_gateway needs a"
        " GATEWAY_IMAGE_ROOT built by the image, so it is not a fake-run oracle) and the real nginx in a built"
        " image (T24E deployment test)",
    ),
    "DEP-06": oracle(
        n(
            CU,
            "test_ui_route_set_is_the_frozen_19",
            "test_model_route_set_is_the_frozen_6",
            "test_ui_openapi_matches_baseline",
        )
        + n(
            GWP,
            "test_the_gateway_is_the_only_prefix_stripping_boundary",
            "test_api_prefix_is_stripped_exactly_once_and_the_suffix_is_raw",
            "test_every_recorded_legacy_flow_is_unchanged_under_the_prefix",
            "test_unprefixed_direct_api_still_works",
            "test_encoded_node_ids_reach_the_route_unchanged",
        ),
        "partial",
        "Exactly one prefix-stripping boundary; every recorded legacy flow returns the same schema and status"
        " under the prefix as unprefixed; encoded ids pass through unchanged (T24C)",
        "The real nginx in a built image (T24E deployment test)",
    ),
    "DEP-07": oracle(
        n(CU, "test_model_openapi_matches_baseline")
        + n(WA, "test_api_docs_stay_public_on_als")
        + n(
            GWP,
            "test_fastapi_docs_and_openapi_carry_the_prefix",
            "test_api_redirects_point_back_through_the_prefix",
            "test_run_api_passes_root_path_only_when_prefixed",
            "test_the_api_itself_never_strips_the_prefix",
        ),
        "partial",
        "/docs, /redoc and /openapi.json carry the prefix, redirects point back through it, root_path is passed"
        " only when prefixed and the API itself never strips it (T24C)",
        "The real nginx in a built image (T24E deployment test)",
    ),
    "DEP-08": blocked(
        [T24E],
        n(CU, "test_sse_framing_and_event_order", "test_sse_survives_client_fragmentation")
        + n(GWP, "test_api_locations_stream_without_buffering_and_keep_long_streams_open")
        + n(V1L, "test_job_stream_is_legacy_sse_with_ids_and_a_single_end")
        + n(DST, "test_edge_template_overwrites_forwarded_proto_and_forwards_the_prefix_unchanged",
            "test_edge_template_renders_for_the_example_inventory"),
        "SSE framing is frozen, the gateway template streams without buffering (T24C) and the Ansible edge"
        " template forwards /fair2wise unchanged, unbuffered, with X-Forwarded-Proto overwritten (T24E static);"
        f" SSE through the running container and the edge proxy still needs the deployed stack: {NOTE_D311}",
    ),
    "DEP-09": oracle(
        n(IMC, "test_final_images_hold_no_private_content", "test_dockerignore_backstop_excludes_private_paths")
        + n(IMD, "test_no_secrets_or_broad_copies")
        + n(CIR, "test_image_checks_pass_for_a_correct_image", "test_image_checks_fail_on_a_bad_image"),
        "partial",
        "The COPY allowlist and the .dockerignore backstop keep planted private files out of every final; no"
        " secret or broad copy in the Dockerfile; the post-build image check refuses a bad image (T24B, T24D)",
        "Layer-by-layer inspection of a real build and its cache (image_checks runs in build-app.yml after the"
        " first CI build, T24E) and the untracked storage/terminology harvest files (audit A5-1, T28C)",
    ),
    "DEP-10": blocked(
        [T24E],
        n(DST, "test_state_is_the_only_fair2wise_mount_on_als_and_local_adds_tiled")
        + n(DSA, "test_backup_then_restore_is_byte_identical_and_the_manifest_describes_every_file"),
        "The role mounts only the state volume (and the read-only writer secret) on ALS and adds the Tiled"
        " data directory on :local, and the state tree restores byte for byte (T24E static/harness);"
        f" persistence of the mounts across restart/recreate of a running container: {NOTE_D311}",
    ),
    "DEP-11": oracle(
        n(
            SW,
            "test_als_approval_refused_for_non_admin_downgrade_worker_or_wrong_target",
            "test_worker_context_cannot_move_between_deployments",
        )
        + n(
            GIG,
            "test_missing_capability_and_scope_are_denied",
            "test_forge_sentinels_and_links_survive_and_t08a_reader_sees_the_import",
            "test_preflight_refusals",
        )
        + n(
            V1L,
            "test_publication_target_outside_the_deployment_is_denied",
            "test_h5_gate_refuses_every_target_without_a_rehearsal_allowlist",
        )
        + n(V1C, "test_publication_gate_matches_base_and_namespace"),
        "full",
        "The importer takes its target from the deployment profile, denies a missing capability or scope, refuses"
        " a target outside the deployment or the rehearsal allowlist, and never touches FORGE sentinels (T08B,"
        " T14)",
        fixtures=TILED_FIXTURE,
    ),
    "DEP-12": oracle(
        n(
            SW,
            "test_als_admin_approval_has_fresh_role_check_and_separate_writer",
            "test_local_approval_is_local_operator_with_writer_and_no_identity",
            "test_worker_context_records_actor_and_run",
        )
        + n(
            CT,
            "test_local_approval_requester_is_local_operator",
            "test_als_approval_requires_verified_admin_evidence",
        )
        + n(WA, "test_telemetry_actor_comes_from_the_context"),
        "partial",
        "Requester, approver and executing writer are distinct in approvals and telemetry; local audit says"
        " local-operator",
        "Audit of an importer run and token-leak scan of its logs (T08B)",
    ),
    "DEP-13": oracle(
        n(
            SNC,
            "test_build_runs_with_tiled_absent_network_blocked_and_no_shell",
            "test_a_fresh_interpreter_builds_without_importing_tiled_or_the_tiled_backends",
        )
        + n(GIG, "test_importer_never_parses_calls_a_model_or_downloads")
        + n(V1C, "test_default_operations_register_no_kind_and_no_publication")
        + n(IMD, "test_runtime_has_no_runtime_pip_or_seed_steps")
        + n(IMF, "test_services_and_order", "test_check_mode_prints_the_plan_without_starting"),
        "partial",
        "JSON creation runs with Tiled absent, the importer makes no parser, model or download call, the default"
        " operations register no publication, and the images seed nothing at build or start (T23B, T08B, T14,"
        " T24B)",
        "Start of a built container never publishing a corpus (T24E deployment test)",
        fixtures=TILED_FIXTURE,
    ),
    "DEP-14": oracle(
        n(
            SW,
            "test_local_approval_never_authorizes_an_als_target",
            "test_staging_approval_does_not_authorize_prod",
        )
        + n(
            GIG,
            "test_local_approval_cannot_authorize_an_als_target",
            "test_receipt_is_a_separate_hashed_immutable_artifact",
        )
        + n(JBU, "test_pointer_only_moves_to_a_verified_receipt_for_its_own_target")
        + n(SLA, "test_local_approval_cannot_authorize_the_als_target")
        + n(SLI, "test_an_approval_verified_by_one_server_does_not_authorize_another"),
        "full",
        "The same JSON hash needs a target-specific plan, approval and receipt: local, staging and production"
        " receipts never cross, also between two real 0.2.18 servers (T08B, T10, T25C)",
        fixtures=TILED_FIXTURE,
    ),
    "DEP-15": oracle(
        n(
            CIW,
            "test_build_runs_for_prs_with_read_only_permissions",
            "test_build_never_logs_in_pushes_or_publishes",
            "test_no_secret_is_referenced",
            "test_publish_runs_on_main_and_release_tags_and_promotes_by_dispatch",
            "test_gate_fails_closed_on_o06_and_protected_ref",
            "test_every_writer_job_is_gated_and_in_an_environment",
            "test_the_gate_cannot_be_bypassed",
            "test_only_the_job_token_logs_in_to_ghcr",
            "test_promotion_is_a_retag_of_a_published_digest",
        )
        + n(
            CIR,
            "test_any_published_als_prod_digest_can_be_promoted_regardless_of_als_dev",
            "test_promotion_refusals",
            "test_publish_pushes_immutable_and_release_tags_never_channels",
            "test_stale_build_cannot_move_a_channel_backwards_but_rollback_can",
        ),
        "partial",
        "Pull requests build read-only and never publish; publication is manual dispatch inside named"
        " environments behind an O06 gate that fails closed; promotion re-tags a published digest and :als-prod"
        " needs the same commit staged on :als-dev (T24D)",
        "The O06 tag rules are a proposal the owner has not approved, and GitHub environment protection is"
        " server-side configuration (H6 / O06)",
    ),
    "DEP-16": blocked(
        [T24E],
        n(
            DST,
            "test_container_image_is_repository_at_digest_and_validated_by_regex",
            "test_no_tag_reference_anywhere",
            "test_no_secret_hostname_or_real_digest_is_committed",
            "test_example_inventory_uses_placeholders_and_vault_references_only",
            "test_edge_template_overwrites_forwarded_proto_and_forwards_the_prefix_unchanged",
            "test_als_environment_provides_cors_origins_and_the_secret_file_path",
            "test_writer_secret_file_is_0600_owned_by_the_container_uid_and_never_logged",
            "test_every_variable_the_plays_need_is_declared",
        ),
        "The Ansible handoff (deploy/) deploys repository@digest only, carries no FQDN, hostname, digest or"
        " secret, forwards /fair2wise unchanged behind TLS with X-Forwarded-Proto overwritten, provides"
        " F2W_CORS_ORIGINS and a 0600 uid-10001 writer secret file, and overrides no locked image value (T24E"
        " static); the plays have never run against a host (O07 inputs, ansible-lint/syntax-check not"
        f" installed) and the container behind the edge needs D3.11: {NOTE_D311}",
    ),
    "DEP-17": blocked(
        [T24E],
        n(
            DSA,
            "test_backup_then_restore_is_byte_identical_and_the_manifest_describes_every_file",
            "test_a_database_with_an_open_writer_is_copied_consistently",
            "test_a_tampered_member_is_refused_and_nothing_is_written",
            "test_sidecar_mismatch_and_missing_sidecar_are_refused",
            "test_a_rewritten_manifest_is_refused_by_hash_and_a_foreign_profile_by_policy",
            "test_a_member_escaping_the_destination_is_refused",
            "test_restore_refuses_a_non_empty_destination_unless_replace",
        )
        + n(DST, "test_backup_archives_with_manifest_and_verifies_before_success",
            "test_restore_verifies_profile_and_owner_and_never_replaces_by_default"),
        "The host tool archives the state mount with a manifest and sha256 sidecar (consistent SQLite copies,"
        " real JobStore database), restores byte for byte and refuses a tampered archive, a foreign profile"
        " or an escaping path before writing (T24E harness); restore onto a new disposable host with a"
        f" container reading the restored volume, and central-Tiled backup ownership (O07): {NOTE_D311}",
    ),
    "DEP-18": blocked(
        [T24E],
        n(TL, "test_recovery_after_killed_server")
        + n(JBW, "test_graceful_shutdown_requeues_running_job_and_keeps_finished_stages")
        + n(IMS, "test_starts_in_order_after_health_and_stops_in_reverse_on_sigterm")
        + n(DST, "test_stop_timeout_is_120_on_every_stop_or_recreate",
            "test_health_checks_use_the_prefixed_health_urls_and_the_supervisor_status"),
        "Job drain on shutdown and the supervisor's ordered health/stop are covered in-process (T10, T24B);"
        " the role stops with --stop-timeout 120 and checks /fair2wise/healthz, /fair2wise/api/health and the"
        f" supervisor status for every required service (T24E static); the running images: {NOTE_D311}",
    ),
    "DEP-19": oracle(
        n(
            SF,
            "test_local_every_exposed_inventoried_route_works_without_sign_in",
            "test_als_user_is_chat_only_and_denied_before_effects",
        )
        + n(SLL, "test_local_app_serves_every_route_without_sign_in", "test_local_graph_edit_and_tiled_uri_need_no_identity")
        + n(
            SLA,
            "test_reader_privileged_work_is_denied_before_effects",
            "test_admin_privileged_work_takes_effect",
            "test_reader_cannot_change_deployment_wide_settings",
        )
        + n(V1T, "test_reader_is_chat_only_and_admin_publishes_with_a_real_role_check")
        + n(
            CLA,
            "test_ui26_pending_survives_restart_and_applies_exactly_once",
            "test_ui10_retry_after_a_streamed_action_does_not_apply_twice",
        ),
        "partial",
        "On :local every route and graph edit works without sign-in (D34); on an ALS-shaped real 0.2.18 server a"
        " Tiled admin's edits take effect and a non-admin is denied before any effect (D30); a Save is never"
        " faked, lost or applied twice across restart or retry (T25C, T14, T18C)",
        "The frozen browser UI against the deployed ALS stack (T24E deployment test)",
        fixtures=TILED_FIXTURE,
    ),
    "DEP-20": blocked(
        [T24E],
        n(PS, "test_personal_settings_persist_across_restart")
        + n(DST, "test_als_environment_provides_cors_origins_and_the_secret_file_path"),
        "Personal settings persist across restart in-process and the role sets only F2W_CORS_ORIGINS and the"
        " secret-file path, never a model, profile or authorization override (T24E static); persistence of"
        f" configured model and limits across a real profile change of the running stack: {NOTE_D311}",
    ),
    "DEP-21": blocked(
        [T24E, T13],
        n(DSA, "test_release_record_keeps_previous_and_new_digest_through_rollout_and_rollback",
            "test_release_record_refusals", "test_release_record_cli_exit_codes_and_json")
        + n(DST, "test_rollout_records_the_digest_and_rollback_re_points_to_the_recorded_previous_one"),
        "Image rollback re-points to a digest recorded on the host, records previous and new digest, refuses a"
        " tag or an unrecorded digest and a state-schema label mismatch, and never touches graph data (T24E);"
        f" the host rehearsal and the separate JSON/Tiled data rollback (T13) are still needed: {NOTE_D311}",
    ),
    "DEP-22": oracle(
        n(GWP, "test_build_runtime_prefix_mismatch_is_refused_with_a_rebuild_hint")
        + n(IMP, "test_ui_built_for_another_prefix_is_refused")
        + n(IMD, "test_build_checks_the_ui_prefix"),
        "full",
        "A UI bundle built for another base path is refused at gateway render, at image preflight and at image"
        " build, each with a rebuild hint instead of a runtime ENV workaround (T24C, T24B)",
    ),
    # ---- ALS authorization ------------------------------------------------------------------------------------
    "AUTH-01": oracle(
        n(
            SF,
            "test_als_anonymous_gets_only_reduced_health",
            "test_als_default_deny_and_not_exposed_routes",
            "test_builtin_docs_routes_are_declared_public",
        )
        + n(
            WA,
            "test_anonymous_is_refused_except_health",
            "test_api_docs_stay_public_on_als",
            "test_unknown_or_rejected_session_cookie_is_signed_out",
        ),
        "full",
        "Unauthenticated ALS requests reach only health and the public docs pages; nothing else runs",
        fixtures=["tests/api_wiring/_wiring_support.py", "tests/security/security_fakes.py"],
    ),
    "AUTH-02": oracle(
        n(SF, "test_als_user_is_chat_only_and_denied_before_effects")
        + n(
            WA,
            "test_user_chat_surface_is_allowed",
            "test_sessions_are_own_or_any",
            "test_traces_and_annotations_are_own_or_any",
            "test_user_settings_are_reduced_and_admin_settings_are_full",
        )
        + n(WO, "test_als_user_reads_an_uploaded_graph_by_id")
        + whole(PS)
        + n(RPA, "test_als_chat_user_may_ask"),
        "full",
        "A non-admin ALS user can chat, keep own sessions, annotate own turns, read the JSON graph and keep"
        " personal settings, without graph writes",
    ),
    "AUTH-03": oracle(
        n(
            WA,
            "test_user_privileged_work_is_denied_before_effects",
            "test_user_gets_no_ingestion_offer",
            "test_user_settings_are_reduced_and_admin_settings_are_full",
        )
        + n(
            SG,
            "test_require_denies_before_effect",
            "test_requires_decorator_guards_services_tools_and_async_workers",
            "test_user_chat_action_yes_denied_before_job_creation",
        )
        + n(
            PS,
            "test_non_admin_frozen_ui_save_succeeds_and_ignores_deployment_fields",
            "test_every_settings_field_is_classified",
        ),
        "full",
        "Non-admin ingestion, batch and deployment-wide setting changes are denied before any effect",
    ),
    "AUTH-04": oracle(
        n(SF, "test_als_user_is_chat_only_and_denied_before_effects")
        + n(WA, "test_user_privileged_work_is_denied_before_effects")
        + n(RTE, "test_chat_only_context_denied_write_before_effect", "test_write_without_approval_or_plan_denied")
        + n(RTS, "test_chat_only_context_sees_no_write_tools_and_hallucinated_write_is_denied")
        + n(RPA, "test_delegated_tool_is_hidden_and_denied_without_chat_ask")
        + n(SW, "test_als_approval_refused_for_non_admin_downgrade_worker_or_wrong_target"),
        "partial",
        "Legacy routes, SDK tools, runtime tools and worker approvals deny non-admin edit and approval",
        "The Tiled graph edit write path itself (T08B)",
    ),
    "AUTH-05": oracle(
        n(
            WA,
            "test_admin_privileged_work_takes_effect",
            "test_admin_gets_the_offer_and_can_approve",
            "test_admin_is_allowed_on_every_route",
        )
        + n(SF, "test_als_admin_can_do_privileged_work_with_fresh_role_checks")
        + n(SW, "test_als_admin_approval_has_fresh_role_check_and_separate_writer"),
        "partial",
        "Admin approval and scoped privileged work succeed with verified subjects, a separate writer and audit",
        "Scoped Tiled import with ID readback (T08B)",
    ),
    "AUTH-06": oracle(
        n(SF, "test_forged_trust_claims_never_elevate")
        + n(
            SR,
            "test_als_writer_or_service_credential_never_establishes_a_user",
            "test_resolver_has_no_trust_claim_inputs",
        )
        + n(ST, "test_user_principal_is_not_admin", "test_missing_or_foreign_credential_never_sent"),
        "full",
        "Caller-declared admin, spoofed headers, the writer account and Tiled service principals never elevate a"
        " user",
    ),
    "AUTH-07": oracle(
        n(
            SF,
            "test_als_revoked_or_expired_session_is_sign_in_required",
            "test_identity_outage_fails_closed_without_local_trust",
        )
        + n(WA, "test_privileged_capability_rechecks_the_role", "test_identity_outage_fails_closed")
        + n(
            SW,
            "test_als_worker_stops_on_downgrade_expiry_revocation",
            "test_als_worker_outage_is_retryable_not_trusted",
        )
        + n(
            SR,
            "test_role_check_max_age_is_bounded",
            "test_outage_with_fresh_cache_keeps_chat_but_refuses_privileged",
            "test_role_cache_is_bounded_and_bypassed_at_protected_boundaries",
        ),
        "full",
        "Privileged authorization is revalidated within the bounded cache age; expiry, downgrade, revocation and"
        " outages fail closed",
    ),
    "AUTH-08": oracle(
        n(SF, "test_staging_session_is_not_accepted_with_another_issuer")
        + n(SR, "test_identity_port_for_another_server_is_refused", "test_als_target_is_fixed_per_profile")
        + n(
            SW,
            "test_staging_approval_does_not_authorize_prod",
            "test_worker_context_cannot_move_between_deployments",
            "test_als_approval_refused_for_non_admin_downgrade_worker_or_wrong_target",
        )
        + n(WS, "test_als_refuses_an_identity_port_for_another_tiled"),
        "partial",
        "Identity and approval grants are bound to their ALS environment",
        "Import receipts and tokens across staging/production on real servers (T08B/T25C)",
    ),
    "AUTH-09": oracle(
        n(GC, "test_als_hosts_are_never_rewritten_to_localhost")
        + n(ST, "test_admin_principal_is_parsed_from_0_2_18_shape")
        + n(
            GWP,
            "test_auth09_sign_in_round_trip_under_the_prefix",
            "test_auth09_cookie_is_not_sent_outside_the_prefix",
            "test_auth09_external_providers_stay_unsupported",
            "test_auth09_no_tiled_auth_or_admin_proxy_through_the_gateway",
        )
        + n(
            SLA,
            "test_anonymous_gets_only_health_docs_and_sign_in",
            "test_a_tiled_token_sent_by_the_browser_is_not_a_credential",
            "test_sign_in_round_trip_keeps_tokens_server_side",
            "test_sign_out_ends_the_session_here_and_at_tiled",
        )
        + n(
            SLU,
            "test_handlers_set_a_hardened_cookie_and_never_return_tokens",
            "test_wrong_password_unknown_and_external_providers",
        ),
        "partial",
        "Sign-in and return work beneath /fair2wise with a hardened prefix-scoped cookie; Tiled tokens stay"
        " server-side and are never a browser credential; no Tiled auth or admin route is proxied through the"
        " gateway; the round trip holds against a real multi-user 0.2.18 server (T24C, T25C)",
        "The central ALS Tiled's own sign-in provider behind the edge proxy (T24E deployment test)",
        fixtures=TILED_FIXTURE,
    ),
    "AUTH-10": oracle(
        n(SR, "test_forge_delegate_keeps_caller_capability")
        + n(RTS, "test_chat_only_context_sees_no_write_tools_and_hallucinated_write_is_denied")
        + n(PK, "test_summary_runs_the_t19b_operation_through_an_authorizing_executor"),
        "partial",
        "A delegated call keeps the caller's capability set; a non-admin caller inside an admin-capable host gets"
        " no write tools",
        "The real FORGE consumer adapter (T15)",
    ),
    "AUTH-11": oracle(
        n(SF, "test_chat_recording_never_uses_the_writer")
        + n(WA, "test_owners_survive_a_restart", "test_telemetry_actor_comes_from_the_context"),
        "full",
        "Ordinary chat/session recording never uses the service writer",
    ),
    # ---- Git promotion --------------------------------------------------------------------------------------
    "REL-01": oracle(
        n(RLH, "test_prepare_commit_verify_describe_export")
        + n(
            RLR,
            "test_bytes_edited_between_prepare_and_commit_fail_committed_equals_tested",
            "test_a_later_commit_that_rewrites_the_snapshot_fails_against_the_tested_record",
            "test_an_extra_or_missing_committed_file_in_the_snapshot_directory_is_refused",
            "test_report_for_a_different_snapshot_is_refused",
            "test_provisional_and_not_pass_verdicts_are_recorded_and_never_upgraded",
        ),
        "partial",
        "Committed blob equals the tested bytes by commit+path; any mutation or a report bound to other bytes is"
        " refused; a non-PASS verdict is never recorded as passed",
        "Staging import of the committed artifact and the staging PASS report (T26C, T08B)",
        supporting=n(JK, "test_fixture_files_match_manifest_hashes"),
    ),
    "REL-02": oracle(
        n(GIG, "test_importer_never_parses_calls_a_model_or_downloads")
        + n(JBU, "test_full_publication_verifies_receipts_and_activates")
        + n(SNC, "test_build_runs_with_tiled_absent_network_blocked_and_no_shell")
        + n(RLH, "test_lookup_and_export_never_select_by_branch_or_working_tree")
        + n(RLP, "test_a_full_release_run_issues_only_read_only_git_and_never_touches_the_remote"),
        "partial",
        "Export selects committed bytes by commit and path, the importer makes no parser, model or download"
        " call, publication verifies receipts and activates without rebuilding, and the release run is"
        " read-only Git (T26B, T08B, T10)",
        "The production promotion rehearsal on the real staging and production targets (T26C, T13)",
        fixtures=TILED_FIXTURE,
    ),
    "REL-03": oracle(
        n(SW, "test_staging_approval_does_not_authorize_prod")
        + n(
            GIG,
            "test_local_approval_cannot_authorize_an_als_target",
            "test_no_approval_wrong_plan_expired_and_modified_plans_are_refused_without_writes",
            "test_receipt_is_a_separate_hashed_immutable_artifact",
        )
        + n(
            JBU,
            "test_pointer_only_moves_to_a_verified_receipt_for_its_own_target",
            "test_import_requires_the_publish_capability_to_submit",
        )
        + n(SLI, "test_an_approval_verified_by_one_server_does_not_authorize_another"),
        "partial",
        "A production import needs its own target-scoped plan, an admin approval of exactly that plan and issues"
        " its own hashed receipt; a staging approval or receipt, or one verified by another server, never"
        " substitutes (T08B, T10, T25C)",
        "The staging-then-production run on the real targets (T26C)",
        fixtures=TILED_FIXTURE,
    ),
    "REL-04": oracle(
        n(
            RLR,
            "test_reference_to_gitignored_papers_is_refused",
            "test_tracked_beamline_ops_documents_are_not_private",
            "test_source_rag_index_parsed_bodies_and_crops_are_refused",
            "test_full_parsed_bodies_local_paths_and_credentials_in_the_graph_are_refused",
            "test_gitignored_or_untracked_manifest_references_are_refused",
            "test_local_path_or_secret_in_manifest_is_refused",
            "test_extra_files_in_the_snapshot_directory_are_refused",
        )
        + n(
            SNR,
            "test_secrets_local_paths_and_signed_urls_are_rejected_without_echoing_values",
            "test_path_allowlist_rejects_pdfs_crops_databases_env_and_private_stores",
            "test_full_parsed_bodies_and_document_chunks_are_blocked_but_snippets_are_not",
            "test_snapshot_creation_records_disclosure_status_and_blocks_commit_readiness",
        ),
        "partial",
        "Commit-ready check refuses gitignored papers, the source-RAG index, parsed bodies, crops, credentials and"
        " local paths before commit while tracked ops documents and snippets stay allowed; the snapshot records"
        " its disclosure status and a failing one is not commit-ready (T26B, T23B)",
        "A CI re-check of the committed snapshot blobs on push does not exist yet (the T24D workflows build"
        " images and check them, not Git snapshots); it belongs to the promotion rehearsal (T26C)",
        supporting=n(JK, "test_fixture_inputs_carry_no_local_paths"),
    ),
    "REL-05": oracle(
        n(
            IMC,
            "test_final_images_hold_no_private_content",
            "test_final_images_keep_code_schemas_and_tracked_kg_json",
            "test_dockerignore_backstop_excludes_private_paths",
        )
        + n(IMD, "test_no_secrets_or_broad_copies")
        + n(CIR, "test_image_checks_fail_on_a_bad_image"),
        "partial",
        "Every final keeps tracked KG JSON (owner O-D1) and excludes raw PDFs, parsed bodies, crops, the"
        " source-RAG index, credentials and databases through the COPY allowlist and the .dockerignore backstop;"
        " the post-build check refuses a bad image (T24B, T24D)",
        "Intermediate layers and build cache of a real build (first CI build pending, T24E) and the untracked"
        " storage/terminology harvest files the Dockerfile copies (audit A5-1 / PC-1, T28C)",
    ),
    "REL-06": oracle(
        n(
            RLE,
            "test_unresolvable_locators_are_recorded_as_unavailable_not_dropped",
            "test_evidence_with_other_bytes_or_an_invalid_ref_is_refused",
            "test_required_private_assets_block_promotion_when_missing",
        ),
        "partial",
        "Unavailable evidence is recorded explicitly; a missing or altered required private asset blocks"
        " promotion with no rebuild and nothing added to Git",
        "Runtime degraded-capability state on a target without the assets (T26C/T08B)",
    ),
    "REL-07": oracle(
        n(
            GIG,
            "test_acknowledged_edit_is_never_lost_by_an_import",
            "test_edit_journal_is_durable_append_only_and_ordered",
        )
        + n(GIF, "test_changed_snapshot_uses_create_new_revision_and_retires_old")
        + n(SNC, "test_repair_creates_a_new_version_with_a_verified_parent", "test_existing_snapshot_is_never_overwritten")
        + n(CLT, "test_json_to_tiled_import_retains_fields_ids_evidence_and_reconciles_edits")
        + n(RLR, "test_committed_snapshot_with_other_bytes_is_never_overwritten"),
        "partial",
        "Admin edits are journalled and reconciled into a new snapshot version with a verified parent; the"
        " previous version and the committed bytes are never overwritten; an import that would erase an"
        " acknowledged edit is refused (T08B, T23B, T26B, T18C)",
        "Committing the reconciled version and promoting it end to end on the rehearsal target (T26C)",
        fixtures=TILED_FIXTURE,
    ),
    "REL-08": blocked([T26C], note="Data rollback rehearsal"),
    "REL-09": blocked(
        [T15], note="FORGE merge status is recorded by the integration task and the owner with Alex"
    ),
    # ---- local trusted access ----------------------------------------------------------------------------------
    "LOCAL-01": oracle(
        n(SF, "test_local_every_exposed_inventoried_route_works_without_sign_in")
        + n(
            WS, "test_local_serves_every_route_with_zero_identity_calls", "test_local_health_is_full_and_path_free"
        )
        + n(SLL, "test_local_app_serves_every_route_without_sign_in")
        + n(GWP, "test_ui_assets_docs_and_deep_links"),
        "partial",
        "Every inventoried route works locally without sign-in or identity calls, including with non-admin,"
        " expired or absent Tiled credentials against a real 0.2.18 server; /fair2wise and its deep links"
        " resolve through the gateway without a redirect (T25C, T24C)",
        "The browser opening the built :local image (T24E deployment test)",
        fixtures=TILED_FIXTURE,
    ),
    "LOCAL-02": blocked(
        [H3GOLD],
        n(SF, "test_local_everything_allowed_without_sign_in_and_without_identity_calls")
        + n(WS, "test_local_tiled_uri_stays_editable")
        + n(
            SLL,
            "test_local_app_serves_every_route_without_sign_in",
            "test_local_json_build_approval_publication_and_graph_read",
            "test_local_graph_edit_and_tiled_uri_need_no_identity",
        ),
        "The API-level local workflows (versioned JSON creation, approval, local Tiled publication, graph editing"
        " and the Tiled URL setting) run without sign-in against disposable 0.2.18 servers (T25C, T08B, T10,"
        " landed); whether literature intake, extraction and figure processing preserve their scientific"
        " contracts on isolated fixtures rests on the pilot-paper labels (H3 B4)",
    ),
    "LOCAL-03": oracle(
        n(SW, "test_local_approval_is_local_operator_with_writer_and_no_identity")
        + n(
            CT,
            "test_local_approval_requester_is_local_operator",
            "test_local_context_is_local_operator_without_identity",
            "test_local_operator_cannot_carry_als_identity_fields",
        )
        + n(SR, "test_writer_identity_required_and_not_local_operator")
        + n(WS, "test_local_trace_is_raw_and_annotator_is_the_local_operator"),
        "full",
        "Local audit records the local-operator, fabricates no authentication, and keeps the separate configured"
        " writer",
    ),
    "LOCAL-04": oracle(
        n(SW, "test_local_worker_needs_no_refresh_and_no_identity_call")
        + n(RPA, "test_local_context_is_local_operator_and_resolve_context_agrees")
        + n(SG, "test_requires_decorator_guards_services_tools_and_async_workers")
        + n(SF, "test_local_everything_allowed_without_sign_in_and_without_identity_calls"),
        "partial",
        "Local API, SDK tools and workers share one access policy without login",
        "Direct CLI and scoped background resume of jobs (T10/T14)",
    ),
    "LOCAL-05": oracle(
        n(SR, "test_local_is_local_operator_with_all_capabilities_and_no_identity_call")
        + n(SF, "test_local_everything_allowed_without_sign_in_and_without_identity_calls")
        + n(WS, "test_local_serves_every_route_with_zero_identity_calls"),
        "partial",
        "Zero user-role gate calls locally while all application capabilities stay available",
        "Publication through service credentials to a local Tiled (T08B)",
    ),
    "LOCAL-06": oracle(
        n(
            GC,
            "test_closed_port_is_unavailable_not_empty",
            "test_error_mapping_never_returns_empty",
            "test_credentials_come_from_runtime_config_not_environment",
        )
        + n(ST, "test_outages_and_bad_bodies_are_unavailable", "test_transport_errors_are_unavailable")
        + n(GS, "test_wrong_credential_is_denied_and_stopped_server_is_unavailable")
        + n(SR, "test_identity_outage_never_enables_local_trust")
        + n(
            SLW,
            "test_missing_local_writer_is_a_graph_error_not_sign_in",
            "test_revoked_writer_key_fails_graph_operations_and_leaves_chat",
            "test_wrong_writer_key_is_writer_unauthorized_and_writes_nothing",
        )
        + n(GIG, "test_stopped_real_server_is_unavailable_not_empty")
        + n(SLU, "test_handlers_set_a_hardened_cookie_and_never_return_tokens"),
        "full",
        "Missing, wrong or revoked local Tiled writer credentials and a stopped server are truthful graph-operation"
        " errors (never a sign-in challenge, never empty results or a trust grant); chat and JSON capabilities"
        " stay available; no credential reaches the browser (T25C, T08B)",
        fixtures=TILED_FIXTURE,
    ),
    "LOCAL-07": oracle(
        n(SR, "test_als_with_local_trusted_refuses_startup", "test_identity_outage_never_enables_local_trust")
        + n(CT, "test_als_profiles_reject_local_trusted")
        + n(SF, "test_forged_trust_claims_never_elevate", "test_identity_outage_fails_closed_without_local_trust")
        + n(WS, "test_rejected_startup_values_refuse"),
        "full",
        "No client, header or payload claim of local trust authorizes an ALS call; ALS refuses local_trusted at"
        " startup",
    ),
    "LOCAL-08": oracle(
        n(SR, "test_local_bind_defaults_to_loopback_and_network_warns")
        + n(SF, "test_local_network_exposure_is_explicit_and_warned"),
        "full",
        "Local exposure defaults to loopback; wider exposure is explicit and warned",
    ),
    "LOCAL-09": oracle(
        n(DA, "test_full_access_profiles_hold_no_user_surface_source_access")
        + n(XV, "test_h3_gates_are_closed_and_enforced")
        + n(CT, "test_local_approval_rules_and_binding")
        + n(SW, "test_local_approval_never_authorizes_an_als_target")
        + n(CO, "test_importing_package_has_no_side_effects_and_gates_closed"),
        "partial",
        "Local full access keeps source privacy, approval binding and closed H-gates",
        "Immutable JSON version and target-specific import checks (T23B/T08B)",
    ),
    "LOCAL-10": oracle(
        n(
            SW,
            "test_local_approval_never_authorizes_an_als_target",
            "test_staging_approval_does_not_authorize_prod",
            "test_worker_context_cannot_move_between_deployments",
        )
        + n(CT, "test_local_approval_rules_and_binding"),
        "partial",
        "A local approval never authorizes an ALS target; worker contexts cannot move between deployments",
        "The receiving ALS import checking its own profile and admin (T08B)",
    ),
}

# --- owner-decision updates ------------------------------------------------------------------------
A1 = "H1 owner_decisions O-A1 (annotations, 2026-10-12)"
A5 = "H1 owner_decisions O-A5 (JSON graph reading, 2026-10-12)"
A6 = "H1 owner_decisions O-A6 (extraction_mode admin-gated, 2026-10-12)"
A7 = "H1 owner_decisions O-A7 (MkDocs under the prefix, 2026-10-12)"
A8 = "H1 owner_decisions O-A8 (FastAPI docs public, 2026-10-12)"
SVC = "H1 owner_decisions T25B follow-up: service principals refused (2026-10-12)"
GRAPHSEL = "H1 owner_decisions T25B follow-up: default graph selection is per user (2026-10-12)"
D1 = "H1 owner_decisions O-D1 (verbatim snippets in stored KG JSON, 2026-10-12)"
G1 = "H1 owner_decisions O-G1 (what is tracked in Git, 2026-10-12)"
T1 = "H1 owner_decisions O-T1 (Tiled 0.2.18 interim pin, 2026-10-12)"
D2 = "H1 owner_decisions O-D2 (relevance-only quotes, 40% region rule, 2026-10-12)"
TURI = "H1 owner_decisions O-D2/T21B follow-up: Tiled URL editable on :local only (2026-10-12)"
TRACE = "H1 owner_decisions O-D2/T21B follow-up: admin raw traces (2026-10-12)"
R1 = "H1 owner_decisions O-R1 (supervisor planner fallback, 2026-10-13)"
Q1 = "H2 decision Q1 (planning supervisor for UI QA turns)"
Q3 = "H2 decision Q3 (default :local profile)"
Q4 = "H2 decision Q4 (per-user settings on ALS)"
Q5 = "H2 decision Q5 (workflow_mode admin-only)"
Q6 = "H2 decision Q6 / O-A2 (role-check max age 60 s, cap 900 s)"

CHANGES: Dict[str, Dict[str, Any]] = {
    "AUTH-01": {
        "decisions": [A8],
        "add": (
            "Owner decision update: the FastAPI generated pages /docs, /docs/oauth2-redirect, /redoc and"
            " /openapi.json are public on ALS as well as local; every other route stays closed to unauthenticated"
            " callers."
        ),
    },
    "AUTH-02": {
        "decisions": [A1, A5, Q4],
        "add": (
            "Owner decision update: a non-admin may annotate and read only their own sessions, turns and traces"
            " (admins any turn); every authenticated ALS user can read the JSON graph (GET /graph/node with"
            " json_graph_path needs only chat.read_support); model/graph/RAG preferences are the user's own"
            " personal settings, independent per principal."
        ),
    },
    "AUTH-03": {
        "decisions": [Q5, A6, GRAPHSEL, Q4],
        "add": (
            "Owner decision update: extraction_mode and workflow_mode are admin-only deployment-wide settings, and"
            " the server default graph list (PUT /settings json_graph_paths) is admin-only on ALS until per-user"
            " selection exists; a non-admin Save from the frozen UI succeeds for personal fields and ignores"
            " deployment-wide fields with a warning."
        ),
    },
    "AUTH-06": {
        "decisions": [SVC],
        "add": (
            "Owner decision update: Tiled machine accounts (service principals) are refused as callers on ALS;"
            " FORGE acts on behalf of a signed-in user."
        ),
    },
    "AUTH-07": {
        "decisions": [Q6],
        "add": (
            "Owner decision update: the privileged role check may be cached for at most 60 s by default (hard cap"
            " 900 s) and is bypassed at protected boundaries."
        ),
    },
    "AUTH-10": {
        "decisions": [SVC],
        "add": (
            "Owner decision update: delegation is on behalf of a signed-in user; a service principal is not an"
            " accepted FORGE caller on ALS (revisit at T15)."
        ),
    },
    "DEP-02": {
        "decisions": [T1],
        "title": "Local image provisions supervised Tiled (interim pin 0.2.18)",
        "add": (
            "Owner decision update: the interim pin is tiled==0.2.18 (wheel sha256 416079d4...fa06, sdist sha256"
            " 2f394326...1e27) in place of 0.2.19 until a final 0.2.19 exists; the managed server runs in its own"
            " environment."
        ),
    },
    "DEP-04": {
        "decisions": [TURI, Q3],
        "add": (
            "Owner decision update: the Tiled URL is never writable at runtime on ALS (editable on :local only)"
            " and an unset F2W_DEPLOYMENT_PROFILE selects :local."
        ),
    },
    "DEP-05": {
        "decisions": [A7],
        "add": (
            "Owner decision update: the MkDocs site is built into every image (:local, :als-dev, :als-prod) and is"
            " served beneath /fair2wise."
        ),
    },
    "DEP-07": {
        "decisions": [A8],
        "add": (
            "Owner decision update: /docs, /docs/oauth2-redirect, /redoc and /openapi.json are public on ALS as"
            " well as local and must carry the correct prefix."
        ),
    },
    "LLM-01": {
        "decisions": [Q4],
        "add": (
            "Owner decision update: on ALS the provider/model selection is the signed-in user's own personal"
            " setting, independent per principal; on :local the operator's settings are the server's."
        ),
    },
    "LOCAL-02": {
        "decisions": [TURI],
        "add": (
            "Owner decision update: the local operator can edit the Tiled URL setting (editable on :local only)."
        ),
    },
    "QA-01": {
        "decisions": [Q1],
        "add": (
            "Owner decision update: with the planning supervisor route (F2W_RUNTIME=langgraph) the evidence IDs,"
            " graph IDs, citations and statuses must equal those of the direct call on the same fixtures."
        ),
    },
    "QA-04": {
        "decisions": [R1, Q1],
        "add": (
            "Owner decision update: if the supervisor planning model errors or does not call the QA tool, the turn"
            " falls back to the direct answer_from_knowledge call with the reason logged and the failed planning"
            " call in the trace (O-R1)."
        ),
    },
    "QUOTE-01": {
        "decisions": [D2],
        "add": (
            "Owner decision update: no per-quote, per-answer, per-session or per-user count limits as long as"
            " quotes are relevant and verify against sources retrieved and cited for the turn; one response may"
            " not reproduce more than 40% of any single page/section verbatim (quotes plus echoed runs)."
        ),
    },
    "REL-04": {
        "decisions": [D1, G1],
        "add": (
            "Owner decision update: stored KG JSON may contain verbatim context snippets and is tracked in Git;"
            " research papers, raw PDFs, full parsed bodies, figure crops, the source-RAG index, credentials,"
            " local paths and operational databases must not be tracked."
        ),
    },
    "REL-05": {
        "decisions": [D1],
        "add": (
            "Owner decision update: tracked KG JSON (storage/kg, storage/terminology) is kept in the Docker image;"
            " raw PDFs, full parsed paper bodies, figure crops, the source-RAG index (storage/source_index),"
            " credentials, local paths and operational databases remain excluded from build context and all"
            " layers."
        ),
    },
    "SEC-04": {
        "decisions": [TRACE],
        "add": (
            "Owner decision update: admins (verified ALS admins and every :local user) receive the raw trace from"
            " GET /traces/{turn_id}; other users receive the projected summary."
        ),
    },
    "TIL-02": {
        "decisions": [T1],
        "add": (
            "Owner decision update: external-mode checks must verify the central server's version and capabilities"
            " and fail clearly on mismatch (central servers are reported to run 0.2.19; the local interim pin is"
            " 0.2.18)."
        ),
    },
    "TIL-07": {
        "decisions": [T1],
        "title": "Exact owner-selected Tiled artifact (interim 0.2.18) and capability contract recorded",
        "add": (
            "Owner decision update: the owner-selected artifact for now is tiled==0.2.18 (wheel sha256"
            " 416079d4...fa06, sdist sha256 2f394326...1e27); the 0.2.19rc2 capability description is re-checked"
            " against 0.2.18; changing the pin invalidates H1."
        ),
    },
}
