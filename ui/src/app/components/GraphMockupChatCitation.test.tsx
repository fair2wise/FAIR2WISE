/**
 * Task 7 (owner bug list 2026-11): a KG reference clicked in the chat opens the same full entity record as a click in
 * the KG viewer (description and paper provenance), read from the corpus the answer took the node from.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphMockup } from './GraphMockup';
import { resetGraphSourceStore } from './graphSourceStore';
import type { GraphPayload } from './data/liveAgent';

/** What a multi-corpus chat answer carries for the cited node: id, label, type, the corpus, no description. */
const CHAT_NODE = { id: 'm:poly', label: 'P3HT film', type: 'Material', description: '', graph_id: 'qwen3_235b_580papers' };
const FULL_NODE = {
  ...CHAT_NODE,
  description: 'Regioregular poly(3-hexylthiophene) thin film used as a donor.',
  publications: [{ source_paper: 'doi_10.1000_xyz.pdf', paper_title: 'Edge-on packing in P3HT', doi: '10.1000/xyz' }],
};

const LISTING = {
  sources: [
    { id: 'json:rsoxs_v4', kind: 'json', label: 'rsoxs_v4', read_only: true, available: true, graph_id: 'rsoxs_v4' },
    { id: 'json:qwen3_235b_580papers/v1', kind: 'json', label: 'qwen3 / v1 (release)', read_only: true, available: true,
      graph_id: 'qwen3_235b_580papers' },
    { id: 'tiled:qwen3_235b_580papers', kind: 'tiled', label: 'qwen3_235b_580papers / v1', read_only: true, available: true,
      graph_id: 'qwen3_235b_580papers' },
  ],
  tiled: { status: 'ok', detail: null },
};

function stubFetch(nodeAnswer: (url: string) => Response, listing: unknown = LISTING) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/v1/graph/sources')) return new Response(JSON.stringify(listing), { status: 200 });
    if (url.includes('/graph/node/')) return nodeAnswer(url);
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const nodeUrls = (fetchMock: ReturnType<typeof stubFetch>) => fetchMock.mock.calls
  .map(call => String(call[0])).filter(url => url.includes('/graph/node/'));

/** The chat view: the viewer is off, the answer's graph is `retrievedGraph`, a citation click sets `citeFocusNodeId`. */
function renderChatGraph(retrieved: GraphPayload, viewerSourcePath = 'source:json:rsoxs_v4') {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <div style={{ width: 800, height: 600 }}>
        <GraphMockup
          graph={{ nodes: [], edges: [], source_path: viewerSourcePath }}
          retrievedGraph={retrieved}
          citeFocusNodeId="m:poly"
          highlightedNodeIds={[]}
          isKgViewer={false}
          onToggleKgViewer={() => undefined}
          onKgViewerNodeLimitChange={() => undefined}
          onGraphSourceChange={() => undefined}
        />
      </div>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  resetGraphSourceStore();
});
afterEach(() => vi.unstubAllGlobals());

describe('chat citation opens the full entity record', () => {
  it('reads the record from the corpus the answer took it from, with description and papers', async () => {
    // The viewer's source (rsoxs_v4) does not hold the node: only the cited corpus' source answers.
    const fetchMock = stubFetch(url => (url.includes('source=tiled%3Aqwen3_235b_580papers')
      ? new Response(JSON.stringify(FULL_NODE), { status: 200 })
      : new Response(JSON.stringify({ detail: { code: 'x', message: 'Node not found: m:poly' } }), { status: 404 })));
    renderChatGraph({ nodes: [CHAT_NODE], edges: [], source_path: 'chat' });
    expect(await screen.findByText(/Regioregular poly\(3-hexylthiophene\)/)).toBeInTheDocument();
    expect(await screen.findByText('Edge-on packing in P3HT')).toBeInTheDocument();
    expect(screen.queryByTestId('node-detail-error')).toBeNull();
    const urls = nodeUrls(fetchMock);
    expect(urls).toHaveLength(1);
    expect(urls[0]).toMatch(/\/graph\/node\/m%3Apoly\?source=tiled%3Aqwen3_235b_580papers$/);
  });

  it('prefers an explicit corpus field over graph_id', async () => {
    const fetchMock = stubFetch(() => new Response(JSON.stringify(FULL_NODE), { status: 200 }));
    renderChatGraph({ nodes: [{ ...CHAT_NODE, graph_id: 'other', corpus: 'qwen3_235b_580papers' }], edges: [], source_path: 'chat' });
    await screen.findByText(/Regioregular poly/);
    expect(nodeUrls(fetchMock)[0]).toContain('source=tiled%3Aqwen3_235b_580papers');
  });

  it('falls back to the viewer source when the node names no listed corpus', async () => {
    const fetchMock = stubFetch(url => (url.includes('source=json%3Arsoxs_v4')
      ? new Response(JSON.stringify(FULL_NODE), { status: 200 })
      : new Response('{}', { status: 404 })));
    renderChatGraph({ nodes: [{ ...CHAT_NODE, graph_id: 'unlisted' }], edges: [], source_path: 'chat' });
    await screen.findByText(/Regioregular poly/);
    expect(nodeUrls(fetchMock)).toHaveLength(1);
    expect(nodeUrls(fetchMock)[0]).toContain('source=json%3Arsoxs_v4');
  });

  it('says that the record could not be loaded instead of showing an empty panel', async () => {
    stubFetch(() => new Response(JSON.stringify({ detail: { code: 'unknown_source', message: 'Unknown graph source' } }), { status: 404 }));
    renderChatGraph({ nodes: [CHAT_NODE], edges: [], source_path: 'chat' });
    const alert = await screen.findByTestId('node-detail-error');
    expect(alert).toHaveTextContent('The full record (description and papers) could not be loaded');
    expect(alert).toHaveTextContent('Showing only what the answer carried');
    // The label from the answer is still shown.
    expect(screen.getByText('P3HT film')).toBeInTheDocument();
  });

  it('does not offer Edit for a node read from another corpus than the viewer shows', async () => {
    stubFetch(() => new Response(JSON.stringify(FULL_NODE), { status: 200 }), {
      ...LISTING,
      sources: LISTING.sources.map(source => ({ ...source, read_only: false })),
    });
    renderChatGraph({ nodes: [CHAT_NODE], edges: [], source_path: 'chat' });
    await screen.findByText(/Regioregular poly/);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Edit properties' })).toBeNull());
  });
});
