/**
 * Extraction tab API client (A8, T40). Typed against the T38/T39/T42 contract:
 * `GET /v1/library/kgs|schemas|models`, `POST /v1/library/estimate`, export of a bundle, and the
 * `library_extract` job on the generic `/v1/jobs` routes (`GET /v1/jobs?state=&kind=`,
 * `GET /v1/jobs/{id}/logs?after=` polled with a cursor; the SSE `/stream` route is not used by the UI).
 * The model chosen here rides in the job inputs; it never goes through `PUT /settings`.
 */
import { jsonBody, v1Json, v1Url } from './apiHttp';

/** Job kind submitted by the Extraction tab. */
export const EXTRACT_JOB_KIND = 'library_extract';

/** Job kind of "Find supporting text" (the recovery of passages for an existing KG; no model is used). */
export const PASSAGES_JOB_KIND = 'library_passages';

/** Lifecycle state of a `/v1` job. */
export type JobState =
  | 'queued'
  | 'running'
  | 'paused_budget'
  | 'paused_provider'
  | 'awaiting_review'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

/** Terminal states: no further progress or logs arrive. */
export const TERMINAL_JOB_STATES: ReadonlySet<JobState> = new Set(['succeeded', 'failed', 'cancelled']);

/** Reference returned when a job is submitted (identical inputs return the same job). */
export interface JobRef {
  id: string;
  kind: string;
  state: JobState;
}

/** Final word of a run: the job state, except a failed job with some papers done is `partially_failed`. */
export type RunOutcome = JobState | 'partially_failed';

/** Papers of a run (counts are exact; `failed_list` is bounded and only in the detailed form). */
export interface RunPapers {
  total: number;
  done: number;
  failed: number;
  skipped: number;
  failed_list?: { name: string; reason: string }[];
}

/** KG growth counters of a run (absent keys mean "not reported yet"). */
export interface RunGrowth {
  entities_new?: number;
  entities_merged?: number;
  relations_new?: number;
  relations_merged?: number;
  entities_total?: number;
  relations_total?: number;
}

/** Supporting-text coverage counts of one paper (or of a whole run): terms with and without a passage, and how found. */
export interface PassageCounts {
  terms: number;
  with_passage: number;
  quoted: number;
  located: number;
  /** Sentences the model gave that were not found in the paper (not stored). */
  rejected: number;
  uncovered: number;
  /** Relation evidence (absent on an older server): relations, those with a sentence naming both ends, sentences stored, without. */
  relations?: number;
  relations_with_evidence?: number;
  evidence_sentences?: number;
  relations_without?: number;
}

/** `summary.passages` of an extraction: its coverage per paper (file name) and in total. */
export interface PassagesBlock {
  total: PassageCounts;
  papers: Record<string, PassageCounts>;
}

/** Why a paper was not searched by a recovery (`recovery.papers[name].reason`). */
export type RecoveryReason = 'ambiguous_name' | 'missing' | 'no_text_layer' | 'unreadable';

/** One paper's result in a recovery: counts, or a `reason` (with the server's `text`) when it was skipped. */
export interface RecoveryPaperResult {
  terms?: number;
  with_passage_before?: number;
  located_added?: number;
  newly_covered?: number;
  still_uncovered?: number;
  relations?: number;
  relations_with_evidence_before?: number;
  evidence_added?: number;
  relations_newly_covered?: number;
  relations_still_without?: number;
  reason?: RecoveryReason | string;
  text?: string;
}

/** `summary.recovery` of a `library_passages` job. */
export interface RecoveryBlock {
  total: {
    terms: number;
    with_passage_before: number;
    located_added: number;
    newly_covered: number;
    still_uncovered: number;
    relations?: number;
    relations_with_evidence_before?: number;
    evidence_added?: number;
    relations_newly_covered?: number;
    relations_still_without?: number;
    papers_processed: number;
    papers_skipped: number;
  };
  papers: Record<string, RecoveryPaperResult>;
}

/** Share of a paper's relations that must have a sentence naming both ends before the run view stops warning (the server logs the same 50%). */
export const RELATION_WARN_RATIO = 0.5;

