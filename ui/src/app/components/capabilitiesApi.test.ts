import { afterEach, describe, expect, it, vi } from 'vitest';
import { canApprove, canEditNode, canPublish, canRunOperations, fetchCapabilities, parseCapabilities, snippetsDisclosed } from './capabilitiesApi';

const FULL = { role: 'user', profile: 'als', can_edit: false, can_save: false, can_upload: false, can_run_operations: true, can_approve: false,
  can_publish: false, snippets_disclosed: false };

afterEach(() => vi.unstubAllGlobals());

describe('snippetsDisclosed', () => {
  it('is true only for an explicit true; an older server without the field means false', () => {
    expect(snippetsDisclosed(parseCapabilities({ snippets_disclosed: true }))).toBe(true);
    expect(snippetsDisclosed(parseCapabilities({ snippets_disclosed: false }))).toBe(false);
    expect(snippetsDisclosed(parseCapabilities({ can_edit: true }))).toBe(false);
    expect(snippetsDisclosed(parseCapabilities({ snippets_disclosed: 'true' }))).toBe(false);
    expect(snippetsDisclosed(undefined)).toBe(false);
    expect(snippetsDisclosed(null)).toBe(false);
  });
});

describe('parseCapabilities', () => {
  it('parses the eight fields', () => {
    expect(parseCapabilities(FULL)).toEqual(FULL);
  });
  it('turns missing, malformed or non-boolean fields into null', () => {
    expect(parseCapabilities({ role: 'root', can_save: 'false', can_edit: 0, profile: 3 })).toEqual({
      role: null, profile: null, can_edit: null, can_save: null, can_upload: null, can_run_operations: null,
      can_approve: null, can_publish: null, snippets_disclosed: null,
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

describe('canRunOperations gate (T19E)', () => {
  it('shows only on an explicit true; unknown, missing field and false hide', () => {
    expect(canRunOperations(undefined)).toBe(false);
    expect(canRunOperations(parseCapabilities({}))).toBe(false);
    expect(canRunOperations(parseCapabilities({ can_run_operations: 'true' }))).toBe(false);
    expect(canRunOperations(parseCapabilities({ can_run_operations: false }))).toBe(false);
    expect(canRunOperations(parseCapabilities({ can_run_operations: true }))).toBe(true);
  });
});

describe('canApprove gate (Pending Approvals)', () => {
  it('shows only on an explicit true; unknown, missing field, a string and false hide', () => {
    expect(canApprove(undefined)).toBe(false);
    expect(canApprove(parseCapabilities({}))).toBe(false);
    expect(canApprove(parseCapabilities({ can_approve: 'true' }))).toBe(false);
    expect(canApprove(parseCapabilities({ can_approve: false }))).toBe(false);
    expect(canApprove(parseCapabilities({ can_approve: true }))).toBe(true);
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

describe('canPublish gate (Publish to Tiled)', () => {
  it('shows only on an explicit true', () => {
    expect(canPublish(undefined)).toBe(false);
    expect(canPublish(parseCapabilities({}))).toBe(false);
    expect(canPublish(parseCapabilities({ can_publish: 'true' }))).toBe(false);
    expect(canPublish(parseCapabilities({ can_publish: false }))).toBe(false);
    expect(canPublish(parseCapabilities({ can_publish: true }))).toBe(true);
  });
});
