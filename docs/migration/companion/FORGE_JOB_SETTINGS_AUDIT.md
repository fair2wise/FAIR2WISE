# What FORGE does with model settings on background paper jobs

**Source:** uploaded forge_rsoxs_analysis-main(2).zip, archive-recorded commit f1cd2ded95f26d8e43569dc94613b1dfde6b2045. This is a static trace of folder ingestion and bulk PDF parsing, not a live demonstration or a claim about every FORGE agent/worker.

## 1. Explicit model selection is captured for the running job

FolderIngestPanel reads useResolvedModel('kg') and sends that value in startFolderIngest. The server records the model, concurrency, thinking budget and pipeline fingerprint in the job configuration, passes the same model argument into _run_folder_ingest_background, and that worker's closure forwards it to each AddPublicationRequest. IngestJournal.acquire serializes/hashes the configuration and persists it. [FGS-01 to FGS-05]

**Call-flow inference:** changing the browser selector after that request does not modify the already-running explicit-model job in these paths. A later start request can carry the new selection. The UI state is not polled for a replacement model inside the worker. Bulk PDF parsing follows the same request-model-to-background-task pattern. [FGS-08, FGS-09]

## 2. Restart recovery keeps the saved argument, not every resolved setting

_resume_interrupted_folder_ingest loads previous['config'], drops _pipeline, and invokes start_pdf_parse or start_folder_ingest with the remaining values. The explicit model value is retained; _ingest_pipeline_revision is calculated again. When a restarted request receives a different configuration ID, the old job can be marked superseded. [FGS-06, FGS-07]

Thus do not claim that every restart reuses an identical settings snapshot or necessarily the same job ID. This audit also does not prove every lower-level cache key covers every setting.

## 3. Default/environment gaps

The request model may be None. llm_refine uses model or default_ollama_model(); the helper consults OLLAMA_MODEL/current server defaults. The provider path also reads configuration such as VLLM_BASE_URL from the environment. A saved requested model is therefore not a complete frozen record of the effective inference configuration. [FGS-10 to FGS-12]

A changed browser selector alone is not the same thing as a server-default/environment change. The audit supports fixed explicit-model behavior, but not an unconditional all-settings-are-frozen guarantee. Separate resolver enable/budget controls also have live pause/drain behavior. [FGS-13]

## 4. Proposed F2W adoption (O03, not yet approved)

Keep the useful per-submission explicit model behavior. Strengthen reproducibility by resolving and recording nonsecret effective settings once for the job, while rate limits, cancellation and authorization remain live. A changed-model resume should create an explicit settings/execution revision and retain old result provenance. Continue using F2W model classes and frontend choices; do not port FORGE's default model/provider constants. Full details: JOB_SETTINGS_CONTRACT.md.

## 5. Exact source references

| ID | Source location | Evidence |
| --- | --- | --- |
| FGS-01 | `frontend/src/components/KG/FolderIngestPanel.tsx` L73-L125 | UI resolves the KG model and includes it in the start request. |
| FGS-02 | `frontend/src/lib/knowledgeAgentApi.ts` L1718-L1753 | Client serializes start parameters; it does not update a running job when the selector changes. |
| FGS-03 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3416-L3472 | Start records requested model and relevant job settings, then passes them to the worker. |
| FGS-04 | `als_knowledge_agent/src/als_knowledge_agent/graph/ingest_journal.py` L98-L126 | Journal keys the sorted serialized configuration and stores it. |
| FGS-05 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3167-L3243 | Worker closure forwards its model argument for each paper. |
| FGS-06 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3048-L3060 | Pipeline fingerprint includes default model, parser and an environment-supplied model revision. |
| FGS-07 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3063-L3099 | Recovery reuses stored request config, removes _pipeline and restarts through the start functions. |
| FGS-08 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3770-L3793 | Bulk PDF parse journals and passes the model argument to its background task. |
| FGS-09 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L2905-L2937 | PDF background worker passes its model into run_pdf_parse. |
| FGS-10 | `als_knowledge_agent/src/als_knowledge_agent/graph/pdf_extraction.py` L348-L365 | Omitted model is resolved with default_ollama_model at the inference path. |
| FGS-11 | `als_knowledge_agent/src/als_knowledge_agent/graph/pdf_extraction.py` L410-L426 | Provider transport settings can be read from the server environment. |
| FGS-12 | `als_knowledge_agent/src/als_knowledge_agent/graph/llm_defaults.py` L38-L53 | Default model helper consults the current process environment. |
| FGS-13 | `als_knowledge_agent/src/als_knowledge_agent/server.py` L3594-L3632 | Separate paid resolver controls can pause/drain a worker; not all operational controls are immutable. |
| FGS-14 | `docs/json-paper-staging.md` L1-L50 | Donor has JSON staging and later model-free publication, but this is not the same schema as the F2W KG JSON. |

forge_job_settings_source_map.json contains line excerpts and file hashes for reproducible inspection. No donor or F2W application tests were run for this audit. No credentials, source PDFs or runtime databases are included.
