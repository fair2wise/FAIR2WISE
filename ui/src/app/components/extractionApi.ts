/**
 * Extraction tab API client (A8, T40). Typed against the T38/T39/T42 contract:
 * `GET /v1/library/kgs|schemas|models`, `POST /v1/library/estimate`, export of a bundle, and the
 * `library_extract` job on the generic `/v1/jobs` routes (`GET /v1/jobs?state=&kind=`,
 * `GET /v1/jobs/{id}/logs?after=`, `GET /v1/jobs/{id}/stream` with `Last-Event-ID` resume).
 * The model chosen here rides in the job inputs; it never goes through `PUT /settings`.
 */
import { readSseStream, type SseBlock } from './data/liveAgent';
import { jsonBody, v1Fetch, v1Json, v1Url } from './apiHttp';

/** Job kind submitted by the Extraction tab. */
export const EXTRACT_JOB_KIND = 'library_extract';

/** Lifecycle state of a `/v1` job. */
export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

/** Terminal states: no further progress or logs arrive. */
export const TERMINAL_JOB_STATES: ReadonlySet<JobState> = new Set(['succeeded', 'failed', 'cancelled']);

/** Reference returned when a job is submitted (identical inputs return the same job). */
export interface JobRef {
  id: string;
  kind: string;
  state: JobState;
}

/** A job record. `progress` is `dict[str, int]` (e.g. `{done: 3, total: 10}`). */
export interface JobInfo extends JobRef {
  progress: Record<string, number>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

/** `GET /v1/jobs`. */
export interface JobListResponse {
  jobs: JobInfo[];
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
}

/** `GET /v1/library/schemas`. */
export interface SchemasResponse {
  schemas: SchemaChoice[];
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
  return v1Json<JobRef>(
    v1Url('/v1/jobs'),
    jsonBody('POST', { kind: EXTRACT_JOB_KIND, inputs: { params: request } }),
    fetcher,
  );
}

/** Fetch one job. */
export function fetchJob(jobId: string, fetcher: typeof fetch = fetch): Promise<JobInfo> {
  return v1Json<JobInfo>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}`), {}, fetcher);
}

/** Jobs of a kind in a state (re-attach after a reload: `state=running`, `kind=library_extract`). */
export function fetchJobs(
  filter: { state?: JobState; kind?: string },
  fetcher: typeof fetch = fetch,
): Promise<JobListResponse> {
  return v1Json<JobListResponse>(v1Url('/v1/jobs', filter), {}, fetcher);
}

/** Log lines after `seq` (0 for all); the server bounds the buffer. */
export function fetchJobLogs(jobId: string, after = 0, fetcher: typeof fetch = fetch): Promise<JobLogsResponse> {
  return v1Json<JobLogsResponse>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/logs`, { after }), {}, fetcher);
}

/** Request cancellation (the job stops between PDFs). */
export function cancelJob(jobId: string, fetcher: typeof fetch = fetch): Promise<JobInfo> {
  return v1Json<JobInfo>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`), { method: 'POST' }, fetcher);
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

/** Options of `streamJobEvents`. */
export interface JobStreamOptions {
  /** Called for every event (`job_log`, progress, state changes), with the SSE `id` when present. */
  onEvent: (block: SseBlock) => void;
  /** Resume point: sent as `Last-Event-ID` so a reconnect does not replay or lose lines. */
  lastEventId?: string;
  signal?: AbortSignal;
  fetcher?: typeof fetch;
}

/**
 * Follow `GET /v1/jobs/{id}/stream` with the shared SSE reader from `liveAgent.ts`.
 * Resolves when the server closes the stream; the caller reconnects with the last seen id if the
 * job is not terminal. Throws `ApiError` on a non-2xx status.
 */
export async function streamJobEvents(jobId: string, options: JobStreamOptions): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId;
  const response = await v1Fetch(
    v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/stream`),
    { headers, signal: options.signal },
    options.fetcher,
  );
  await readSseStream(response, block => {
    options.onEvent(block);
  });
}
