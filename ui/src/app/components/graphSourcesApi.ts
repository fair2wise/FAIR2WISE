/**
 * Client of `GET /v1/graph/sources` (policy V80): the JSON KGs and Tiled graphs the server offers to the
 * KG viewer. Ids are opaque: the UI only echoes one back as the `source` query parameter and never builds
 * a path or URL from it. A 401/403/404/405 means "this server or caller has no such route" and hides the
 * selector (`HIDDEN_STATUSES`).
 */
import { v1Json, v1Url } from './apiHttp';

export type GraphSourceKind = 'json' | 'tiled';

/** `ok`, `unavailable` (Tiled is offered but unreachable) or `not_offered` (this deployment has none). */
export type TiledStatus = 'ok' | 'unavailable' | 'not_offered';

export interface GraphSourceInfo {
  id: string;
  kind: GraphSourceKind;
  label: string;
  read_only: boolean;
  available: boolean;
  node_count: number | null;
  edge_count: number | null;
  revision: string | null;
  detail: string | null;
}

export interface GraphSourceListing {
  sources: GraphSourceInfo[];
  tiled: { status: TiledStatus; detail: string | null };
}

const TILED_STATUSES: ReadonlySet<string> = new Set(['ok', 'unavailable', 'not_offered']);

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/** Parse a listing defensively: rows without a valid id, kind or label are dropped; read-only defaults to true. */
export function parseGraphSourceListing(raw: unknown): GraphSourceListing {
  const body = raw !== null && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const rows = Array.isArray(body.sources) ? body.sources : [];
  const sources: GraphSourceInfo[] = [];
  for (const row of rows) {
    if (row === null || typeof row !== 'object') continue;
    const item = row as Record<string, unknown>;
    const id = text(item.id);
    if (!id || (item.kind !== 'json' && item.kind !== 'tiled')) continue;
    sources.push({
      id,
      kind: item.kind,
      label: text(item.label) ?? id,
      read_only: item.read_only !== false,
      available: item.available !== false,
      node_count: count(item.node_count),
      edge_count: count(item.edge_count),
      revision: text(item.revision),
      detail: text(item.detail),
    });
  }
  const tiled = body.tiled !== null && typeof body.tiled === 'object' ? (body.tiled as Record<string, unknown>) : {};
  const status = typeof tiled.status === 'string' && TILED_STATUSES.has(tiled.status)
    ? (tiled.status as TiledStatus)
    : 'not_offered';
  return { sources, tiled: { status, detail: text(tiled.detail) } };
}

/** `GET /v1/graph/sources`. Rejects with `ApiError` (callers hide the selector on `HIDDEN_STATUSES`). */
export async function fetchGraphSources(fetcher: typeof fetch = fetch): Promise<GraphSourceListing> {
  return parseGraphSourceListing(await v1Json<unknown>(v1Url('/v1/graph/sources'), {}, fetcher));
}