/** Share of a paper's terms that must carry a passage before the run view stops warning (the server logs the same 90%). */
export const PASSAGE_WARN_RATIO = 0.9;

/** Plain-words reasons a paper was not searched. */
export const RECOVERY_REASON_TEXT: Record<string, string> = {
  ambiguous_name: 'Two library files have this name and the KG does not say which one was used, so it was skipped.',
  missing: 'This paper is not in the library, so it was skipped.',
  no_text_layer: 'This PDF has no text layer (a scan), so there is nothing to search.',
  unreadable: 'This PDF could not be read, so it was skipped.',
};

/**
 * Live status of a job from `GET /v1/jobs/{id}/progress` (key `summary`) or the rows of
 * `GET /v1/jobs?detail=summary`. Everything is read from the job store on the server.
 */
export interface JobRunSummary {
  outcome: RunOutcome;
  state: JobState;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
  /** `phase` is `parsing`, `chunking`, `extracting`, `merging into the KG` or `finished` while the run is live. */
  stage: { name: string | null; status: string | null; phase: string | null };
  /** Display name of the paper being processed (never a path); only while running. */
  current_paper: string | null;
  papers: RunPapers;
  growth: RunGrowth;
  /** Other stage counters, e.g. `paper_pages_done` and `paper_pages_total` of the current paper. */
  counters: Record<string, number>;
  params: { bundle?: string; model?: string; backend?: string; schema?: string; topic?: string };
  /** Supporting-text coverage of an extraction (papers finished so far); absent on an older server or another job kind. */
  passages?: PassagesBlock;
  /** Result of a `library_passages` job (papers finished so far); absent for other job kinds. */
  recovery?: RecoveryBlock;
  /** Set when a user schema's prompt text was condensed for this run (sizes in characters); `null`/absent otherwise. */
  schema_prompt_condensed?: SchemaPromptCondensed | null;
  error: string | null;
  /** `kept` lines are in the store of `total` written; `cap` is the per-job row cap. */
  logs: { kept: number; total: number; cap: number };
}

/**
 * A job as the UI uses it. The server's handle names the id `job_id` and has no counters; `normalizeJob`
 * fills `id`, `progress` (`{done, total}` papers), `error` and the dates from the handle and its `summary`.
 */
export interface JobInfo extends JobRef {
  progress: Record<string, number>;
  error: string | null;
  created_at: string;
  updated_at: string;
  summary?: JobRunSummary;
}

/** Normalize a job body from the server (the handle's `job_id` or an `id`, with an optional `summary`). */
export function normalizeJob(raw: unknown): JobInfo {
  const body = (raw ?? {}) as Record<string, unknown>;
  const summary = (body.summary ?? undefined) as JobRunSummary | undefined;
  const progress =
    (body.progress as Record<string, number> | undefined)
    ?? (summary ? { done: summary.papers.done, total: summary.papers.total } : {});
  const stamp = (value: unknown) => (typeof value === 'string' ? value : '');
  return {
    id: String(body.id ?? body.job_id ?? ''),
    kind: String(body.kind ?? ''),
    state: (body.state as JobState) ?? 'queued',
    progress,
    error: (body.error as string | null | undefined) ?? summary?.error ?? null,
    created_at: stamp(body.created_at) || stamp(body.submitted_at),
    updated_at: stamp(body.updated_at) || stamp(body.submitted_at),
    ...(summary ? { summary } : {}),
  };
}

/** `GET /v1/jobs`. */
export interface JobListResponse {
  jobs: JobInfo[];
}

/** `GET /v1/jobs/{id}/progress`: the per-phase telemetry plus the additive `summary`. */
export interface JobProgressResponse {
  job_id: string;
  summary?: JobRunSummary;
}

/** One log line of a job (T33). `seq` is the SSE event id used to resume. */
export interface JobLogEntry {
  seq: number;
  at: string;
  level: 'debug' | 'info' | 'warning' | 'error';
  stage: string;
  message: string;
}

/** `GET /v1/jobs/{id}/logs?after=`. */
export interface JobLogsResponse {
  logs: JobLogEntry[];
  /** Lines the store holds for the job. Absent on an older server. */
  kept?: number;
  /** Lines ever written for the job (`total - kept` fell off the per-job cap). Absent on an older server. */
  total?: number;
}

