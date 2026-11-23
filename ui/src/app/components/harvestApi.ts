/**
 * Harvester API client (A8, T46). Typed against the T45 contract (policy rows V76-V79 plus the
 * `library_harvest` job kind): `GET /v1/library/harvest/runs`, `GET .../runs/{id}/candidates`,
 * `POST .../candidates/{id}/pdf` (multipart) and `POST .../candidates/{id}/dismiss`. A run is a
 * `library_harvest` job submitted through `POST /v1/jobs`; its id is the run id. `:local` only: on ALS
 * the routes answer 404 (the Library tab itself is hidden there).
 */
import { jsonBody, v1Json, v1Url } from './apiHttp';
import type { JobRef } from './extractionApi';
import type { LibraryPaper } from './libraryApi';

/** Job kind of a harvest run. */
export const HARVEST_JOB_KIND = 'library_harvest';

/** Paper search sources a run can use. */
export type HarvestSource = 'arxiv' | 'openalex';

/** Every source, in display order, with its label. */
export const HARVEST_SOURCES: ReadonlyArray<{ id: HarvestSource; label: string }> = [
  { id: 'arxiv', label: 'arXiv' },
  { id: 'openalex', label: 'OpenAlex' },
];

/** Smallest and largest accepted `max_downloads` and the default. */
export const MAX_DOWNLOADS_MIN = 1;
export const MAX_DOWNLOADS_MAX = 200;
export const MAX_DOWNLOADS_DEFAULT = 20;

/** Longest accepted topic. */
export const TOPIC_MAX_CHARS = 200;

/** Outcome of one candidate: see the V77 row of the API README. */
export type HarvestStatus =
  | 'found'
  | 'downloading'
  | 'downloaded'
  | 'in_library'
  | 'paywalled'
  | 'filed'
  | 'failed'
  | 'skipped'
  | 'dismissed';

/** Generic topic relevance tier: A and B are downloaded, C never. */
export type HarvestTier = 'A' | 'B' | 'C';

/** Per-status candidate counts of a run (`total` is their sum). */
export interface HarvestCounts {
  total: number;
  /** Absent on a server that lists papers only once they are settled. */
  found?: number;
  downloading?: number;
  downloaded: number;
  in_library: number;
  paywalled: number;
  filed: number;
  failed: number;
  skipped: number;
  /** Absent on a server that does not count dismissed candidates separately. */
  dismissed?: number;
}

/** One harvest run (`GET /v1/library/harvest/runs`). */
export interface HarvestRun {
  /** The `library_harvest` job id. */
  id: string;
  topic: string;
  category: string;
  /** Schema label recorded with the run; `null` when none was chosen. */
  schema: string | null;
  sources: string[];
  max_downloads: number;
  created_at: string;
  counts: HarvestCounts;
}

/** `GET /v1/library/harvest/runs`. */
export interface HarvestRunsResponse {
  runs: HarvestRun[];
}

/** One paper the search found and what happened to it. */
export interface HarvestCandidate {
  id: string;
  title: string;
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  /** Search source that found it (`arxiv` or `openalex`). */
  source: string;
  tier: HarvestTier;
  score: number;
  status: HarvestStatus;
  /** DOI or arXiv page, for the user's own browser (never fetched by the server for paywalled papers). */
  landing_url: string | null;
  pdf_url: string | null;
  /** Library path once the PDF is filed. */
  relpath: string | null;
  /** Short reason (why it failed, was skipped, ...). */
  detail: string | null;
}

/** `GET /v1/library/harvest/runs/{run_id}/candidates`. */
export interface HarvestCandidatesResponse {
  candidates: HarvestCandidate[];
}

/** `POST /v1/library/harvest/candidates/{id}/pdf`: the filed candidate, its paper and the upload outcome. */
export interface HarvestFileResult {
  candidate: HarvestCandidate;
  paper: LibraryPaper;
  duplicate: boolean;
  manifest?: { updated: boolean; path: string; detail: string };
}

