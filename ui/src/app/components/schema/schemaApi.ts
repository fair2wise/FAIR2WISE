/**
 * Schema panel API client: the `/v1/library/schemas/*` authoring routes (V89 to V94 and V96 to V98).
 *
 * The routes exist only on `:local` and need the capability `library.author_schema`; on ALS they answer 404, so
 * `probeUserSchemas` resolves `null` and the panel hides (the probe-and-hide pattern of `libraryApi.ts`).
 * Schema text travels in JSON bodies only. Nothing here logs it or writes it to browser storage.
 */
import { ApiError, HIDDEN_STATUSES, jsonBody, v1Fetch, v1Json, v1Url } from '../apiHttp';
import { agentNetworkErrorMessage, parseAgentApiError, parseAgentApiErrorCode } from '../agentApiErrors';
import { AGENT_API_BASE } from '../data/liveAgent';

export const SCHEMAS_PATH = '/v1/library/schemas';

/** Server cap of schema text (bytes, UTF-8). */
export const MAX_SCHEMA_BYTES = 256 * 1024;
/** Server cap of the generate description (characters). */
export const MAX_DESCRIPTION_CHARS = 2000;
/** User schema names: a lowercase letter first, then lowercase letters, digits, `_` or `-`, at most 64 characters. */
export const SCHEMA_NAME_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

/** True when `name` is an acceptable user schema name (the server checks again). */
export function isValidSchemaName(name: string): boolean {
  return SCHEMA_NAME_PATTERN.test(name);
}

/** UTF-8 size of `text` in bytes (the server's cap is in bytes). */
export function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** One finding of the validator. `line` is 1-based; `column` may be absent. */
export interface SchemaProblem {
  line: number;
  message: string;
  code: string;
  column: number | null;
  fixable: boolean;
}

/** The validator report (`POST /schemas/validate`, and inside 422 bodies and generate attempts). */
export interface SchemaReport {
  ok: boolean;
  problems: SchemaProblem[];
  stats: Record<string, number>;
  /** What was checked, one phrase per item (the server's own words). */
  checked: string[];
  /** What was not checked (the server's own words). */
  note: string;
  /** True when `insert-core` would fix at least one problem. */
  fixable: boolean;
  /** Findings that do not stop a save: how the extractor will read a slot (attribute, or a relation that reads from the container). */
  warnings: SchemaProblem[];
}

/** One stored version without its text. */
export interface UserSchemaVersion {
  id: string;
  version: number;
  sha256: string;
  created_at: string;
}

/** `POST /schemas`: the saved version (`name` is the schema name). */
export interface SavedSchema extends UserSchemaVersion {
  name: string;
}

/** `GET /schemas/user`: one row per name. */
export interface UserSchemaRow {
  name: string;
  latest: UserSchemaVersion & { name?: string };
  versions: number[];
  ids: string[];
}

/** `GET /schemas/user/{name}@vN`. */
export interface StoredSchema extends SavedSchema {
  text: string;
}

/** `GET /schemas/user/{name}/diff`. */
export interface SchemaDiffResult {
  name: string;
  from: number;
  to: number;
  /** Unified diff text (may be empty when the versions are identical). */
  diff: string;
}

/** `POST /schemas/generate`. */
export interface GenerateResult {
  ok: boolean;
  text: string;
  report: SchemaReport;
  attempts: Array<{ round: number; text: string; report: SchemaReport }>;
  repairs: number;
  model: string;
  backend: 'cborg' | 'ollama';
  stopped: string | null;
}

/** `POST /schemas/preview` without `confirm`: the estimate and the confirmation token. */
export interface PreviewEstimate {
  confirmed: false;
  schema: string;
  schema_sha256: string;
  paper: string;
  backend: string;
  model: string;
  max_pages: number;
  pages: number;
  estimated_tokens: number | null;
  estimated_cost_usd: number | null;
  confirmation: string;
  limits: { page_chars: number; call_seconds: number; overall_seconds: number };
}

export interface PreviewRelation {
  relation: string;
  related_term: string;
  verified: boolean;
}

export interface PreviewTerm {
  term: string;
  category: string;
  raw_category: string | null;
  definition: string;
  page: number;
  relations: PreviewRelation[];
  attributes?: Array<{ property: string; value: string }>;
  swapped_relations?: Array<{ relation: string; from: string; to: string }>;
}

export interface PreviewSummary {
  terms: number;
  categories_exact: number;
  categories_mapped: number;
  categories_fell_back_to_thing: number;
  relations: number;
  relations_mapped: number;
  relations_became_related_to: number;
  relations_unverified: number;
  /** Relations whose slot holds a value (string, number): stored as an attribute of the term, as the extraction does. */
  relations_became_attributes?: number;
  /** Relations that were the wrong way round: stored on the other term, as the extraction does. */
  relations_swapped?: number;
}

