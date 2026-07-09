import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryLiveAgentWithHistory } from './liveAgent';

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
});
