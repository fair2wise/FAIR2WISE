import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  deleteAgentSession,
  queryAgentActionStream,
  queryLiveAgentWithHistory,
  resetAgentSession,
  searchGraphNodes,
  updateGraphNode,
} from './liveAgent';

describe('liveAgent chat requests', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends recent chat history with the current message', async () => {
    const responseBody = {
      status: 'answered',
      answer: 'ok',
      sufficient: true,
      node_ids: [],
      confidence: 1,
      rounds: [],
      graph: { nodes: [], edges: [], source_path: '' },
      workdir: 'runs/session',
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await queryLiveAgentWithHistory('What about the second one?', undefined, [
      { role: 'user', content: 'Compare P3HT and PTB7.' },
      { role: 'assistant', content: '' },
      { role: 'assistant', content: 'P3HT is first. PTB7 is second.' },
    ], 'chat-123');

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      message: 'What about the second one?',
      messages: [
        { role: 'user', content: 'Compare P3HT and PTB7.' },
        { role: 'assistant', content: 'P3HT is first. PTB7 is second.' },
      ],
      session_id: 'chat-123',
    });
  });

  it('streams a download decision action and returns the completed response', async () => {
    const complete = {
      status: 'awaiting_extraction_decision',
      answer: 'Downloaded paper.',
      sufficient: false,
      node_ids: [],
      confidence: 1,
      rounds: [],
      graph: { nodes: [], edges: [], source_path: '' },
      workdir: 'runs/session',
      pending: { kind: 'extraction', prompt: 'Run extraction?' },
    };
    const sse =
      'event: progress\ndata: {"phase":"download_started","message":"Fetching paper"}\n\n' +
      `event: complete\ndata: ${JSON.stringify(complete)}\n\n`;
    const fetchMock = vi.fn(async () => new Response(sse, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const progress: string[] = [];
    const result = await queryAgentActionStream(
      'yes',
      'download',
      event => progress.push(event.phase),
      undefined,
      0,
      'chat-123',
    );

    expect(progress).toEqual(['download_started']);
    expect(result.status).toBe('awaiting_extraction_decision');
    expect(result.pending?.kind).toBe('extraction');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/chat/action/stream');
    expect(JSON.parse(String(init?.body))).toEqual({
      decision: 'yes',
      kind: 'download',
      candidate_index: 0,
      session_id: 'chat-123',
    });
  });

  it('resets backend session context', async () => {
    const responseBody = {
      status: 'reset',
      session_memory: 'runs/session/session_memory.json',
      session_memory_has_context: false,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await resetAgentSession(undefined, 'chat-123');

    expect(result).toEqual(responseBody);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/session/reset');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ session_id: 'chat-123' });
  });

  it('deletes backend session context', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await deleteAgentSession('chat-123');

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/session/chat-123');
    expect(init?.method).toBe('DELETE');
  });

  it('searches nodes in the active graph', async () => {
    const responseBody = {
      query: 'solid electrolyte',
      retrieval_backend: 'semantic',
      results: [{ node: { id: 'n1', label: 'Electrolyte', type: 'Material', description: '' }, score: 0.91 }],
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await searchGraphNodes('solid electrolyte', 8);

    expect(result).toEqual(responseBody);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/graph/nodes/search');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ query: 'solid electrolyte', limit: 8 });
  });

  it('sends directed relationship updates with node edits', async () => {
    const responseBody = { id: 'n1', label: 'One', type: 'Thing', description: '' };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const relationshipUpdates = [
      { action: 'add' as const, source: 'n2', predicate: 'rel:affects', target: 'n1' },
    ];

    await updateGraphNode('n1', { relationship_updates: relationshipUpdates });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/graph/node/n1');
    expect(init?.method).toBe('PATCH');
    expect(JSON.parse(String(init?.body))).toEqual({ relationship_updates: relationshipUpdates });
  });
});
