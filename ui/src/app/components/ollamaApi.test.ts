import { describe, expect, it, vi } from 'vitest';
import { fetchOllamaModels, normalizeOllamaListing, ollamaStateText } from './ollamaApi';

const respond = (status: number, body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status }));

describe('ollamaApi', () => {
  it('reads the list from the API route, never from Ollama', async () => {
    const fetcher = respond(200, { state: 'ok', models: ['llama3:8b', 'qwen2.5:7b'], error: null });
    const picker = await fetchOllamaModels(fetcher as unknown as typeof fetch);
    expect(picker).toEqual({ state: 'ok', models: ['llama3:8b', 'qwen2.5:7b'], error: null });
    expect(String(fetcher.mock.calls[0][0])).toContain('/v1/ollama/models');
    expect(String(fetcher.mock.calls[0][0])).not.toContain('11434');
  });

  it('keeps every server state', async () => {
    for (const state of ['empty', 'unset', 'unreachable', 'error'] as const) {
      const picker = await fetchOllamaModels(respond(200, { state, models: [], error: 'why' }) as unknown as typeof fetch);
      expect(picker.state).toBe(state);
    }
  });

  it('treats a server without the route (or a caller not allowed) as not offered', async () => {
    for (const status of [401, 403, 404, 405]) {
      expect((await fetchOllamaModels(respond(status, {}) as unknown as typeof fetch)).state).toBe('not_offered');
    }
  });

  it('reports other failures and never throws', async () => {
    expect((await fetchOllamaModels(respond(500, { message: 'boom' }) as unknown as typeof fetch)).state).toBe('failed');
    const offline = vi.fn(async () => { throw new TypeError('Failed to fetch'); });
    expect((await fetchOllamaModels(offline as unknown as typeof fetch)).state).toBe('failed');
  });

  it('turns an unexpected body into an error listing and an ok list with no names into empty', () => {
    expect(normalizeOllamaListing({ foo: 1 }).state).toBe('error');
    expect(normalizeOllamaListing(null).state).toBe('error');
    expect(normalizeOllamaListing({ state: 'ok', models: [] }).state).toBe('empty');
    expect(normalizeOllamaListing({ state: 'ok', models: ['a', 'a', '', 3] }).models).toEqual(['a']);
  });

  it('says what each state means', () => {
    expect(ollamaStateText({ state: 'unset', models: [], error: null })).toContain('OLLAMA_URL is unset');
    expect(ollamaStateText({ state: 'unreachable', models: [], error: null })).toContain('could not reach Ollama');
    expect(ollamaStateText({ state: 'empty', models: [], error: null })).toContain('no models');
    expect(ollamaStateText({ state: 'ok', models: ['a'], error: null })).toBe('');
  });
});
