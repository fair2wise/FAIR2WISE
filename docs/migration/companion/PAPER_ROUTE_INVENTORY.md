# v4 exposure rule for the source route inventory

## Draft v5 integration requirements

Versioned JSON remains the KG creation artifact; Tiled 0.2.19 consumes it through the separate adapter in JSON_KG_PUBLICATION_SPEC.md. Preserve current scientific schema logic and inheritance; the measurement/technique split is not an acceptance requirement. Relevant exact cited quotations are allowed while complete source papers and original crops remain internal. Any earlier conditional wording about O01/O02/O04 in source-derived commentary is superseded by OWNER_CLARIFICATIONS_V5.md; source observations themselves remain unchanged. O03 is the only pending owner policy, with donor behavior documented in FORGE_JOB_SETTINGS_AUDIT.md.


The following inventory remains source evidence, not permission to serve source material to users. D19 requires full papers, parsed bodies and figure crops to remain agent-private. Port every paper capability, but keep raw-read/image endpoints authenticated internal-only or return safe derived metadata through a separate projection. Existing FORGE raw-source UI routes are an explicit compatibility-policy exception, not a reason to leak source material. `paper_route_inventory.json` includes v4_exposure review dispositions. O02 was resolved: relevant exact cited quotations are permitted, while full-source serving remains prohibited.

# FORGE paper API migration inventory

Exact donor methods, paths and functions. All paper behavior must have equivalent F2W application and supported HTTP access. Shared handlers require splitting, not copying. This inventory covers the server and three route modules; T17 must also trace FORGE gateway/frontend callers.

## GET /api/models
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:206-244` - `list_models`
- Disposition: **integrate_existing_F2W_contract**. Use existing F2W model/provider settings and health semantics; no donor defaults.

## GET /health
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:248-249` - `health`
- Disposition: **integrate_existing_F2W_contract**. Use existing F2W model/provider settings and health semantics; no donor defaults.

## GET /api/graph/summary
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:269-290` - `graph_summary`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/vocab_quality
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:294-314` - `vocab_quality`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/entities
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:318-332` - `list_entities`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/full
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:336-360` - `graph_full`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/growth
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:364-377` - `graph_growth`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/recent
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:381-386` - `graph_recent`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/node_edges
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:390-411` - `graph_node_edges`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/co_measured
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:415-525` - `graph_co_measured`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/ingest_fit
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:529-768` - `ingest_fit`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/live_session/open
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:818-849` - `live_session_open`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/live_session/close
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:853-862` - `live_session_close`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## GET /api/graph/live_session/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:866-873` - `live_session_status`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/ingest_reduction
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:877-894` - `ingest_reduction`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## GET /api/graph/paper_ingest/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:967-983` - `paper_ingest_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/add_publication
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:987-1019` - `add_publication`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/upload_paper
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:1504-1652` - `upload_paper`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/simulate_paper
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:1682-1748` - `simulate_paper`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/add_annotation
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:1752-1863` - `add_annotation`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/admin_status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:1888-1899` - `graph_admin_status`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## POST /api/graph/vocab_cleanup/propose
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:1950-1994` - `vocab_cleanup_propose`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/vocab_cleanup/apply
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2012-2046` - `vocab_cleanup_apply`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/backfill_extractions
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2050-2371` - `backfill_extractions`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/reset
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2375-2464` - `graph_reset`
- Disposition: **replace_with_scoped_reviewed_maintenance**. Do not port global reset. Preserve required paper-maintenance intent with snapshot-scoped reviewed changes.

