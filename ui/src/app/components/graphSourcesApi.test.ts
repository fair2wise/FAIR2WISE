import { describe, expect, it, vi } from 'vitest';
import { fetchGraphSources, parseGraphSourceListing } from './graphSourcesApi';

const LISTING = {
  sources: [
    { id: 'json:rsoxs_v1', kind: 'json', label: 'matkg_rsoxs_v1.json', read_only: true, available: true,
      node_count: null, edge_count: null, revision: null, detail: null },
    { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true,
      node_count: 268, edge_count: 126, revision: 'v1@abc', detail: null },
  ],
  tiled: { status: 'ok', detail: null },
};

describe('parseGraphSourceListing', () => {
  it('parses sources and the Tiled status', () => {
    const parsed = parseGraphSourceListing(LISTING);
    expect(parsed.sources.map(s => s.id)).toEqual(['json:rsoxs_v1', 'tiled:e2e_demo']);
    expect(parsed.sources[1]).toMatchObject({ kind: 'tiled', node_count: 268, edge_count: 126, read_only: true });
    // T19E: graph_id is carried when the server sends it and null otherwise (older server).
    expect(parseGraphSourceListing({ sources: [{ ...LISTING.sources[0], graph_id: 'rsoxs_v1' }] }).sources[0].graph_id)
      .toBe('rsoxs_v1');
    expect(parsed.sources[0].graph_id).toBeNull();
    expect(parsed.tiled).toEqual({ status: 'ok', detail: null });
  });

  it('drops malformed rows, defaults read_only to true and unknown statuses to not_offered', () => {
    const parsed = parseGraphSourceListing({
      sources: [{ id: 'json:a', kind: 'json' }, { id: 'x', kind: 'other' }, null, { kind: 'json' }],
      tiled: { status: 'weird' },
    });
    expect(parsed.sources).toHaveLength(1);
    expect(parsed.sources[0]).toMatchObject({ label: 'json:a', read_only: true, available: true, node_count: null });
    expect(parsed.tiled.status).toBe('not_offered');
    expect(parseGraphSourceListing(null)).toEqual({ sources: [], tiled: { status: 'not_offered', detail: null }, default: null });
  });
});

describe('fetchGraphSources', () => {
  it('requests /v1/graph/sources with same-origin credentials', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(LISTING), { status: 200 }));
    const listing = await fetchGraphSources(fetcher as unknown as typeof fetch);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/graph\/sources$/);
    expect(init.credentials).toBe('same-origin');
    expect(listing.sources).toHaveLength(2);
  });

  it.each([404, 401])('rejects with the status on HTTP %i', async status => {
    const fetcher = vi.fn(async () => new Response('{}', { status }));
    await expect(fetchGraphSources(fetcher as unknown as typeof fetch)).rejects.toMatchObject({ status });
  });
});