/** `POST /schemas/preview` with `confirm: true` (the estimate fields plus the run). */
export interface PreviewResult extends Omit<PreviewEstimate, 'confirmed'> {
  confirmed: true;
  pages_used: number[];
  page_results: Array<{ page: number; terms: number; note?: string }>;
  terms: PreviewTerm[];
  summary: PreviewSummary;
  stopped: string | null;
  note: string;
}

/** An HTTP failure that carries the validator report of a 422 (`reason: schema_invalid`). */
export class SchemaInvalidError extends ApiError {
  readonly report: SchemaReport;

  constructor(message: string, report: SchemaReport, code: string | null, reason: string | null) {
    super(message, 422, code, reason);
    this.name = 'SchemaInvalidError';
    this.report = report;
  }
}

/** Parse a list of findings defensively (a missing or odd list is empty). */
function parseProblems(raw: unknown): SchemaProblem[] {
  const out: SchemaProblem[] = [];
  if (!Array.isArray(raw)) return out;
  for (const item of raw) {
    if (item === null || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    out.push({
      line: typeof row.line === 'number' && row.line >= 1 ? Math.floor(row.line) : 1,
      message: typeof row.message === 'string' ? row.message : '',
      code: typeof row.code === 'string' ? row.code : '',
      column: typeof row.column === 'number' ? row.column : null,
      fixable: row.fixable === true,
    });
  }
  return out;
}

/** Parse a report defensively: anything the server did not send becomes an empty value. */
export function parseReport(raw: unknown): SchemaReport | null {
  if (raw === null || typeof raw !== 'object') return null;
  const body = raw as Record<string, unknown>;
  if (typeof body.ok !== 'boolean' || !Array.isArray(body.problems)) return null;
  const problems = parseProblems(body.problems);
  const stats: Record<string, number> = {};
  if (body.stats && typeof body.stats === 'object') {
    for (const [key, value] of Object.entries(body.stats as Record<string, unknown>)) {
      if (typeof value === 'number') stats[key] = value;
    }
  }
  return {
    ok: body.ok,
    problems,
    warnings: parseProblems(body.warnings),
    stats,
    checked: Array.isArray(body.checked) ? body.checked.filter((x): x is string => typeof x === 'string') : [],
    note: typeof body.note === 'string' ? body.note : '',
    fixable: body.fixable === true,
  };
}

/** A POST that keeps the report of a 422 (`v1Fetch` would drop it). */
async function postWithReport<T>(path: string, body: unknown, fetcher: typeof fetch, signal?: AbortSignal): Promise<T> {
  const url = v1Url(path);
  let response: Response;
  try {
    response = await fetcher(url, { credentials: 'same-origin', ...jsonBody('POST', body), ...(signal ? { signal } : {}) });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(agentNetworkErrorMessage(AGENT_API_BASE, error), 0);
  }
  if (response.ok) return (await response.json()) as T;
  const text = await response.text();
  const typed = parseAgentApiErrorCode(text);
  const message = parseAgentApiError(text, response.status);
  if (response.status === 422) {
    try {
      const report = parseReport((JSON.parse(text) as { report?: unknown }).report);
      if (report) throw new SchemaInvalidError(message, report, typed.code, typed.reason);
    } catch (error) {
      if (error instanceof SchemaInvalidError) throw error;
    }
  }
  throw new ApiError(message, response.status, typed.code, typed.reason);
}

/** V89: validate schema text. Pass `signal` so a stale request can be cancelled. */
export async function validateSchema(text: string, signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<SchemaReport> {
  const raw = await postWithReport<unknown>(`${SCHEMAS_PATH}/validate`, { text }, fetcher, signal);
  const report = parseReport(raw);
  if (!report) throw new ApiError('The server sent a report in an unknown shape.', 200);
  return report;
}

/** V90: the text with the missing parts of the required core inserted. */
export async function insertCore(text: string, fetcher: typeof fetch = fetch): Promise<string> {
  const out = await v1Json<{ text?: unknown }>(v1Url(`${SCHEMAS_PATH}/insert-core`), jsonBody('POST', { text }), fetcher);
  if (typeof out.text !== 'string') throw new ApiError('The server sent no text.', 200);
  return out.text;
}

/** V91: save as the next version of `name`. A 422 throws {@link SchemaInvalidError} with the report. */
export function saveSchema(name: string, text: string, fetcher: typeof fetch = fetch): Promise<SavedSchema> {
  return postWithReport<SavedSchema>(SCHEMAS_PATH, { name, text }, fetcher);
}

/** V92: every user schema; `null` when this caller or server has none of the routes (the panel hides). */
export async function probeUserSchemas(fetcher: typeof fetch = fetch): Promise<UserSchemaRow[] | null> {
  let response: Response;
  try {
    response = await fetcher(v1Url(`${SCHEMAS_PATH}/user`), { credentials: 'same-origin' });
  } catch {
    return null;
  }
  if (HIDDEN_STATUSES.has(response.status)) return null;
  if (!response.ok) throw new ApiError(`Schema request failed (HTTP ${response.status}).`, response.status);
  const body = (await response.json()) as { schemas?: unknown };
  return Array.isArray(body.schemas) ? (body.schemas as UserSchemaRow[]) : [];
}

/** V93: one stored version with its text. */
export function fetchStoredSchema(id: string, fetcher: typeof fetch = fetch): Promise<StoredSchema> {
  const ref = id.startsWith('user/') ? id.slice('user/'.length) : id;
  return v1Json<StoredSchema>(v1Url(`${SCHEMAS_PATH}/user/${encodeURIComponent(ref).replace(/%40/g, '@')}`), {}, fetcher);
}

/** V94: unified diff between two versions of `name`. */
export function fetchSchemaDiff(name: string, from: number, to: number, fetcher: typeof fetch = fetch): Promise<SchemaDiffResult> {
  return v1Json<SchemaDiffResult>(v1Url(`${SCHEMAS_PATH}/user/${encodeURIComponent(name)}/diff`, { from, to }), {}, fetcher);
}

/** V98: delete one version (409 `schema_in_use` while a KG uses it). */
export async function deleteSchemaVersion(id: string, fetcher: typeof fetch = fetch): Promise<void> {
  const ref = id.startsWith('user/') ? id.slice('user/'.length) : id;
  await v1Fetch(v1Url(`${SCHEMAS_PATH}/user/${encodeURIComponent(ref).replace(/%40/g, '@')}`), { method: 'DELETE' }, fetcher);
}

/** Body of V96. */
export interface GenerateRequest {
  description: string;
  backend?: 'cborg' | 'ollama';
  model?: string;
}

/** V96: ask the model for a draft (can take minutes). */
export function generateSchema(request: GenerateRequest, signal?: AbortSignal, fetcher: typeof fetch = fetch): Promise<GenerateResult> {
  return postWithReport<GenerateResult>(`${SCHEMAS_PATH}/generate`, request, fetcher, signal);
}

/** Body of V97. */
export interface PreviewRequest {
  schema: string;
  paper_id: string;
  backend?: 'cborg' | 'ollama';
  model?: string;
  confirm?: boolean;
  confirmation?: string;
}

/** V97 without `confirm`: the estimate. */
export function previewEstimate(request: Omit<PreviewRequest, 'confirm' | 'confirmation'>, fetcher: typeof fetch = fetch): Promise<PreviewEstimate> {
  return postWithReport<PreviewEstimate>(`${SCHEMAS_PATH}/preview`, request, fetcher);
}

/** V97 with `confirm: true` and the estimate's token. */
export function previewRun(
  request: Omit<PreviewRequest, 'confirm'> & { confirmation: string },
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<PreviewResult> {
  return postWithReport<PreviewResult>(`${SCHEMAS_PATH}/preview`, { ...request, confirm: true }, fetcher, signal);
}

/**
 * Plain words for a failed schema call, from the server's status and typed reason (its own message is the
 * fallback; it never holds a URL, a key or a provider body).
 */
export function schemaErrorText(error: unknown, what: 'generate' | 'save' | 'preview' | 'other' = 'other'): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : 'The request failed.';
  const { status, reason, code } = error;
  if (reason === 'body_too_large') return 'The text is too large: the server accepts at most 256 KiB of schema text.';
  if (reason === 'schema_in_use') return 'A KG was built on this version, so it cannot be deleted. Delete or keep the KG; a new version leaves the old one in place.';
  if (reason === 'model_timeout') return 'The model did not answer in time. Try again, or pick a smaller or faster model.';
  if (reason === 'response_too_large') return 'The model answered with far too much text and the answer was discarded. Try a shorter description.';
  if (reason === 'ollama_unreachable') return 'The Ollama server did not answer. Check that it is running, or choose a CBORG model.';
  if (reason === 'model_unreachable') return 'The model server did not answer. Try again in a moment.';
  if (reason === 'model_call_failed') return 'The model call failed. Try again, or choose another model.';
  if (reason === 'cborg_ip_not_authorized') return 'CBORG does not accept this machine\'s network address, so no model call was made.';
  if (reason === 'stale_approval' || code === 'stale_approval') return 'The estimate no longer matches (schema, paper or model changed). Estimate again.';
  if (status === 501 || code === 'unsupported_capability') {
    return `${error.message || 'No model backend is configured on the server.'} A schema can still be written or pasted by hand.`;
  }
  if (status === 403 || code === 'denied') {
    return what === 'preview' || what === 'generate'
      ? 'This model is not on the free list, so it was not called. Pick one of the models the form offers.'
      : 'You are not authorized to do this.';
  }
  if (status === 401) return 'You are not authorized to do this.';
  if (status === 404) return 'Not found. The schema or paper may have been removed.';
  if (status === 409) return error.message || 'The request conflicts with the current state.';
  return error.message || 'The request failed.';
}
