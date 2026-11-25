/**
 * Harvester API client (A8, T46). Typed against the T45 contract (policy rows V76-V79 plus the
 * `library_harvest` job kind): `GET /v1/library/harvest/runs`, `GET .../runs/{id}/candidates`,
 * `POST .../candidates/{id}/pdf` (multipart, one file per request; policy V78 with the verdict contract),
 * `POST .../runs/{id}/pdf` (V88, the batch drop), `POST .../candidates/{id}/undo` (V87) and
 * `POST .../candidates/{id}/dismiss`. A run is a
 * `library_harvest` job submitted through `POST /v1/jobs`; its id is the run id. `:local` only: on ALS
 * the routes answer 404 (the Library tab itself is hidden there).
 */
import { jsonBody, v1Json, v1Url } from './apiHttp';
import { normalizeJob, type JobRef } from './extractionApi';
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
  | 'blocked'
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
  /** Genuinely not open access (OpenAlex lists no open-access copy). */
  paywalled: number;
  /** Open access, but the deployment's network policy refused the host (the candidate's detail names it). Absent on an older server. */
  blocked?: number;
  filed: number;
  failed: number;
  skipped: number;
  /** Absent on a server that does not count dismissed candidates separately. */
  dismissed?: number;
}

/** What one source answered for a run; `error` and `rate_limited` mean it could not be searched. */
export interface HarvestSourceStatus {
  state: 'ok' | 'error' | 'rate_limited';
  /** Results the source returned. */
  returned: number;
  /** Plain-words reason (a failure) or note (`ok` with "reduced rate, no API key"); never a URL or a key. */
  message: string;
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
  /** Per source (`arxiv`, `openalex`); absent on a server from before it was recorded, empty for an old run. */
  source_status?: Record<string, HarvestSourceStatus>;
}

/** `GET /v1/library/harvest/runs`. */
export interface HarvestRunsResponse {
  runs: HarvestRun[];
  /** Whether OpenAlex is searched with a key; `false` means unauthenticated at a reduced rate. Absent: unknown. */
  openalex_keyed?: boolean;
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
  /** Short reason (why it failed, was skipped, which host was blocked, ...). */
  detail: string | null;
  /** Whether the source calls the paper open access; `null`/absent when unknown. */
  oa?: boolean | null;
  /** The rule that found the free link, when one did. */
  link_rule?: string | null;
  /** How a PDF a person supplied was matched; `null`/absent for anything the harvester fetched itself. */
  provenance?: HarvestProvenance | null;
  /** Who supplied it (the acting principal's subject). */
  supplied_by?: string | null;
  /** For a confirmed mismatch: both DOIs and both titles. */
  match?: HarvestMismatchRecord | null;
  /**
   * The link the user's own browser opens ("Open at publisher"): the DOI URL or landing page, behind the
   * deployment's library proxy prefix when one is set. The server only builds it; absent on an older server.
   */
  open_url?: string | null;
}

/** `provenance` of a candidate whose PDF a person supplied. */
export type HarvestProvenance = 'user_supplied' | 'user_supplied_confirmed' | 'user_supplied_mismatch_confirmed';

/** Recorded with a confirmed mismatch. */
export interface HarvestMismatchRecord {
  candidate_doi: string | null;
  candidate_title: string;
  found_dois: string[];
  found_title: string;
}

/** Statuses of a paper that still needs a PDF by hand (`MANUAL_STATUSES` on the server). */
export const MANUAL_STATUSES: readonly HarvestStatus[] = ['paywalled', 'blocked', 'failed'];

/** True when the candidate still needs a PDF by hand. */
export function needsPdf(candidate: Pick<HarvestCandidate, 'status'>): boolean {
  return MANUAL_STATUSES.includes(candidate.status);
}

/** How a supplied PDF was matched to a paper. */
export type MatchKind = 'strong' | 'weak' | 'mismatch';

/** The matcher's verdict: what the PDF carries against the paper (never the PDF text). */
export interface HarvestVerdict {
  kind: MatchKind;
  /** Plain-words reason. */
  reason: string;
  title_similarity: number;
  found_dois: string[];
  found_title: string;
}

/** The paper a PDF was dropped on or matched to. */
export interface HarvestTarget {
  id: string;
  title: string;
  doi: string | null;
}