## GET /api/graph/catalog-preview
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2472-2597` - `catalog_preview`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## POST /api/graph/enrich
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2706-2764` - `start_enrichment`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/enrich/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2768-2770` - `enrichment_status`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## POST /api/graph/enrich/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2774-2778` - `stop_enrichment`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## POST /api/graph/topic_sweep
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2832-2842` - `start_topic_sweep`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/topic_sweep/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2846-2848` - `topic_sweep_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/topic_sweep/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:2852-2856` - `stop_topic_sweep`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/ingest_folder
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3338-3472` - `start_folder_ingest`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/ingest_telemetry
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3476-3516` - `ingest_telemetry`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/ingest_folder/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3520-3539` - `folder_ingest_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/staged_import/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3543-3548` - `staged_import_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/uncertainty/summary
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3552-3591` - `scientific_uncertainty_summary`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/uncertainty/cborg/control
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3602-3632` - `set_cborg_control`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/local_review/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3647-3662` - `local_review_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/local_review
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3666-3696` - `start_local_review`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/local_review/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3700-3717` - `stop_local_review`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/uncertainty/cases
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3724-3733` - `scientific_uncertainty_cases`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/ingest_folder/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3737-3741` - `stop_folder_ingest`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/pdf_parse
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3745-3793` - `start_pdf_parse`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/pdf_parse/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3797-3804` - `pdf_parse_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/pdf_parse/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3808-3813` - `stop_pdf_parse`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/table_csv/{entity_id}
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:3930-3948` - `table_csv`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/publication_figures/{publication_id}
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4031-4075` - `publication_figures`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/figure_image/{entity_id}
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4079-4108` - `figure_image`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/figure_classifications
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4207-4244` - `figure_classifications`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/parse_status
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4248-4342` - `parse_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/review_resolve
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4360-4441` - `review_resolve`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## POST /api/graph/review_resolve_bulk
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4461-4573` - `review_resolve_bulk`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## POST /api/graph/figure_scale
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4592-4655` - `figure_scale_record`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/dual_fit_queue
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4659-4726` - `dual_fit_queue`
- Disposition: **remain_in_FORGE**. Experimental/live-run/simulation behavior is outside F2W ownership.

## GET /api/graph/review_queue
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4743-4915` - `review_queue`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /api/graph/paper_extraction
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:4954-5054` - `paper_extraction`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/classify_figures
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:5089-5122` - `classify_figures`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /api/graph/paper_subgraph
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:5126-5190` - `paper_subgraph_route`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/extract_figures
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:5209-5243` - `extract_figures`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /api/graph/analyze_figure
- Source: `als_knowledge_agent/src/als_knowledge_agent/server.py:5263-5387` - `analyze_figure`
- Disposition: **split_paper_portion**. Port literature projection/orchestration only; keep experimental graph, dual-fit triggers and mixed-context composition in FORGE.

## GET /roots
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:107-109` - `ingest_roots`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /list
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:113-163` - `list_folder`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /index
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:225-243` - `start_index`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /index/stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:247-261` - `stop_index`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /index/status
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:265-281` - `index_status`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /domains
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:290-299` - `folder_domains`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /search
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_folders_routes.py:381-412` - `search_folder`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /run
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/staged_import_routes.py:210-212` - `importer_run`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /start
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/staged_import_routes.py:216-237` - `start_importer`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/staged_import_routes.py:241-259` - `stop_importer`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /status
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:344-411` - `campaign_state`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /run
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:420-430` - `run_stage`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /round3
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:434-470` - `run_round3`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /stop
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:474-497` - `stop_stage`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /policy
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:506-507` - `get_policy`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## PUT /policy
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:511-518` - `put_policy`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /review
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:527-534` - `get_review`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /review/decide
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:547-555` - `decide_review`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /approval
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:570-608` - `grant_approval`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## DELETE /approval
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:612-614` - `revoke`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /requeue_paused
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:618-635` - `requeue_paused`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## GET /chunks/summary
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:644-666` - `chunk_summary`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.

## POST /chunks/plan
- Source: `als_knowledge_agent/src/als_knowledge_agent/graph/term_campaign_routes.py:683-693` - `plan_chunks`
- Disposition: **port_paper_contract**. Preserve paper HTTP behavior via F2W application service and FORGE compatibility adapter; verify auth/effects.