/** Summary of one KG bundle (`<F2W_WORKDIR>/kgs/<name>/`). */
export interface KgBundleSummary {
  name: string;
  topic: string | null;
  schema: string;
  model: string | null;
  backend: string | null;
  paper_count: number;
  parent: string | null;
  /** True when the bundle was reverse-built by `kg_to_terms`. */
  reconstructed: boolean;
  updated_at: string | null;
}

/** `GET /v1/library/kgs`. */
export interface KgBundlesResponse {
  bundles: KgBundleSummary[];
  /** Plain KG files (`*.json` in the release directory) that can be continued into a new bundle. */
  sources: string[];
}

/** KG bundle names: a letter or digit first, then letters, digits, `_` or `-`, at most 64 characters. */
export const KG_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** True when `name` is an acceptable KG bundle name (the server validates again). */
export function isValidKgName(name: string): boolean {
  return KG_NAME_PATTERN.test(name);
}

/** One extraction schema from the registry. */
export interface SchemaChoice {
  /** Registry id, e.g. `matkg_schema@0.3.1`; this is what the estimate and the job take. */
  name: string;
  label: string;
  description: string | null;
  version?: string;
  review_status?: string;
  sha256?: string;
  /** `user` for a schema written in the schema panel (id `user/<name>@vN`); absent or `shipped` for the registry sets. */
  origin?: 'shipped' | 'user';
  schema_name?: string;
  schema_version?: number;
}

/** True for a user schema choice (id `user/<name>@vN`). */
export function isUserSchemaChoice(choice: Pick<SchemaChoice, 'name' | 'origin'>): boolean {
  return choice.origin === 'user' || choice.name.startsWith('user/');
}

/** `GET /v1/library/schemas`. */
export interface SchemasResponse {
  schemas: SchemaChoice[];
}

/** State of the deployment's Ollama server listing (no URL is ever sent to the browser). */
export type OllamaListingState = 'ok' | 'empty' | 'unset' | 'unreachable' | 'error';

/** The Ollama block of `GET /v1/library/models` and the body of `GET /v1/ollama/models`. */
export interface OllamaListing {
  state: OllamaListingState;
  models: string[];
  /** Short fixed reason for `unset`/`unreachable`/`error`. */
  error: string | null;
}

/** `GET /v1/library/models` (the live provider list, as for the judge model; never hardcoded). */
export interface ModelsResponse {
  available: boolean;
  models: string[];
  source: 'live' | 'unavailable' | string;
  error: string | null;
  /** Stable code of a known refusal (`cborg_ip_not_authorized`), else null/absent. */
  error_code?: string | null;
  /** Provider backend name (e.g. `openai`), or null. */
  backend?: string | null;
  /** Ollama models, listed independently of CBORG (absent on an older server). */
  ollama?: OllamaListing;
}

/** Body of `POST /v1/library/estimate` (pages times model). */
export interface EstimateRequest {
  paper_ids: string[];
  model: string;
  schema: string;
}

/** Deterministic cost estimate; `confirmation` is echoed in the start request after the user confirms. */
export interface EstimateResponse {
  pages: number;
  /** Number of papers estimated. */
  paper_count?: number;
  /** Papers whose page count is unknown (the estimate is then a lower bound). */
  unknown_pages?: number;
  model: string;
  estimated_tokens: number | null;
  estimated_cost_usd: number | null;
  confirmation: string;
  /** Only for a user schema: the full size of its prompt text and the budgets extraction bounds it to. */
  schema_prompt?: SchemaPromptSizes;
}

/** `estimate.schema_prompt`: characters of the full schema prompt text and the per-backend budgets. */
export interface SchemaPromptSizes {
  full_chars: number;
  budgets: { ollama: number; cborg: number };
}

/** `summary.schema_prompt_condensed`: a user schema's prompt text was condensed for this run. */
export interface SchemaPromptCondensed {
  full_chars: number;
  used_chars: number;
  budget: number;
}

