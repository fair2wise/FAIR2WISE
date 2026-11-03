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
  };
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
