import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphMockup } from './GraphMockup';
import { loadAgentSettings, saveAgentSettings } from './agentSettings';
import { resetGraphSourceStore, setGraphSourceStatus, setSelectedGraphSource } from './graphSourceStore';
import type { GraphPayload } from './data/liveAgent';

const NODE = { id: 'm:a', label: 'Alpha film', type: 'Material', description: '' };

function graphOf(sourcePath: string, nodes = [NODE]): GraphPayload {
  return { nodes, edges: [], source_path: sourcePath };
}

const LISTING = {
  sources: [{ id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true }],
  tiled: { status: 'ok', detail: null },
};

/** The listing of a `:local` server whose corpus `e2e_demo` is live on the Save target (editable Tiled source). */
const EDITABLE_LISTING = {
  sources: [
    { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: false, available: true },
    { id: 'json:other', kind: 'json', label: 'other.json', read_only: true, available: true },
  ],
  tiled: { status: 'ok', detail: null },
};

function stubFetch(listing: unknown = LISTING, results: unknown[] = []) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/v1/graph/sources')) return new Response(JSON.stringify(listing), { status: 200 });
    if (url.includes('/graph/nodes/search')) {
      return new Response(JSON.stringify({ query: 'alpha', retrieval_backend: 'lexical:source', results }), { status: 200 });
    }
    if (url.includes('/graph/node/') && init?.method === 'PATCH') {
      return new Response(JSON.stringify({ ...NODE, label: 'Alpha film (edited)' }), { status: 200 });
    }
    if (url.includes('/graph?') || url.endsWith('/graph')) {
      return new Response(JSON.stringify(graphOf('source:tiled:e2e_demo')), { status: 200 });
    }
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderViewer(graph: GraphPayload, isKgViewer = true, citeFocusNodeId: string | null = null) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <div style={{ width: 800, height: 600 }}>
        <GraphMockup
          graph={graph}
          highlightedNodeIds={[]}
          isKgViewer={isKgViewer}
          citeFocusNodeId={citeFocusNodeId}
          onToggleKgViewer={() => undefined}
          onKgViewerNodeLimitChange={() => undefined}
          onGraphSourceChange={() => undefined}
        />
      </div>
    </QueryClientProvider>,
  );
}

async function searchFor(text: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Search nodes' }));
  fireEvent.change(screen.getByPlaceholderText('Describe a node…'), { target: { value: text } });
  fireEvent.submit(screen.getByPlaceholderText('Describe a node…').closest('form') as HTMLFormElement);
}

function searchUrls(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls.map(call => String(call[0])).filter(url => url.includes('/graph/nodes/search'));
}

beforeEach(() => {
  window.localStorage.clear();
  resetGraphSourceStore();
});
afterEach(() => vi.unstubAllGlobals());

describe('GraphMockup graph source wiring', () => {
  it('shows the source selector in the KG viewer only', async () => {
    stubFetch();
    const { unmount } = renderViewer(graphOf('storage/kg/matkg_rsoxs_v1.json'));
    expect(await screen.findByRole('combobox', { name: 'Graph source' })).toBeInTheDocument();
    unmount();
    renderViewer(graphOf('storage/kg/matkg_rsoxs_v1.json'), false);
    expect(screen.queryByRole('combobox', { name: 'Graph source' })).toBeNull();
  });

  it('passes the viewed source on node search', async () => {
    const fetchMock = stubFetch();
    renderViewer(graphOf('source:tiled:e2e_demo'));
    await searchFor('alpha');
    await waitFor(() => expect(searchUrls(fetchMock)).toHaveLength(1));
    expect(searchUrls(fetchMock)[0]).toMatch(/\/graph\/nodes\/search\?source=tiled%3Ae2e_demo$/);
  });

  it('sends no source for the default graph', async () => {
    const fetchMock = stubFetch();
    renderViewer(graphOf('storage/kg/matkg_rsoxs_v1.json'));
    await searchFor('alpha');
    await waitFor(() => expect(searchUrls(fetchMock)).toHaveLength(1));
    expect(searchUrls(fetchMock)[0]).toMatch(/\/graph\/nodes\/search$/);
  });

  it('shows the error of an unavailable source instead of an empty canvas', async () => {
    stubFetch();
    setSelectedGraphSource('tiled:e2e_demo');
    renderViewer(graphOf('source:tiled:e2e_demo', []));
    act(() => setGraphSourceStatus({ loading: false, error: 'Tiled graph is unavailable' }));
    expect(await screen.findByTestId('graph-source-error')).toHaveTextContent('Tiled graph is unavailable');
  });

  it('says so when a source has no nodes', async () => {
    stubFetch();
    setSelectedGraphSource('tiled:e2e_demo');
    renderViewer(graphOf('source:tiled:e2e_demo', []));
    expect(await screen.findByTestId('graph-source-empty')).toBeInTheDocument();
  });
});