/** The sentence that says a user schema's prompt is condensed, or `null` when it fits (or is not a user schema). */
export function condensedSchemaNote(sizes: SchemaPromptSizes | undefined, backend: 'ollama' | 'cborg'): string | null {
  if (!sizes) return null;
  const budget = sizes.budgets[backend];
  if (sizes.full_chars <= budget) return null;
  return `This schema's prompt text is ${sizes.full_chars.toLocaleString('en-US')} characters, over the ${budget.toLocaleString('en-US')} `
    + `allowed for ${backend === 'ollama' ? 'Ollama' : 'CBORG'}. Extraction will send a condensed view (class names with their first `
    + 'sentence, relations with domain and range) instead of the full text.';
}

/**
 * `params` of a `library_extract` job (T39): `bundle`, `papers`, `model`, `schema`, `topic`, `confirmation`
 * and the client's `nonce`. A continuation first creates its child bundle with `continueKg`; the job then
 * extracts into that existing bundle, so it carries no `continue_from`.
 */
export interface StartExtractionRequest {
  /** Bundle name (a new bundle; for a continuation, the child created by `continueKg`). */
  bundle: string;
  topic: string | null;
  /** Registry id such as `matkg_schema@0.3.1`; locked on continuation. */
  schema: string;
  model: string;
  /** Set to `ollama` when `model` comes from the Ollama list; omitted for CBORG (the server default). */
  backend?: 'ollama';
  /** T35 library paper ids. */
  papers: string[];
  confirmation: string;
  /** Makes a deliberate re-run a new job (job identity is idempotent on identical inputs). */
  nonce?: string;
}

/** Body of `POST /v1/library/kgs/continue`: `name` plus exactly one of `parent` or `source`. */
export interface ContinueKgRequest {
  name: string;
  /** Parent bundle name. */
  parent?: string;
  /** A `*.json` KG file in the release directory (one of `KgBundlesResponse.sources`). */
  source?: string;
  /** Required for a `source`; a `parent` keeps its locked schema. */
  schema?: string;
  topic?: string;
}

/** Response of `POST /v1/library/kgs/continue`: the new bundle plus any reverse-build warnings. */
export interface ContinueKgResponse extends KgBundleSummary {
  warnings: string[];
}

/** `POST /v1/library/kgs/{name}/export`: where the bundle was written (inside the release directory). */
export interface ExportResult {
  name: string;
  file_name: string;
  /** Size of the exported file on the server; absent from an older server. */
  size_bytes?: number;
}

/** URL that streams the exported `matkg_<name>.json` to the browser (404 until the KG has been exported). */
export function kgDownloadUrl(name: string): string {
  return v1Url(`/v1/library/kgs/${encodeURIComponent(name)}/download`);
}

/** List KG bundles. */
export function fetchKgBundles(fetcher: typeof fetch = fetch): Promise<KgBundlesResponse> {
  return v1Json<KgBundlesResponse>(v1Url('/v1/library/kgs'), {}, fetcher);
}

/** List the schema registry choices. */
export function fetchSchemas(fetcher: typeof fetch = fetch): Promise<SchemasResponse> {
  return v1Json<SchemasResponse>(v1Url('/v1/library/schemas'), {}, fetcher);
}

/** List extraction models (live provider list; `available=false` without a backend). */
export function fetchExtractionModels(fetcher: typeof fetch = fetch): Promise<ModelsResponse> {
  return v1Json<ModelsResponse>(v1Url('/v1/library/models'), {}, fetcher);
}

/** Ask for the cost estimate of an extraction. */
export function estimateExtraction(request: EstimateRequest, fetcher: typeof fetch = fetch): Promise<EstimateResponse> {
  return v1Json<EstimateResponse>(v1Url('/v1/library/estimate'), jsonBody('POST', request), fetcher);
}

/** Submit a `library_extract` job. */
export function startExtraction(request: StartExtractionRequest, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<unknown>(
    v1Url('/v1/jobs'),
    jsonBody('POST', { kind: EXTRACT_JOB_KIND, inputs: { params: request } }),
    fetcher,
  ).then(raw => {
    const { id, kind, state } = normalizeJob(raw);
    return { id, kind, state };
  });
}

