import { describe, expect, it, vi } from 'vitest';
import { formatMetric, normalizeResult, operationUrl, runOperation } from './operationsApi';

const REPORT = {
  schema_version: 'f2w-operation-report/1', operation: 'kg_compute_statistics', operation_version: '1.0.0',
  method: 'kg-statistics/1', population: 'all node and edge records of graph rsoxs_v2',
  counting_units: { entities: 'distinct node ids', triples: 'distinct (s,p,o) triples' },
  coverage: { exactness: 'exact', records_scanned: 12, reasons: [] },
  metrics: { entities: 7, triples: 5, legacy: { avg: 1.5 } }, preview: {}, display_truncated: false,
  excluded: {}, warnings: [], completeness: 'complete',
};
const RESULT = { tool: 'kg_compute_statistics', status: 'ok', data: REPORT, warnings: [], artifact_refs: ['sha256:abc'] };

describe('operationUrl', () => {
  it('addresses the five routes and repeats list parameters', () => {
    expect(operationUrl({ operation: 'kg_compute_statistics', graph_id: 'rsoxs_v2' })).toMatch(/\/v1\/operations\/statistics\?graph_id=rsoxs_v2$/);
    expect(operationUrl({ operation: 'kg_compare_snapshots', graph_ids: ['a', 'b'] })).toMatch(/\/compare\?graph_ids=a&graph_ids=b$/);
    expect(operationUrl({ operation: 'kg_paper_subgraph', graph_id: 'g', paper: '10.1/x', max_nodes: 5 }))
      .toMatch(/\/paper-subgraph\?graph_id=g&paper=10.1%2Fx&max_nodes=5$/);
    expect(operationUrl({ operation: 'kg_recent_entities', graph_id: 'g', since: '', limit: 3 })).toMatch(/\/recent\?graph_id=g&limit=3$/);
    expect(operationUrl({ operation: 'curate_audit_corpus', corpus: 'c', sections: ['summary', 'stubs'] }))
      .toMatch(/\/corpus-audit\?corpus=c&sections=summary&sections=stubs$/);
    expect(operationUrl({ operation: 'curate_audit_corpus', corpus: 'c', sections: [] })).toMatch(/\/corpus-audit\?corpus=c$/);
  });
});

describe('normalizeResult', () => {
  it('keeps the typed blocks and the unknown report fields', () => {
    const result = normalizeResult(RESULT);
    expect(result.status).toBe('ok');
    expect(result.data?.metrics).toEqual(REPORT.metrics);
    expect(result.data?.counting_units.entities).toBe('distinct node ids');
    expect(result.data?.coverage).toMatchObject({ exactness: 'exact', records_scanned: 12, reasons: [] });
    expect(result.data?.schema_version).toBe('f2w-operation-report/1');
    expect(result.artifact_refs).toEqual(['sha256:abc']);
  });
  it('turns a malformed body into empty blocks, never undefined', () => {
    const result = normalizeResult({ status: 'partial', data: { metrics: 'nope', coverage: null, warnings: 'x' } });
    expect(result.status).toBe('partial');
    expect(result.data).toMatchObject({ metrics: {}, coverage: null, warnings: [], counting_units: {}, preview: {} });
    expect(normalizeResult(null)).toEqual({ tool: null, status: 'error', data: null, warnings: [], artifact_refs: [] });
  });
});

describe('runOperation', () => {
  it('sends the read with same-origin credentials and parses the ToolResult', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(RESULT), { status: 200 }));
    const result = await runOperation({ operation: 'kg_compute_statistics', graph_id: 'rsoxs_v2' }, fetcher as unknown as typeof fetch);
    expect(result.data?.metrics.entities).toBe(7);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/operations\/statistics\?graph_id=rsoxs_v2$/);
    expect(init.credentials).toBe('same-origin');
  });
  it.each([401, 403, 404, 501])('rejects with the status and server message on HTTP %i', async status => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ code: 'x', message: `refused ${status}` }), { status }));
    await expect(runOperation({ operation: 'kg_compute_statistics', graph_id: 'g' }, fetcher as unknown as typeof fetch))
      .rejects.toMatchObject({ status, message: expect.stringContaining(`refused ${status}`) });
  });
});

describe('formatMetric', () => {
  it('formats numbers, scalars and structures', () => {
    expect(formatMetric(12345)).toBe((12345).toLocaleString());
    expect(formatMetric(1.23456)).toBe('1.235');
    expect(formatMetric(null)).toBe('-');
    expect(formatMetric(true)).toBe('true');
    expect(formatMetric({ a: 1 })).toBe('{"a":1}');
    expect(formatMetric('x'.repeat(200).split('')).length).toBeLessThanOrEqual(120);
  });
});
