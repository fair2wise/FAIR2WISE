import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphMockup } from './GraphMockup';
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

function stubFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/v1/graph/sources')) return new Response(JSON.stringify(LISTING), { status: 200 });
    if (url.includes('/graph/nodes/search')) {
      return new Response(JSON.stringify({ query: 'alpha', retrieval_backend: 'lexical:source', results: [] }), { status: 200 });
    }
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderViewer(graph: GraphPayload, isKgViewer = true) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <div style={{ width: 800, height: 600 }}>
        <GraphMockup
          graph={graph}
          highlightedNodeIds={[]}
          isKgViewer={isKgViewer}
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
