/**
 * Capabilities client (T18D, owner decision Q13): `GET /v1/capabilities` tells the UI what the
 * caller may do so Edit/Save are hidden for ALS non-admins. Hiding is cosmetic; the server's 403
 * stays the backstop, so every uncertain state keeps the current (shown) behaviour.
 */
import { v1Json, v1Url } from './apiHttp';

/** Caller role reported by the server. */
export type CapabilityRole = 'local_operator' | 'admin' | 'user';

/** Parsed `GET /v1/capabilities` body. Unknown or malformed fields become `null`. */
export interface Capabilities {
  role: CapabilityRole | null;
  profile: string | null;
  can_edit: boolean | null;
  can_save: boolean | null;
  can_upload: boolean | null;
  /** T19E: the caller holds `chat.ask`, the entry capability of the `GET /v1/operations/*` reads. */
  can_run_operations: boolean | null;
  /**
   * Pending Approvals (owner instruction 2026-11-09): the entry check of `GET /v1/approvals/pending` (`graph.publish`
   * and, on ALS, a verified admin). The sidebar entry shows only on an explicit `true`.
   */
  can_approve: boolean | null;
}

const ROLES: ReadonlySet<string> = new Set(['local_operator', 'admin', 'user']);

/** Parse a capabilities body defensively: only a real boolean counts, anything else is `null`. */
export function parseCapabilities(raw: unknown): Capabilities {
  const body = raw !== null && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const flag = (value: unknown): boolean | null => (typeof value === 'boolean' ? value : null);
  return {
    role: typeof body.role === 'string' && ROLES.has(body.role) ? (body.role as CapabilityRole) : null,
    profile: typeof body.profile === 'string' ? body.profile : null,
    can_edit: flag(body.can_edit),
    can_save: flag(body.can_save),
    can_upload: flag(body.can_upload),
    can_run_operations: flag(body.can_run_operations),
    can_approve: flag(body.can_approve),
  };
}

/**
 * Whether the Operations tab shows. Unlike the Edit gate, an unknown answer hides it: the tab has nothing to
 * show to a caller the server has not confirmed (older server without the field, 401, network error).
 */
export function canRunOperations(caps: Capabilities | null | undefined): boolean {
  return caps?.can_run_operations === true;
}

/**
 * Whether the Pending Approvals page shows. As for Operations, only an explicit `true` shows it: an older server
 * without the field, a 401/403/404 or a network error hides it (the server's 403 on the route stays the backstop).
 */
export function canApprove(caps: Capabilities | null | undefined): boolean {
  return caps?.can_approve === true;
}

/** `GET /v1/capabilities`. Rejects with `ApiError` on 401/404/other failures (callers keep defaults). */
export async function fetchCapabilities(fetcher: typeof fetch = fetch): Promise<Capabilities> {
  return parseCapabilities(await v1Json<unknown>(v1Url('/v1/capabilities'), {}, fetcher));
}

/**
 * Whether the node card shows `Edit properties`. Hidden only for the JSON graph source or an
 * explicit `can_save: false`; unknown capabilities (loading, 401, 404, network error) show it.
 */
export function canEditNode(graphSource: string | undefined, caps: Capabilities | null | undefined): boolean {
  return graphSource !== 'json' && (caps?.can_save ?? true);
}