/** Fetch one job. */
export function fetchJob(jobId: string, fetcher: typeof fetch = fetch): Promise<JobInfo> {
  return v1Json<unknown>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}`), {}, fetcher).then(normalizeJob);
}

/**
 * Jobs of a kind, newest first. `detail: 'summary'` adds each job's live `summary` (a runs list; the server keeps
 * it to 50 rows).
 */
export function fetchJobs(
  filter: { state?: JobState; kind?: string; limit?: number; detail?: 'summary' },
  fetcher: typeof fetch = fetch,
): Promise<JobListResponse> {
  return v1Json<{ jobs: unknown[] }>(v1Url('/v1/jobs', filter), {}, fetcher).then(body => ({
    jobs: (body.jobs ?? []).map(normalizeJob),
  }));
}

/** The live summary of one job (`GET /v1/jobs/{id}/progress`); `null` from a server that does not send it yet. */
export function fetchJobSummary(jobId: string, fetcher: typeof fetch = fetch): Promise<JobRunSummary | null> {
  return v1Json<JobProgressResponse>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/progress`), {}, fetcher).then(
    body => body.summary ?? null,
  );
}

/** Options of `fetchJobLogs`. */
export interface JobLogsOptions {
  /** Most lines to return (the server allows 1 to 1000). */
  limit?: number;
  /** Ignore `after` and return the newest `limit` lines: how a reloaded page finds its cursor. */
  tail?: boolean;
}

/** Log lines after `seq` (`-1` for all kept lines, since the first line has seq 0), or the newest with `tail`. */
export function fetchJobLogs(
  jobId: string,
  after = -1,
  fetcher: typeof fetch = fetch,
  options: JobLogsOptions = {},
): Promise<JobLogsResponse> {
  return v1Json<JobLogsResponse>(
    v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/logs`, {
      after,
      ...(options.limit !== undefined ? { limit: options.limit } : {}),
      ...(options.tail ? { tail: 'true' } : {}),
    }),
    {},
    fetcher,
  );
}

/** Request cancellation (the job stops between PDFs). */
export function cancelJob(jobId: string, fetcher: typeof fetch = fetch): Promise<JobInfo> {
  return v1Json<unknown>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`), { method: 'POST' }, fetcher).then(
    normalizeJob,
  );
}

/** Export a bundle to the configured release directory as `matkg_<name>.json` (sends `{confirm: true}`). */
export function exportKgBundle(name: string, fetcher: typeof fetch = fetch): Promise<ExportResult> {
  return v1Json<ExportResult>(
    v1Url(`/v1/library/kgs/${encodeURIComponent(name)}/export`),
    jsonBody('POST', { confirm: true }),
    fetcher,
  );
}

/** Create a bundle by continuing a bundle or a plain KG file (reverse build via `kg_to_terms`). */
export function continueKg(request: ContinueKgRequest, fetcher: typeof fetch = fetch): Promise<ContinueKgResponse> {
  return v1Json<ContinueKgResponse>(v1Url('/v1/library/kgs/continue'), jsonBody('POST', request), fetcher);
}

/**
 * Start "Find supporting text" for a KG bundle: `POST /v1/library/kgs/{name}/passages` answers the job handle. A new `nonce`
 * makes every click a new job (the server also refuses a second run while one is queued or running).
 */
export function startPassages(name: string, nonce: string, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<unknown>(
    v1Url(`/v1/library/kgs/${encodeURIComponent(name)}/passages`),
    jsonBody('POST', { nonce }),
    fetcher,
  ).then(raw => {
    const { id, kind, state } = normalizeJob(raw);
    return { id, kind, state };
  });
}

/** Plain words for the typed refusals of `startPassages` (`reason` of the error body), else the server's message. */
export function passagesReasonText(reason: string | null | undefined, fallback: string): string {
  switch (reason) {
    case 'no_source_papers': return 'This KG does not record which papers its terms came from, so there is nothing to search.';
    case 'bundle_busy': return 'Another job is working on this KG now. Try again when it ends.';
    case 'passages_in_progress': return 'Supporting text is already being searched for this KG.';
    default: return fallback;
  }
}
