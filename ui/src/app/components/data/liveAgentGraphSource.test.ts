import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchGraphNeighborhood,
  fetchGraphNeighborhoodResult,
  fetchGraphNodeDetail,
  fetchLiveGraph,
  graphSourceQuery,
  searchGraphNodes,
} from './liveAgent';

const GRAPH = { nodes: [], edges: [], source_path: 'source:tiled:e2e_demo' };

function stub(body: unknown = GRAPH, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function lastUrl(fetchMock: ReturnType<typeof stub>): string {
  return String((fetchMock.mock.calls[0] as unknown as [string])[0]);
}

afterEach(() => vi.unstubAllGlobals());

describe('graph source on every graph request', () => {
  it('builds the source query only for a real source', () => {
    expect(graphSourceQuery(undefined)).toBe('');
    expect(graphSourceQuery(null)).toBe('');
    expect(graphSourceQuery('')).toBe('');
    expect(graphSourceQuery('tiled:e2e_demo')).toBe('source=tiled%3Ae2e_demo');
  });

  it('GET /graph sends source, and nothing at all for the default graph', async () => {
    const withSource = stub();
    await fetchLiveGraph('tiled:e2e_demo');
    expect(lastUrl(withSource)).toMatch(/\/graph\?source=tiled%3Ae2e_demo$/);
    const plain = stub();
    await fetchLiveGraph();
    expect(lastUrl(plain)).toMatch(/\/graph$/);
    const nulled = stub();
    await fetchLiveGraph(null);
    expect(lastUrl(nulled)).toMatch(/\/graph$/);
  });

  it('POST /graph/nodes/search sends source as a query parameter and keeps the body', async () => {
    const mock = stub({ query: 'a', retrieval_backend: 'lexical:source', results: [] });
    await searchGraphNodes('beta', 5, undefined, 'json:rsoxs_v1');
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/graph\/nodes\/search\?source=json%3Arsoxs_v1$/);
    expect(JSON.parse(String(init.body))).toEqual({ query: 'beta', limit: 5 });
    const plain = stub({ query: 'a', retrieval_backend: 'x', results: [] });
    await searchGraphNodes('beta', 5);
    expect(lastUrl(plain)).toMatch(/\/graph\/nodes\/search$/);
  });

  it('GET /graph/neighborhood sends hops and source', async () => {
    const mock = stub();
    await fetchGraphNeighborhood('m:a', 2, 'tiled:e2e_demo');
    expect(lastUrl(mock)).toMatch(/\/graph\/neighborhood\/m%3Aa\?hops=2&source=tiled%3Ae2e_demo$/);
    const plain = stub();
    await fetchGraphNeighborhood('m:a', 2);
    expect(lastUrl(plain)).toMatch(/\/graph\/neighborhood\/m%3Aa\?hops=2$/);
  });

  it('reports the truncation header of a neighborhood read without changing the body', async () => {
    const body = { nodes: [], edges: [], source_path: 'tiled://graphql' };
    const cut = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'X-F2W-Graph-Truncated': 'true' } }));
    vi.stubGlobal('fetch', cut);
    expect(await fetchGraphNeighborhoodResult('m:a', 3)).toEqual({ graph: body, truncated: true });
    expect(await fetchGraphNeighborhood('m:a', 3)).toEqual(body);
    const whole = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', whole);
    expect(await fetchGraphNeighborhoodResult('m:a', 3)).toEqual({ graph: body, truncated: false });
  });

  it('GET /graph/node sends source (and still json_graph_path when given)', async () => {
    const mock = stub({ id: 'm:a', label: 'A', type: 'Thing', description: '' });
    await fetchGraphNodeDetail('m:a', undefined, 'json:rsoxs_v1');
    expect(lastUrl(mock)).toMatch(/\/graph\/node\/m%3Aa\?source=json%3Arsoxs_v1$/);
    const plain = stub({ id: 'm:a', label: 'A', type: 'Thing', description: '' });
    await fetchGraphNodeDetail('m:a');
    expect(lastUrl(plain)).toMatch(/\/graph\/node\/m%3Aa$/);
  });

  it('shows the typed message of a source error, not raw JSON', async () => {
    stub({ detail: { code: 'source_unavailable', message: 'Tiled graph is unavailable' } }, 503);
    await expect(fetchLiveGraph('tiled:e2e_demo')).rejects.toThrow('Tiled graph is unavailable');
    stub({ detail: { code: 'unknown_source', message: 'Unknown graph source' } }, 404);
    await expect(fetchGraphNeighborhood('m:a', 1, 'json:x')).rejects.toThrow('Unknown graph source');
    stub({ detail: { code: 'unknown_source', message: 'Unknown graph source' } }, 404);
    await expect(searchGraphNodes('a', 1, undefined, 'json:x')).rejects.toThrow('Unknown graph source');
    stub({ detail: { code: 'unknown_source', message: 'Unknown graph source' } }, 404);
    await expect(fetchGraphNodeDetail('a', undefined, 'json:x')).rejects.toThrow('Unknown graph source');
  });

  it('keeps the default graph error text unchanged', async () => {
    stub({ detail: 'boom' }, 500);
    await expect(fetchLiveGraph()).rejects.toThrow('{"detail":"boom"}');
  });
});
