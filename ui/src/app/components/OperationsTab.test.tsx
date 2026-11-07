import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { buildArgs, graphOptionLabel, operableGraphs, operationErrorMessage, OperationsTab } from './OperationsTab';
import { ApiError } from './apiHttp';
import type { GraphSourceInfo } from './graphSourcesApi';

const source = (over: Partial<GraphSourceInfo>): GraphSourceInfo => ({
  id: 'json:x', kind: 'json', label: 'x', read_only: true, available: true, node_count: null, edge_count: null,
  revision: null, detail: null, graph_id: 'x', ...over,
});

const LISTING = {
  sources: [
    { id: 'json:matkg_rsoxs_v2', kind: 'json', label: 'matkg_rsoxs_v2.json', read_only: true, available: true,
      node_count: 7, edge_count: 5, revision: null, detail: null, graph_id: 'rsoxs_v2' },
    { id: 'json:matkg_rsoxs_prev', kind: 'json', label: 'matkg_rsoxs_prev.json', read_only: true, available: true,
      node_count: 6, edge_count: 4, revision: null, detail: null, graph_id: 'rsoxs_prev' },
    { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true,
      node_count: 268, edge_count: 126, revision: 'v1@abc', detail: null, graph_id: 'e2e_demo' },
  ],
  tiled: { status: 'ok', detail: null },
};

const STATS = {
  tool: 'kg_compute_statistics', status: 'ok', warnings: [], artifact_refs: [],
  data: {
    operation: 'kg_compute_statistics', method: 'kg-statistics/1', completeness: 'complete',
    population: 'all node and edge records of graph rsoxs_v2',
    counting_units: { entities: 'distinct node ids', triples: 'distinct (s,p,o) triples' },
    coverage: { exactness: 'exact', records_scanned: 12, reasons: [] },
    metrics: { entities: 7, triples: 5 }, preview: { sample: ['a'] }, excluded: {}, warnings: [], display_truncated: false,
  },
};

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { path: string; search: string }[];

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/graph/sources': { status: 200, body: LISTING },
    'GET /v1/operations/statistics': { status: 200, body: STATS },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    calls.push({ path: url.pathname, search: url.search });
    const hit = routes[`${method} ${url.pathname}`];
    return hit
      ? new Response(JSON.stringify(hit.body), { status: hit.status })
      : new Response(JSON.stringify({ code: 'not_found', message: 'no route' }), { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OperationsTab />
    </QueryClientProvider>,
  );
}

describe('OperationsTab helpers', () => {
  it('lists only sources with a graph_id, JSON first, deduplicated', () => {
    const graphs = operableGraphs([
      source({ id: 'tiled:a', kind: 'tiled', graph_id: 'a' }),
      source({ id: 'json:old', graph_id: null }),
      source({ id: 'json:a', graph_id: 'a' }),
      source({ id: 'json:b', graph_id: 'b' }),
    ]);
    expect(graphs.map(g => g.id)).toEqual(['json:a', 'json:b']);
    expect(graphOptionLabel(source({ label: 'matkg_a.json', graph_id: 'a' }))).toBe('matkg_a.json (a)');
    expect(graphOptionLabel(source({ label: 'a', graph_id: 'a' }))).toBe('a');
  });

  it('builds the request only when the form is complete', () => {
    const base = { operation: 'kg_compute_statistics' as const, graphId: '', compareIds: [], paper: '', maxNodes: '', maxEdges: '', since: '', limit: '', corpus: '', sections: '' };
    expect(buildArgs(base)).toEqual({ problem: 'Choose a graph.' });
    expect(buildArgs({ ...base, graphId: 'g' })).toEqual({ args: { operation: 'kg_compute_statistics', graph_id: 'g' } });
    expect(buildArgs({ ...base, operation: 'kg_compare_snapshots', compareIds: ['a'] })).toHaveProperty('problem');
    expect(buildArgs({ ...base, operation: 'kg_compare_snapshots', compareIds: ['a', 'b'] }))
      .toEqual({ args: { operation: 'kg_compare_snapshots', graph_ids: ['a', 'b'] } });
    expect(buildArgs({ ...base, operation: 'kg_paper_subgraph', graphId: 'g', paper: 'ab' })).toHaveProperty('problem');
    expect(buildArgs({ ...base, operation: 'kg_paper_subgraph', graphId: 'g', paper: ' 10.1/x ', maxNodes: '5', maxEdges: 'x' }))
      .toEqual({ args: { operation: 'kg_paper_subgraph', graph_id: 'g', paper: '10.1/x', max_nodes: 5, max_edges: undefined } });
    expect(buildArgs({ ...base, operation: 'curate_audit_corpus', corpus: ' c ', sections: 'summary, ,stubs' }))
      .toEqual({ args: { operation: 'curate_audit_corpus', corpus: 'c', sections: ['summary', 'stubs'] } });
  });

  it('explains 501 and 404 and passes other messages through', () => {
    expect(operationErrorMessage(new ApiError('recent needs a live graph', 501))).toMatch(/^Not supported for this graph: recent/);
    expect(operationErrorMessage(new ApiError("graph 'x' is not configured", 404))).toMatch(/^Not found: graph/);
    expect(operationErrorMessage(new ApiError('denied', 403))).toBe('denied');
    expect(operationErrorMessage(new Error('boom'))).toBe('boom');
  });
});