async function openNodePanel() {
  // The editable mode of Settings (the `json` mode never edits, whatever the source says).
  saveAgentSettings({ ...loadAgentSettings(), graphSource: 'splash_links' });
  await searchFor('alpha');
  fireEvent.click(await screen.findByRole('button', { name: /Alpha film/ }));
  await screen.findByRole('button', { name: 'Close node details' });
}

describe('GraphMockup Save on the corpus shown in the graph view (owner decision 2026-11-09)', () => {
  it('hides Edit for a read-only source', async () => {
    stubFetch(LISTING, [{ node: NODE, score: 1 }]);
    setSelectedGraphSource('tiled:e2e_demo');
    renderViewer(graphOf('source:tiled:e2e_demo'));
    await openNodePanel();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Edit properties' })).toBeNull());
  });

  it('offers Edit for an editable Tiled source and sends that source on Save and on the reload', async () => {
    const fetchMock = stubFetch(EDITABLE_LISTING, [{ node: NODE, score: 1 }]);
    setSelectedGraphSource('tiled:e2e_demo');
    renderViewer(graphOf('source:tiled:e2e_demo'));
    await openNodePanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit properties' }));
    fireEvent.click(screen.getByRole('button', { name: /^Save/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(call => call[1]?.method === 'PATCH')).toBe(true));
    const patch = fetchMock.mock.calls.find(call => call[1]?.method === 'PATCH');
    expect(String(patch?.[0])).toMatch(/\/graph\/node\/m%3Aa\?source=tiled%3Ae2e_demo$/);
    await waitFor(() => expect(fetchMock.mock.calls.map(call => String(call[0])))
      .toContainEqual(expect.stringMatching(/\/graph\?source=tiled%3Ae2e_demo$/)));
  });
});

describe('GraphMockup neighborhood truncation notice', () => {
  const focusNodes = [
    { id: 'tiled:beamline:ESAF-1', label: 'ESAF 1', type: 'ESAF', description: '' },
    { id: 'tiled:beamline:Proposal-1', label: 'Proposal 1', type: 'Proposal', description: '' },
  ];

  function stubNeighborhood(truncated: boolean) {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/graph/neighborhood/')) {
        const payload = {
          ...graphOf('tiled://graphql', focusNodes),
          edges: [{ source: focusNodes[0].id, target: focusNodes[1].id, predicate: 'rel:hasProposal' }],
        };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: truncated ? { 'X-F2W-Graph-Truncated': 'true' } : {},
        });
      }
      if (url.includes('/v1/graph/sources')) return new Response(JSON.stringify(LISTING), { status: 200 });
      return new Response('{}', { status: 404 });
    }));
  }

  it('tells the user when the neighborhood was cut by the server cap', async () => {
    stubNeighborhood(true);
    renderViewer(graphOf('tiled://graphql', focusNodes), true, 'tiled:beamline:ESAF-1');
    const notice = await screen.findByTestId('graph-neighborhood-truncated');
    expect(notice).toHaveTextContent('Neighbourhood limited to 2 nodes; 2+ nodes match');
    expect(notice).toHaveAttribute('role', 'status');
  });

  it('shows no notice for a complete neighborhood', async () => {
    stubNeighborhood(false);
    renderViewer(graphOf('tiled://graphql', focusNodes), true, 'tiled:beamline:ESAF-1');
    await waitFor(() => expect(screen.queryByLabelText('Loading neighborhood')).toBeNull());
    expect(screen.queryByTestId('graph-neighborhood-truncated')).toBeNull();
  });
});
