import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryAgentActionStream, queryLiveAgentWithHistory, resetAgentSession } from './liveAgent';

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
    ]);

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      message: 'What about the second one?',
      messages: [
        { role: 'user', content: 'Compare P3HT and PTB7.' },
        { role: 'assistant', content: 'P3HT is first. PTB7 is second.' },
      ],
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
    const result = await queryAgentActionStream('yes', 'download', event => progress.push(event.phase), undefined, 0);

    expect(progress).toEqual(['download_started']);
    expect(result.status).toBe('awaiting_extraction_decision');
    expect(result.pending?.kind).toBe('extraction');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/chat/action/stream');
    expect(JSON.parse(String(init?.body))).toEqual({ decision: 'yes', kind: 'download', candidate_index: 0 });
  });

  it('resets backend session context', async () => {
    const responseBody = {
      status: 'reset',
      session_memory: 'runs/session/session_memory.json',
      session_memory_has_context: false,
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(responseBody), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await resetAgentSession();

    expect(result).toEqual(responseBody);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/session/reset');
    expect(init?.method).toBe('POST');
  });
});