/** `filed: true`: the PDF was filed (a strong match, a confirmed one, or `duplicate` of a file already in the library). */
export interface HarvestFileResult {
  filed: true;
  candidate: HarvestCandidate;
  paper: LibraryPaper;
  duplicate: boolean;
  manifest?: { updated: boolean; path: string; detail: string };
  verdict: HarvestVerdict;
}

/** `filed: false, needs_confirmation: true`: nothing was stored; the user decides. */
export interface HarvestNeedsConfirmation {
  filed: false;
  needs_confirmation: true;
  candidate: HarvestTarget;
  verdict: HarvestVerdict;
  /** The PDF's DOI belongs to this other open candidate of the run (`to=<id>` files it there). */
  other_candidate: (HarvestTarget & { status: HarvestStatus }) | null;
}

/** `filed: false, unmatched: true`: the batch drop found no paper for the file; nothing was stored. */
export interface HarvestUnmatched {
  filed: false;
  unmatched: true;
}

/** Answer of the candidate route (V78). */
export type HarvestSupplyResult = HarvestFileResult | HarvestNeedsConfirmation;

/** Answer of the batch route (V88). */
export type HarvestRunDropResult = HarvestFileResult | HarvestNeedsConfirmation | HarvestUnmatched;

/** `POST /v1/library/harvest/candidates/{id}/undo`. */
export interface HarvestUndoResult {
  candidate: HarvestCandidate;
  removed: { file: boolean; manifest: boolean };
}

/** Most files one multi-file drop accepts (each is sent as its own request). */
export const MAX_FILES_PER_DROP = 20;

/** `GET /v1/library/harvest/runs/{run_id}/candidates`. */
export interface HarvestCandidatesResponse {
  candidates: HarvestCandidate[];
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

/** Submit a `library_harvest` job. */
export function startHarvest(request: StartHarvestRequest, fetcher: typeof fetch = fetch): Promise<JobRef> {
  return v1Json<unknown>(
    v1Url('/v1/jobs'),
    jsonBody('POST', { kind: HARVEST_JOB_KIND, inputs: { params: request } }),
    fetcher,
  ).then(raw => {
    const { id, kind, state } = normalizeJob(raw);   // the server's handle names the id `job_id`
    return { id, kind, state };
  });
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

/** Options of {@link fileHarvestPdf}. */
export interface FileHarvestPdfOptions {
  /** The user confirmed a weak match or a mismatch. */
  confirm?: boolean;
  /** File it against this other open candidate of the same run instead. */
  to?: string;
}

/**
 * Offer a PDF the user saved for a candidate that needs one by hand (multipart field `file`, one file per request).
 * A strong match is filed at once; otherwise nothing is stored and the answer asks for confirmation. 409 unless the
 * candidate is paywalled, blocked or failed.
 */
export function fileHarvestPdf(
  candidateId: string,
  file: File,
  options: FileHarvestPdfOptions = {},
  fetcher: typeof fetch = fetch,
): Promise<HarvestSupplyResult> {
  const form = new FormData();
  form.append('file', file, file.name);
  const query = new URLSearchParams();
  if (options.confirm) query.set('confirm', 'true');
  if (options.to) query.set('to', options.to);
  const suffix = query.size > 0 ? `?${query.toString()}` : '';
  return v1Json<HarvestSupplyResult>(
    v1Url(`/v1/library/harvest/candidates/${encodeURIComponent(candidateId)}/pdf${suffix}`),
    { method: 'POST', body: form },
    fetcher,
  );
}

/** Drop one PDF on a run: it is matched against all open candidates (one file per request; nothing stored unless strong). */
export function fileHarvestRunPdf(
  runId: string,
  file: File,
  fetcher: typeof fetch = fetch,
): Promise<HarvestRunDropResult> {
  const form = new FormData();
  form.append('file', file, file.name);
  return v1Json<HarvestRunDropResult>(
    v1Url(`/v1/library/harvest/runs/${encodeURIComponent(runId)}/pdf`),
    { method: 'POST', body: form },
    fetcher,
  );
}

/** Reverse a filing made from a supplied PDF (file, manifest row, catalog row; the candidate returns to its earlier status). */
export function undoHarvestPdf(candidateId: string, fetcher: typeof fetch = fetch): Promise<HarvestUndoResult> {
  return v1Json<HarvestUndoResult>(
    v1Url(`/v1/library/harvest/candidates/${encodeURIComponent(candidateId)}/undo`),
    { method: 'POST' },
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
