/**
 * Library tab API client (A8, T40). Typed against the T35/T36 route contract:
 * `GET /v1/library`, `GET|POST /v1/library/papers`, `POST /v1/library/papers/{id}/verify`,
 * verify-all and index as jobs. The routes exist only on `:local`; on ALS they answer 404 and
 * `fetchLibraryStatus` returns `null` so the tabs hide (the probe-and-hide pattern of `judgeModel.ts`).
 * Nothing here touches `PUT /settings` or browser storage.
 */
import { ApiError, HIDDEN_STATUSES, jsonBody, v1Json, v1Url } from './apiHttp';
import type { JobRef } from './extractionApi';

export const LIBRARY_PATH = '/v1/library';

/** Verification state of a library paper's title and DOI. */
export type LibraryVerifyStatus = 'unverified' | 'verified' | 'not_found' | 'quota_open' | 'error';

/** Result of the `library_index` job for one paper (T36); `null` when never indexed. */
export type LibraryIndexStatus = 'ok' | 'no_text' | null;

/** `GET /v1/library`: whether the papers folder is mounted, plus headline counts. */
export interface LibraryStatus {
  mounted: boolean;
  paper_count: number;
  indexed_count: number;
  /** True while the OpenAlex quota breaker is open (Verify would be refused). */
  quota_open: boolean;
}

/** One PDF of the mounted papers folder. */
export interface LibraryPaper {
  id: string;
  relpath: string;
  file_name: string;
  sha256: string;
  size_bytes: number;
  title: string | null;
  doi: string | null;
  verify_status: LibraryVerifyStatus;
  verified_at: string | null;
  index_status: LibraryIndexStatus;
}

/** `GET /v1/library/papers`. */
export interface LibraryPapersResponse {
  papers: LibraryPaper[];
  total: number;
}

/** `POST /v1/library/papers`: the stored paper, and whether the sha256 already existed. */
export interface LibraryUploadResult {
  paper: LibraryPaper;
  duplicate: boolean;
}

/** `POST /v1/library/papers/{id}/verify`. */
export interface LibraryVerifyResult {
  paper: LibraryPaper;
}

/** Coerce an untrusted server object into a `LibraryStatus`. */
export function normalizeLibraryStatus(raw: unknown): LibraryStatus {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : 0);
  return {
    mounted: data.mounted === true,
    paper_count: num(data.paper_count),
    indexed_count: num(data.indexed_count),
    quota_open: data.quota_open === true,
  };
}

/**
 * Probe the library. Resolves `null` when this server or caller has no library
 * (401/403/404/405, or the API is unreachable) so the UI hides the tabs; throws `ApiError` for
 * other failures (e.g. 500).
 *
 * @param fetcher - Injectable fetch (tests).
 */
export async function fetchLibraryStatus(fetcher: typeof fetch = fetch): Promise<LibraryStatus | null> {
  let response: Response;
  try {
    response = await fetcher(v1Url(LIBRARY_PATH), { credentials: 'same-origin' });
  } catch {
    return null;
  }
  if (HIDDEN_STATUSES.has(response.status)) return null;
  if (!response.ok) {
    throw new ApiError(`Library request failed (HTTP ${response.status}).`, response.status);
  }
  return normalizeLibraryStatus(await response.json());
}

/** List the papers of the mounted folder. */
export function fetchLibraryPapers(fetcher: typeof fetch = fetch): Promise<LibraryPapersResponse> {
  return v1Json<LibraryPapersResponse>(v1Url(`${LIBRARY_PATH}/papers`), {}, fetcher);
}

/**
 * Upload one PDF (multipart field `file`). The server owns validation (type, size, dedupe by sha256);
 * the caller should also reject non-PDFs client-side before calling.
 */
export function uploadLibraryPaper(file: File, fetcher: typeof fetch = fetch): Promise<LibraryUploadResult> {
  const form = new FormData();
  form.append('file', file, file.name);
  return v1Json<LibraryUploadResult>(v1Url(`${LIBRARY_PATH}/papers`), { method: 'POST', body: form }, fetcher);
}

/** Verify one paper's title/DOI against the registry (explicit user action, cached server-side). */
export function verifyLibraryPaper(paperId: string, fetcher: typeof fetch = fetch): Promise<LibraryVerifyResult> {
  return v1Json<LibraryVerifyResult>(
    v1Url(`${LIBRARY_PATH}/papers/${encodeURIComponent(paperId)}/verify`),
    { method: 'POST' },
    fetcher,
  );
}

/**
 * Submit the `library_verify` job for every paper. `nonce` makes a deliberate re-run a new job
 * (job identity is idempotent on identical inputs).
 */
export function verifyAllLibraryPapers(nonce?: string, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<JobRef>(v1Url('/v1/jobs'), jsonBody('POST', { kind: 'library_verify', inputs: {}, nonce }), fetcher);
}

/** Submit the `library_index` job (Index for QA). */
export function indexLibraryPapers(nonce?: string, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<JobRef>(v1Url('/v1/jobs'), jsonBody('POST', { kind: 'library_index', inputs: {}, nonce }), fetcher);
}

/** True when the file looks like a PDF by name or MIME type (client-side pre-check only). */
export function isPdfFile(file: Pick<File, 'name' | 'type'>): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
}
