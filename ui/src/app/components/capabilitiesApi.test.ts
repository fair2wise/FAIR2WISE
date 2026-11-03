import { afterEach, describe, expect, it, vi } from 'vitest';
import { canEditNode, fetchCapabilities, parseCapabilities } from './capabilitiesApi';

const FULL = { role: 'user', profile: 'als', can_edit: false, can_save: false, can_upload: false };

afterEach(() => vi.unstubAllGlobals());

describe('parseCapabilities', () => {
  it('parses the five fields', () => {
    expect(parseCapabilities(FULL)).toEqual(FULL);
  });
  it('turns missing, malformed or non-boolean fields into null', () => {
    expect(parseCapabilities({ role: 'root', can_save: 'false', can_edit: 0, profile: 3 })).toEqual({
      role: null, profile: null, can_edit: null, can_save: null, can_upload: null,
    });
    for (const bad of [null, undefined, 'x', 42, []]) {
      expect(parseCapabilities(bad).can_save).toBeNull();
    }
  });
});

describe('canEditNode gate', () => {
  it('shows while capabilities are unknown', () => {
    expect(canEditNode('tiled', undefined)).toBe(true);
    expect(canEditNode('tiled', parseCapabilities({}))).toBe(true);
  });
  it('hides only on an explicit can_save false', () => {
    expect(canEditNode('tiled', parseCapabilities({ can_save: false }))).toBe(false);
    expect(canEditNode('tiled', parseCapabilities({ can_save: true }))).toBe(true);
  });
  it('keeps hiding for the json graph source', () => {
    expect(canEditNode('json', parseCapabilities({ can_save: true }))).toBe(false);
  });
});

describe('fetchCapabilities', () => {
  it('requests /v1/capabilities with same-origin credentials', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(FULL), { status: 200 }));
    expect((await fetchCapabilities(fetcher as unknown as typeof fetch)).can_save).toBe(false);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/capabilities$/);
    expect(init.credentials).toBe('same-origin');
  });

  it.each([404, 401])('rejects with the status on HTTP %i', async status => {
    const fetcher = vi.fn(async () => new Response('{}', { status }));
    await expect(fetchCapabilities(fetcher as unknown as typeof fetch)).rejects.toMatchObject({ status });
  });

  it('rejects on a network error', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('down'); });
    await expect(fetchCapabilities(fetcher as unknown as typeof fetch)).rejects.toMatchObject({ status: 0 });
  });
});
