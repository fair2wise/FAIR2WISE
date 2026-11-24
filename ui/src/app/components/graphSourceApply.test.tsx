import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createGraphSourceApplier, FOCUS_REAPPLY_MIN_INTERVAL_MS } from './graphSourceApply';
import { capabilitiesKey } from './capabilitiesHooks';

const KEY = 'fair2wise-agent-settings-v1';

/** One fake server runtime (process-global graph_source) shared by every "tab" of a test. */
function fakeServer(initial: 'splash' | 'json', opts: { profile?: string | null; putStatus?: number } = {}) {
  const state = { graph_source: initial, puts: [] as unknown[], gets: 0, capGets: 0 };
  const settings = () => ({
    backend: 'cborg', model: 'lbl/cborg-chat', graph_source: state.graph_source, workflow_mode: 'agentic',
    extraction_mode: 'targeted', targeted_max_pages: 6, json_graph_path: null, available_json_graphs: [],
    available_cborg_models: [], default_ollama_model: 'x',
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://x').pathname;
    if (path === '/v1/capabilities') {
      state.capGets += 1;
      if (opts.profile === null) return new Response('{}', { status: 404 });
      return new Response(JSON.stringify({ role: 'user', profile: opts.profile ?? 'local' }));
    }
    if (path === '/settings' && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body));
      state.puts.push(body);
      if (opts.putStatus && opts.putStatus !== 200) {
        return new Response(JSON.stringify({ detail: 'No JSON graph files in storage/kg' }), { status: opts.putStatus });
      }
      state.graph_source = body.graph_source;
      return new Response(JSON.stringify(settings()));
    }
    if (path === '/settings') {
      state.gets += 1;
      return new Response(JSON.stringify(settings()));
    }
    return new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return state;
}

function store(blob: Record<string, unknown> | null) {
  if (blob) window.localStorage.setItem(KEY, JSON.stringify({ settingsVersion: 2, ...blob }));
}
const stored = () => JSON.parse(window.localStorage.getItem(KEY) ?? '{}');

let clock = 0;
const now = () => clock;
function applier(qc = new QueryClient()) {
  return { qc, a: createGraphSourceApplier(qc, { now }) };
}

