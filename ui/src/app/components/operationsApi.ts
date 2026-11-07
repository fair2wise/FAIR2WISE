/**
 * Knowledge Operations client (T19E frontend closure). Typed against the five `GET /v1/operations/*`
 * reads (policies V25-V29, capability `chat.ask`): statistics, compare, paper-subgraph, recent and
 * corpus-audit. Each answers the bounded `ToolResult` the agent Tool returns (`status` `ok` or
 * `partial`, `data` the `OperationReport`). Nothing here submits a job, writes, or touches
 * `PUT /settings` or browser storage; graph ids come from `GET /v1/graph/sources` (`graph_id`),
 * never from a path the UI builds.
 */
import { v1Json, v1Url } from './apiHttp';

export const OPERATIONS_PATH = '/v1/operations';

/** The five inline operations; the value is the route segment. */
export const OPERATION_ROUTES = {
  kg_compute_statistics: 'statistics',
  kg_compare_snapshots: 'compare',
  kg_paper_subgraph: 'paper-subgraph',
  kg_recent_entities: 'recent',
  curate_audit_corpus: 'corpus-audit',
} as const;

export type OperationName = keyof typeof OPERATION_ROUTES;

/** Human labels, in display order. */
export const OPERATION_LABELS: ReadonlyArray<{ name: OperationName; label: string; help: string }> = [
  { name: 'kg_compute_statistics', label: 'Graph statistics', help: 'Structural statistics of one graph.' },
  { name: 'kg_compare_snapshots', label: 'Compare graphs', help: 'Growth across graph versions, in the given order (2 to 12).' },
  { name: 'kg_paper_subgraph', label: 'Paper subgraph', help: 'What one graph holds about one Work (DOI, arXiv id or paper key).' },
  { name: 'kg_recent_entities', label: 'Recent entities', help: 'Entities added to a live (Tiled) graph after a time.' },
  { name: 'curate_audit_corpus', label: 'Corpus audit', help: 'Read-only audit of one configured extracted-terms corpus.' },
];

/** `ToolResult.status` values the reads return (refusals arrive as HTTP errors). */
export type OperationStatus = 'ok' | 'partial' | 'empty' | string;

/** Coverage block of an `OperationReport`. */
export interface OperationCoverage {
  exactness: string | null;
  records_scanned: number | null;
  reasons: string[];
  [key: string]: unknown;
}

/** The bounded `OperationReport` (`ToolResult.data`); unknown fields are kept for the raw view. */
export interface OperationReport {
  operation: string | null;
  method: string | null;
  population: string | null;
  completeness: string | null;
  counting_units: Record<string, string>;
  coverage: OperationCoverage | null;
  metrics: Record<string, unknown>;
  preview: Record<string, unknown>;
  excluded: Record<string, string>;
  warnings: string[];
  display_truncated: boolean;
  [key: string]: unknown;
}

/** A `ToolResult` as the routes return it. */
export interface OperationResult {
  tool: string | null;
  status: OperationStatus;
  data: OperationReport | null;
  warnings: string[];
  artifact_refs: string[];
}

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null);
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const stringMap = (value: unknown): Record<string, string> =>
  Object.fromEntries(Object.entries(record(value)).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));

/** Coerce an untrusted report object; absent blocks become empty, never `undefined`. */
export function normalizeReport(raw: unknown): OperationReport | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const data = raw as Record<string, unknown>;
  const cov = data.coverage !== null && typeof data.coverage === 'object' ? record(data.coverage) : null;
  return {
    ...data,
    operation: text(data.operation),
    method: text(data.method),
    population: text(data.population),
    completeness: text(data.completeness),
    counting_units: stringMap(data.counting_units),
    coverage: cov
      ? {
          ...cov,
          exactness: text(cov.exactness),
          records_scanned: typeof cov.records_scanned === 'number' ? cov.records_scanned : null,
          reasons: strings(cov.reasons),
        }
      : null,
    metrics: record(data.metrics),
    preview: record(data.preview),
    excluded: stringMap(data.excluded),
    warnings: strings(data.warnings),
    display_truncated: data.display_truncated === true,
  };
}

/** Coerce an untrusted `ToolResult` body. */
export function normalizeResult(raw: unknown): OperationResult {
  const body = record(raw);
  return {
    tool: text(body.tool),
    status: typeof body.status === 'string' ? body.status : 'error',
    data: normalizeReport(body.data),
    warnings: strings(body.warnings),
    artifact_refs: strings(body.artifact_refs),
  };
}

/** Arguments of one read; the panel builds them, the route validates them. */
export type OperationArgs =
  | { operation: 'kg_compute_statistics'; graph_id: string }
  | { operation: 'kg_compare_snapshots'; graph_ids: string[] }
  | { operation: 'kg_paper_subgraph'; graph_id: string; paper: string; max_nodes?: number; max_edges?: number }
  | { operation: 'kg_recent_entities'; graph_id: string; since?: string; limit?: number }
  | { operation: 'curate_audit_corpus'; corpus: string; sections?: string[] };

/** The URL one read is sent to (exported for tests and the raw view). */
export function operationUrl(args: OperationArgs): string {
  const path = `${OPERATIONS_PATH}/${OPERATION_ROUTES[args.operation]}`;
  switch (args.operation) {
    case 'kg_compute_statistics':
      return v1Url(path, { graph_id: args.graph_id });
    case 'kg_compare_snapshots':
      return v1Url(path, { graph_ids: args.graph_ids });
    case 'kg_paper_subgraph':
      return v1Url(path, { graph_id: args.graph_id, paper: args.paper, max_nodes: args.max_nodes, max_edges: args.max_edges });
    case 'kg_recent_entities':
      return v1Url(path, { graph_id: args.graph_id, since: args.since || undefined, limit: args.limit });
    case 'curate_audit_corpus':
      return v1Url(path, { corpus: args.corpus, sections: args.sections?.length ? args.sections : undefined });
  }
}

/** Run one inline read. Rejects with `ApiError` (401/403 refusal, 404 unknown graph, 501 unsupported, ...). */
export async function runOperation(args: OperationArgs, fetcher: typeof fetch = fetch): Promise<OperationResult> {
  return normalizeResult(await v1Json<unknown>(operationUrl(args), {}, fetcher));
}

/** Render a metric value for the table: numbers as-is, strings quoted, structures as compact JSON. */
export function formatMetric(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  if (typeof value === 'string' || typeof value === 'boolean') return String(value);
  const json = JSON.stringify(value);
  return json.length > 120 ? `${json.slice(0, 117)}...` : json;
}
