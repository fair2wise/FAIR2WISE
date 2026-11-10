/**
 * Pending Approvals client (owner instruction 2026-11-09). Typed against `GET /v1/approvals/pending` (policy V82,
 * capability `graph.publish`; a verified admin on ALS): one aggregate of everything that waits for a human
 * decision. Every item carries the *existing* route that approves or rejects it (`approve`, `reject`: V18
 * publication approval bound to the shown `plan_sha256`, V08 cancel, V09 resume, V13 case review at the shown
 * revision, V81 merge apply), so this client never decides anything through a route of its own. Item paths are
 * relative to the API base (`v1/...`) because the server's response projector makes absolute paths opaque.
 */
import { ApiError, v1Json, v1Url } from './apiHttp';

export const PENDING_APPROVALS_PATH = '/v1/approvals/pending';

/** Item kinds the server emits today; unknown kinds are kept and listed last. */
export type ApprovalKind =
  | 'publication'
  | 'rollback'
  | 'retire'
  | 'purge'
  | 'paused_job'
  | 'review_case'
  | 'cost_control'
  | 'merge_apply'
  | string;

/** One existing route the UI calls for an item (method, path relative to the API base, JSON body or null). */
export interface ApprovalAction {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

/** The plan a reviewer approves (V17 `ChangePlan` summary). */
export interface ApprovalPlan {
  plan_id: string | null;
  plan_sha256: string | null;
  snapshot_id: string | null;
  base_revision: string | null;
  creates: number | null;
  links: number | null;
  supersessions: number | null;
  affected: number | null;
}

/** One pending item; the per-kind fields are optional and `null` when absent. */
export interface ApprovalItem {
  kind: ApprovalKind;
  id: string;
  approve: ApprovalAction | null;
  reject: ApprovalAction | null;
  plan: ApprovalPlan | null;
  /** `merge_apply`: the dry run that makes the plan and its hash (the real apply names that hash). */
  plan_route: ApprovalAction | null;
  /** `cost_control`: routes that need a typed amount; shown, never sent blindly. */
  routes: ApprovalAction[];
  job_id: string | null;
  job_kind: string | null;
  corpus_scope: string[];
  target: string | null;
  snapshot_ref: string | null;
  reason: string | null;
  submitted_at: string | null;
  requested_by: string | null;
  pause_reason: string | null;
  case_id: string | null;
  corpus: string | null;
  case_type: string | null;
  route: string | null;
  priority: number | string | null;
  revision: string | null;
  updated: string | number | null;
  accepted_cases: number | null;
  case_ids: string[];
  budget_cap_usd: number | null;
  spend: Record<string, unknown> | null;
  enabled: boolean | null;
}

/** Parsed `GET /v1/approvals/pending` body. */
export interface PendingApprovals {
  items: ApprovalItem[];
  counts: Record<string, number>;
  total: number;
  includes: Record<string, boolean>;
  generated_at: string | null;
}

/** Display order and labels of the groups. */
export const KIND_LABELS: ReadonlyArray<{ kind: ApprovalKind; label: string; help: string }> = [
  { kind: 'publication', label: 'Publication plans', help: 'A frozen snapshot planned against a target; approving imports exactly the shown plan.' },
  { kind: 'rollback', label: 'Rollback plans', help: 'A replacement plan back to a previous version; approving revives that version.' },
  { kind: 'retire', label: 'Corpus removal plans', help: 'Retire every record of a corpus; approving applies the shown plan.' },
  { kind: 'purge', label: 'Purge plans', help: 'Delete the retired records of a corpus; approving applies the shown plan.' },
  { kind: 'paused_job', label: 'Jobs paused on budget', help: 'A job stopped at its cost budget; resume it or cancel it.' },
  { kind: 'review_case', label: 'Curation cases awaiting review', help: 'Cases routed to a person; accept or reject at the revision shown (details in the review routes).' },
  { kind: 'cost_control', label: 'Remote-cost cap exceeded', help: 'Spend is above the configured cap; raising it needs a typed amount.' },
  { kind: 'merge_apply', label: 'Accepted merges to apply', help: 'Accepted canonicalization proposals of a corpus; plan first, then approve the planned hash.' },
];

const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/** Coerce an untrusted action object; anything without a string method and path is `null`. */
export function normalizeAction(raw: unknown): ApprovalAction | null {
  const body = record(raw);
  if (!body || typeof body.method !== 'string' || typeof body.path !== 'string' || !body.path) return null;
  return { method: body.method.toUpperCase(), path: body.path, body: record(body.body) };
}

function normalizePlan(raw: unknown): ApprovalPlan | null {
  const plan = record(raw);
  if (!plan) return null;
  return {
    plan_id: text(plan.plan_id), plan_sha256: text(plan.plan_sha256), snapshot_id: text(plan.snapshot_id),
    base_revision: text(plan.base_revision), creates: num(plan.creates), links: num(plan.links),
    supersessions: num(plan.supersessions), affected: num(plan.affected),
  };
}

/** Coerce one untrusted item; an item without a kind and id is dropped by the caller. */
export function normalizeItem(raw: unknown): ApprovalItem | null {
  const body = record(raw);
  if (!body || typeof body.kind !== 'string' || typeof body.id !== 'string' || !body.id) return null;
  const priority = typeof body.priority === 'number' || typeof body.priority === 'string' ? body.priority : null;
  const updated = typeof body.updated === 'number' || typeof body.updated === 'string' ? body.updated : null;
  return {
    kind: body.kind, id: body.id,
    approve: normalizeAction(body.approve), reject: normalizeAction(body.reject), plan: normalizePlan(body.plan),
    plan_route: normalizeAction(body.plan_route),
    routes: Array.isArray(body.routes) ? body.routes.map(normalizeAction).filter((a): a is ApprovalAction => a !== null) : [],
    job_id: text(body.job_id), job_kind: text(body.job_kind), corpus_scope: strings(body.corpus_scope),
    target: text(body.target), snapshot_ref: text(body.snapshot_ref), reason: text(body.reason),
    submitted_at: text(body.submitted_at), requested_by: text(body.requested_by), pause_reason: text(body.pause_reason),
    case_id: text(body.case_id), corpus: text(body.corpus), case_type: text(body.case_type), route: text(body.route),
    priority, revision: text(body.revision), updated, accepted_cases: num(body.accepted_cases),
    case_ids: strings(body.case_ids), budget_cap_usd: num(body.budget_cap_usd), spend: record(body.spend),
    enabled: typeof body.enabled === 'boolean' ? body.enabled : null,
  };
}

/** Coerce the untrusted listing body. */
export function normalizePending(raw: unknown): PendingApprovals {
  const body = record(raw) ?? {};
  const items = Array.isArray(body.items) ? body.items.map(normalizeItem).filter((i): i is ApprovalItem => i !== null) : [];
  const counts = Object.fromEntries(Object.entries(record(body.counts) ?? {}).filter((e): e is [string, number] => typeof e[1] === 'number'));
  const includes = Object.fromEntries(Object.entries(record(body.includes) ?? {}).filter((e): e is [string, boolean] => typeof e[1] === 'boolean'));
  return { items, counts, total: num(body.total) ?? items.length, includes, generated_at: text(body.generated_at) };
}

/** Items grouped in `KIND_LABELS` order; kinds the labels do not know come last, labelled by their name. */
export function groupByKind(items: ApprovalItem[]): Array<{ kind: ApprovalKind; label: string; help: string; items: ApprovalItem[] }> {
  const known = KIND_LABELS.map(meta => ({ ...meta, items: items.filter(item => item.kind === meta.kind) }));
  const unknownKinds = [...new Set(items.map(item => item.kind).filter(kind => !KIND_LABELS.some(meta => meta.kind === kind)))];
  const unknown = unknownKinds.map(kind => ({ kind, label: kind, help: '', items: items.filter(item => item.kind === kind) }));
  return [...known, ...unknown].filter(group => group.items.length > 0);
}

/** `GET /v1/approvals/pending`. Rejects with `ApiError` (401/403 refused, 404 older server). */
export async function fetchPendingApprovals(fetcher: typeof fetch = fetch): Promise<PendingApprovals> {
  return normalizePending(await v1Json<unknown>(v1Url(PENDING_APPROVALS_PATH), {}, fetcher));
}

/** Absolute URL of an item action: the API base plus the relative path the server gave (never built from ids here). */
export function actionUrl(action: ApprovalAction): string {
  return v1Url(`/${action.path.replace(/^\/+/, '')}`);
}

/**
 * Send one item action exactly as the server described it, with `override` merged into its body (the
 * `plan_sha256` of a merge dry run, a review `reason`). Rejects with `ApiError` carrying the typed `code`.
 */
export async function performApprovalAction(
  action: ApprovalAction,
  override: Record<string, unknown> = {},
  fetcher: typeof fetch = fetch,
): Promise<unknown> {
  const body = action.body === null && Object.keys(override).length === 0 ? null : { ...(action.body ?? {}), ...override };
  const init: RequestInit = body === null
    ? { method: action.method }
    : { method: action.method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  return v1Json<unknown>(actionUrl(action), init, fetcher);
}

/** Run a `merge_apply` item's dry run and return the planned hash (`null` when nothing is planned). */
export async function planMergeApply(item: ApprovalItem, fetcher: typeof fetch = fetch): Promise<string | null> {
  if (!item.plan_route) return null;
  const answer = record(await performApprovalAction(item.plan_route, {}, fetcher));
  const report = record(answer?.report);
  return text(report?.plan_sha256);
}

/** Message for a refused or failed action, naming the typed body (409 `stale_approval`, 403, 501). */
export function approvalErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const code = error.code ? ` (${error.code}${error.reason ? `: ${error.reason}` : ''})` : '';
    if (error.status === 409 && error.code === 'stale_approval') {
      return `The plan changed since it was shown${code}. Nothing was approved; the queue was refreshed, review the current plan.`;
    }
    if (error.status === 409) return `Conflict${code}: ${error.message}`;
    if (error.status === 403) return `Not permitted${code}: ${error.message}`;
    if (error.status === 401) return 'Sign in required.';
    if (error.status === 501) return `Not supported on this deployment${code}: ${error.message}`;
    if (error.status === 404) return `Not found${code}: ${error.message}`;
    return `${error.message}${code}`;
  }
  return error instanceof Error ? error.message : String(error);
}

/** A hash cut for display (the dialog shows the full value). */
export function shortHash(sha: string | null): string {
  return sha ? `${sha.slice(0, 12)}...${sha.slice(-6)}` : '-';
}