beforeEach(() => {
  clock = 1_000_000;
  window.localStorage.clear();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('applyOwnGraphSource: boot', () => {
  it('server splash + UI json default -> exactly one PUT {"graph_source":"json"}', async () => {
    const server = fakeServer('splash');
    await applier().a.boot();
    expect(server.puts).toEqual([{ graph_source: 'json' }]);
    expect(stored().graphSource).toBe('json');
  });

  it('server json + UI json -> no PUT', async () => {
    const server = fakeServer('json');
    await applier().a.boot();
    expect(server.puts).toEqual([]);
  });

  it('server json + explicit Tiled pick -> PUT splash and the stored pick survives (clobber regression)', async () => {
    store({ graphSource: 'splash_links', graphSourceChosen: true });
    const server = fakeServer('json');
    await applier().a.boot();
    expect(server.puts).toEqual([{ graph_source: 'splash' }]);
    expect(stored().graphSource).toBe('splash_links');
    expect(stored().graphSourceChosen).toBe(true);
  });

  it('keeps the explicit pick even when the PUT fails', async () => {
    store({ graphSource: 'splash_links', graphSourceChosen: true });
    fakeServer('json', { putStatus: 400 });
    await applier().a.boot();
    expect(stored().graphSource).toBe('splash_links');
  });

  it('PUT 400 (no JSON files) -> no crash, UI state kept, no user-facing error', async () => {
    const server = fakeServer('splash', { putStatus: 400 });
    await expect(applier().a.boot()).resolves.toBeUndefined();
    expect(server.puts).toEqual([{ graph_source: 'json' }]);
    expect(stored().graphSource).toBe('json');
  });

  it('a failed GET /settings is ignored', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('x', { status: 500 })));
    await expect(applier().a.boot()).resolves.toBeUndefined();
  });

  it('invalidates the cached capabilities after the apply, and only then', async () => {
    fakeServer('splash');
    const { qc, a } = applier();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    await a.boot();
    expect(spy).toHaveBeenCalledWith({ queryKey: capabilitiesKey });
    spy.mockClear();
    fakeServer('json');
    await applier(qc).a.boot();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('applyOwnGraphSource: ALS gating', () => {
  it('ALS profile, source not chosen -> no PUT', async () => {
    const server = fakeServer('splash', { profile: 'als-dev' });
    await applier().a.boot();
    expect(server.puts).toEqual([]);
    expect(stored().graphSource).toBe('json'); // the UI keeps its own source
  });

  it('ALS profile, source chosen -> PUT', async () => {
    store({ graphSource: 'json', graphSourceChosen: true });
    const server = fakeServer('splash', { profile: 'als-prod' });
    await applier().a.boot();
    expect(server.puts).toEqual([{ graph_source: 'json' }]);
  });

  it('unknown profile (capabilities 404), not chosen -> treated as ALS, no PUT', async () => {
    const server = fakeServer('splash', { profile: null });
    await applier().a.boot();
    expect(server.puts).toEqual([]);
  });

  it(':local applies regardless of chosen', async () => {
    store({ graphSource: 'json', graphSourceChosen: false });
    const server = fakeServer('splash', { profile: 'local' });
    await applier().a.boot();
    expect(server.puts).toEqual([{ graph_source: 'json' }]);
  });
});

describe('applyOwnGraphSource: focus', () => {
  it('PUTs only if the server differs, debounced to 15 s', async () => {
    const server = fakeServer('json');
    const { a } = applier();
    await a.boot();
    clock += FOCUS_REAPPLY_MIN_INTERVAL_MS + 1;
    await a.focus(); // same source: GET only
    expect(server.puts).toEqual([]);
    server.graph_source = 'splash'; // server restarted
    clock += 1_000;
    await a.focus(); // debounced
    expect(server.puts).toEqual([]);
    clock += FOCUS_REAPPLY_MIN_INTERVAL_MS;
    await a.focus();
    expect(server.puts).toEqual([{ graph_source: 'json' }]);
  });

  it('never overlaps an in-flight apply', async () => {
    const server = fakeServer('splash');
    const { a } = applier();
    const first = a.boot();
    clock += FOCUS_REAPPLY_MIN_INTERVAL_MS + 1;
    await a.focus(); // boot still running: skipped
    await first;
    expect(server.puts).toHaveLength(1);
    expect(server.gets).toBe(1);
  });

  it('does not change the stored settings and swallows errors', async () => {
    store({ graphSource: 'json' });
    const before = window.localStorage.getItem(KEY);
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
    const { a } = applier();
    await expect(a.focus()).resolves.toBeUndefined();
    expect(window.localStorage.getItem(KEY)).toBe(before);
  });

  it('ALS gating applies on focus too', async () => {
    const server = fakeServer('splash', { profile: 'als-dev' });
    const { a } = applier();
    clock += FOCUS_REAPPLY_MIN_INTERVAL_MS + 1;
    await a.focus();
    expect(server.puts).toEqual([]);
  });
});

describe('applyOwnGraphSource: two tabs on one server (last writer wins)', () => {
  it('the last PUT decides the runtime, each tab keeps its stored source, the loser restores its own on focus', async () => {
    // Both tabs of one browser share localStorage, so each "tab" is modelled by swapping the stored blob:
    // tab A chose Tiled Graph, tab B runs the JSON default.
    const server = fakeServer('splash');
    const tabA = applier();
    const tabB = applier();
    const asA = () => store({ graphSource: 'splash_links', graphSourceChosen: true });
    const asB = () => store({ graphSource: 'json', graphSourceChosen: false });

    asA();
    await tabA.a.boot(); // server already splash: nothing to do
    expect(server.puts).toEqual([]);
    asB();
    await tabB.a.boot(); // B is last: runtime json
    expect(server.graph_source).toBe('json');
    expect(stored().graphSource).toBe('json');

    asA();
    clock += FOCUS_REAPPLY_MIN_INTERVAL_MS + 1;
    await tabA.a.focus(); // A refocuses and restores its own source: runtime splash again
    expect(server.graph_source).toBe('splash');
    expect(stored().graphSource).toBe('splash_links');
    expect(stored().graphSourceChosen).toBe(true);
    expect(server.puts).toEqual([{ graph_source: 'json' }, { graph_source: 'splash' }]);
  });
});
