/**
 * Publish to Tiled client (owner decision 2026-11-10). Typed against `POST /v1/library/kgs/{name}/publish` (policy V83,
 * `:local` only): `confirm: false` is a preview that writes nothing, `confirm: true` submits the `library_publish` job
 * (export, adopt, stage). The job is followed on `GET /v1/jobs/{id}/view`, whose `library_publish` block names the
 * adopted release and the staged plan. Nothing here approves anything: the plan is approved on Pending Approvals with
 * the existing route.
 */
import { jsonBody, v1Json, v1Url } from './apiHttp';

/** One reason a publish would be (or was) refused. */
export interface PublishProblem {
  reason: string;
  code: string;
  message: string;
}

/** `POST .../publish` with `confirm: false`. */
export interface PublishPreview {
  confirmed: false;
  name: string;
  corpus: string;
  snapshot_id: string;
  snapshot_ref: string;
  target: string | null;
  release_file: string;
  /** The job of this exact bundle state when it was published before (unchanged bundle, no new version). */
  previous: { job_id: string; state: string } | null;
  review_page: string;
  problems: PublishProblem[];
}

/** `POST .../publish` with `confirm: true`. */
export interface PublishStarted extends Omit<PublishPreview, 'confirmed'> {
  confirmed: true;
  job_id: string;
  kind: string;
  state: string;
  /** The job existed already (identical inputs). */
  reused: boolean;
}

/** The additive `library_publish` block of `GET /v1/jobs/{id}/view`. */
export interface PublishJobDetail {
  bundle: string | null;
  corpus: string | null;
  snapshot_id: string | null;
  snapshot_ref: string | null;
  target: string | null;
  stages: Record<string, string>;
  publication_job_id: string | null;
  /** `staged` once the plan waits for approval (job `awaiting_review`). */
  publication_state: string | null;
  plan: { plan_id: string | null; plan_sha256: string | null; creates: number | null; links: number | null; supersessions: number | null } | null;
  review_page: string;
  failure: { stage: string; reason: string | null; message: string; code: string | null } | null;
}

/** `GET /v1/jobs/{id}/view` of a `library_publish` job. */
export interface PublishJobView {
  job: { job_id: string; state: string };
  ui_state: string;
  library_publish: PublishJobDetail | null;
}

/** Job states after which the job itself will not change. */
const ENDED = new Set(['succeeded', 'failed', 'cancelled']);

/** True when the view needs no more polling: failed, or finished with the plan visible. */
export function publishSettled(view: PublishJobView | undefined): boolean {
  if (!view) return false;
  const detail = view.library_publish;
  if (detail?.failure) return true;
  const state = view.job?.state;
  if (state === 'failed' || state === 'cancelled') return true;
  return ENDED.has(state) && Boolean(detail?.plan?.plan_sha256);
}

/** Preview what publishing `name` would do now. Writes nothing. */
export function previewPublish(name: string, again = false, fetcher: typeof fetch = fetch): Promise<PublishPreview> {
  return v1Json<PublishPreview>(
    v1Url(`/v1/library/kgs/${encodeURIComponent(name)}/publish`),
    jsonBody('POST', { confirm: false, again }),
    fetcher,
  );
}

/** Submit the publish job (`confirm: true`). */
export function startPublish(name: string, again = false, fetcher: typeof fetch = fetch): Promise<PublishStarted> {
  return v1Json<PublishStarted>(
    v1Url(`/v1/library/kgs/${encodeURIComponent(name)}/publish`),
    jsonBody('POST', { confirm: true, again }),
    fetcher,
  );
}

/** The job view of a publish job. */
export function fetchPublishView(jobId: string, fetcher: typeof fetch = fetch): Promise<PublishJobView> {
  return v1Json<PublishJobView>(v1Url(`/v1/jobs/${encodeURIComponent(jobId)}/view`), {}, fetcher);
}

/** Text for a typed refusal `reason` of the route or of a failed stage. */
export function publishReasonText(reason: string | null, message: string): string {
  switch (reason) {
    case 'disclosure_refused':
      return `Refused by the disclosure check, nothing was adopted or staged. ${stripReason(message)}`;
    case 'no_commit_label':
      return `This image has no commit label. ${stripReason(message)}`;
    case 'nothing_to_publish':
      return `Nothing to publish. ${stripReason(message)}`;
    case 'plan_pending':
      return `A plan for this corpus is already pending. ${stripReason(message)}`;
    default:
      return message;
  }
}

function stripReason(message: string): string {
  return message.replace(/^[a-z_]+: /, '');
}