describe('OperationsTab', () => {
  it('runs statistics for the chosen graph and shows status, metrics with counting units and coverage', async () => {
    renderTab();
    const graph = await screen.findByLabelText('Graph');
    await waitFor(() => expect(screen.getByRole('option', { name: 'matkg_rsoxs_v2.json (rsoxs_v2)' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();
    await userEvent.selectOptions(graph, 'rsoxs_v2');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));

    expect(await screen.findByTestId('operation-status')).toHaveTextContent('ok');
    const table = screen.getByRole('table', { name: 'Operation metrics' });
    expect(table).toHaveTextContent('entities');
    expect(table).toHaveTextContent('distinct node ids');
    expect(screen.getByTestId('operation-coverage')).toHaveTextContent('complete, exact, 12 records scanned');
    expect(screen.getByText(/all node and edge records/)).toBeInTheDocument();
    const call = calls.find(c => c.path === '/v1/operations/statistics');
    expect(call?.search).toBe('?graph_id=rsoxs_v2');
    // no job was submitted and nothing was written
    expect(calls.every(c => c.path.startsWith('/v1/graph/sources') || c.path.startsWith('/v1/operations/'))).toBe(true);
  });

  it('keeps a partial result visibly partial and lists the warnings', async () => {
    routes['GET /v1/operations/statistics'].body = {
      ...STATS, status: 'partial', warnings: ['partial: the scan did not cover the whole population'],
      data: { ...STATS.data, completeness: 'partial', coverage: { exactness: 'lower_bound', records_scanned: 3, reasons: ['bound'] } },
    };
    renderTab();
    const graph = await screen.findByLabelText('Graph');
    await waitFor(() => expect(screen.getByRole('option', { name: 'matkg_rsoxs_v2.json (rsoxs_v2)' })).toBeInTheDocument());
    await userEvent.selectOptions(graph, 'rsoxs_v2');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));
    expect(await screen.findByTestId('operation-status')).toHaveTextContent('partial');
    expect(screen.getByRole('list', { name: 'Warnings' })).toHaveTextContent(/did not cover/);
    expect(screen.getByTestId('operation-coverage')).toHaveTextContent('(bound)');
  });

  it('sends compare with the graphs in order as repeated graph_ids', async () => {
    routes['GET /v1/operations/compare'] = { status: 200, body: { ...STATS, tool: 'kg_compare_snapshots' } };
    renderTab();
    await screen.findByLabelText('Graph');
    await userEvent.selectOptions(screen.getByLabelText('Operation'), 'kg_compare_snapshots');
    const multi = await screen.findByLabelText('Graphs (in order)');
    await waitFor(() => expect(screen.getByRole('option', { name: 'matkg_rsoxs_prev.json (rsoxs_prev)' })).toBeInTheDocument());
    await userEvent.selectOptions(multi, 'rsoxs_prev');
    await userEvent.selectOptions(multi, 'rsoxs_v2');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));
    await screen.findByTestId('operation-status');
    expect(calls.find(c => c.path === '/v1/operations/compare')?.search).toBe('?graph_ids=rsoxs_prev&graph_ids=rsoxs_v2');
  });

  it('shows the server refusal for recent on a JSON graph (501) without crashing', async () => {
    routes['GET /v1/operations/recent'] = { status: 501, body: { code: 'unsupported_capability', message: 'recent entities need a live graph' } };
    renderTab();
    await screen.findByLabelText('Graph');
    await waitFor(() => expect(screen.getByRole('option', { name: 'matkg_rsoxs_v2.json (rsoxs_v2)' })).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Operation'), 'kg_recent_entities');
    await userEvent.selectOptions(screen.getByLabelText('Graph'), 'rsoxs_v2');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Not supported for this graph: .*live graph/);
  });

  it('runs a corpus audit by name with optional sections', async () => {
    routes['GET /v1/operations/corpus-audit'] = { status: 200, body: { ...STATS, tool: 'curate_audit_corpus' } };
    renderTab();
    await screen.findByLabelText('Operation');
    await userEvent.selectOptions(screen.getByLabelText('Operation'), 'curate_audit_corpus');
    expect(screen.queryByLabelText('Graph')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText('Corpus'), 'rsoxs');
    await userEvent.type(screen.getByLabelText(/Sections/), 'summary, stubs');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));
    await screen.findByTestId('operation-status');
    expect(calls.find(c => c.path === '/v1/operations/corpus-audit')?.search).toBe('?corpus=rsoxs&sections=summary&sections=stubs');
  });

  it('tells the user when no listed graph carries an operations id', async () => {
    routes['GET /v1/graph/sources'].body = { sources: [{ ...LISTING.sources[0], graph_id: undefined }], tiled: { status: 'not_offered', detail: null } };
    renderTab();
    expect(await screen.findByText(/no graph with an operations id/)).toBeInTheDocument();
  });
});