/** `POST /v1/library/harvest/candidates/{id}/dismiss`. */
export interface HarvestDismissResult {
  candidate: HarvestCandidate;
}

/**
 * `params` of a `library_harvest` job. `schema` only labels the run. `nonce` makes a deliberate re-run a
 * new job (job identity is idempotent on identical inputs); it rides in `inputs.params`.
 */
export interface StartHarvestRequest {
  topic: string;
  /** Library folder the papers are filed under (created on first filing). */
  category: string;
  schema?: string;
  sources: HarvestSource[];
  max_downloads: number;
  per_query?: number;
  nonce?: string;
}

/** Statuses a person still has to act on. */
export const PAYWALLED_STATUS: HarvestStatus = 'paywalled';

/** Submit a `library_harvest` job. */
export function startHarvest(request: StartHarvestRequest, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<JobRef>(
    v1Url('/v1/jobs'),
    jsonBody('POST', { kind: HARVEST_JOB_KIND, inputs: { params: request } }),
    fetcher,
  );
}

/** Past harvest runs, newest first. */
export function fetchHarvestRuns(fetcher: typeof fetch = fetch): Promise<HarvestRunsResponse> {
  return v1Json<HarvestRunsResponse>(v1Url('/v1/library/harvest/runs'), {}, fetcher);
}

/** The candidates of one run. */
export function fetchHarvestCandidates(
  runId: string,
  fetcher: typeof fetch = fetch,
): Promise<HarvestCandidatesResponse> {
  return v1Json<HarvestCandidatesResponse>(
    v1Url(`/v1/library/harvest/runs/${encodeURIComponent(runId)}/candidates`),
    {},
    fetcher,
  );
}

/** File a PDF the user saved for a paywalled candidate (multipart field `file`; 409 unless it is paywalled). */
export function fileHarvestPdf(
  candidateId: string,
  file: File,
  fetcher: typeof fetch = fetch,
): Promise<HarvestFileResult> {
  const form = new FormData();
  form.append('file', file, file.name);
  return v1Json<HarvestFileResult>(
    v1Url(`/v1/library/harvest/candidates/${encodeURIComponent(candidateId)}/pdf`),
    { method: 'POST', body: form },
    fetcher,
  );
}

/** Dismiss a paywalled candidate (the user will not supply it). */
export function dismissHarvestCandidate(
  candidateId: string,
  fetcher: typeof fetch = fetch,
): Promise<HarvestDismissResult> {
  return v1Json<HarvestDismissResult>(
    v1Url(`/v1/library/harvest/candidates/${encodeURIComponent(candidateId)}/dismiss`),
    { method: 'POST' },
    fetcher,
  );
}

/** True when `url` is an http(s) link safe to open in the user's browser. */
export function isOpenableUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const protocol = new URL(url).protocol;
    return protocol === 'https:' || protocol === 'http:';
  } catch {
    return false;
  }
}

/** Validation of the harvest form: the first problem, or `null` when the request is acceptable. */
export function harvestFormProblem(form: {
  topic: string;
  category: string | null;
  sources: readonly HarvestSource[];
  maxDownloads: number;
}): string | null {
  const topic = form.topic.trim();
  if (topic === '') return 'Enter a topic.';
  if (topic.length > TOPIC_MAX_CHARS) return `The topic is longer than ${TOPIC_MAX_CHARS} characters.`;
  if (form.category === null || form.category === '') return 'Choose a category.';
  if (form.sources.length === 0) return 'Choose at least one source.';
  if (!Number.isInteger(form.maxDownloads) || form.maxDownloads < MAX_DOWNLOADS_MIN
    || form.maxDownloads > MAX_DOWNLOADS_MAX) {
    return `Max downloads must be a whole number from ${MAX_DOWNLOADS_MIN} to ${MAX_DOWNLOADS_MAX}.`;
  }
  return null;
}
