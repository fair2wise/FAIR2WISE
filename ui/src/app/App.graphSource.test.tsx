import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

// AppShell applies the UI's own knowledge graph source on load and on focus (owner decision 2026-11).

const KEY = 'fair2wise-agent-settings-v1';

function settingsBody(graphSource: 'splash' | 'json') {
  return {
    backend: 'cborg', model: 'lbl/cborg-chat', graph_source: graphSource, workflow_mode: 'agentic',
    extraction_mode: 'targeted', targeted_max_pages: 6, json_graph_path: null, available_json_graphs: [],
    available_cborg_models: ['lbl/cborg-chat'], default_ollama_model: 'x',
  };
}

function stubServer(initial: 'splash' | 'json', hold?: Promise<void>) {
  const state = { source: initial, puts: [] as unknown[], capGets: 0 };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://x').pathname;
    if (path === '/settings' && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body));
      state.puts.push(body);
      if (body.graph_source) state.source = body.graph_source;
      return new Response(JSON.stringify(settingsBody(state.source)));
    }
    if (path === '/settings') {
      if (hold) await hold;
      return new Response(JSON.stringify(settingsBody(state.source)));
    }
    if (path === '/v1/capabilities') {
      state.capGets += 1;
      return new Response(JSON.stringify({ role: 'local_operator', profile: 'local', can_edit: true, can_save: true }));
    }
    if (path === '/graph') return new Response(JSON.stringify({ nodes: [], edges: [], source_path: '' }));
    return new Response('{}', { status: 404 });
  }));
  return state;
}

async function renderApp() {
  vi.resetModules();
  const { default: App } = await import('./App');
  return render(<App />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('AppShell graph source apply', { timeout: 30_000 }, () => {
  it('does not block the first render on GET /settings', async () => {
    let release!: () => void;
    const hold = new Promise<void>(resolve => { release = resolve; });
    const server = stubServer('splash', hold);
    await renderApp();
    expect(await screen.findByPlaceholderText('Ask a domain-specific question...')).toBeInTheDocument();
    expect(server.puts).toEqual([]); // the settings promise is still pending
    release();
    await waitFor(() => expect(server.puts).toEqual([{ graph_source: 'json' }]));
  });

  it('boot sends exactly one partial PUT and re-applies on focus only after the debounce and a drift', async () => {
    let clock = 5_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => clock);
    const server = stubServer('splash');
    await renderApp();
    await waitFor(() => expect(server.puts).toEqual([{ graph_source: 'json' }]));

    server.source = 'splash'; // server restarted
    act(() => { window.dispatchEvent(new Event('focus')); });
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(server.puts).toHaveLength(1); // debounced

    clock += 16_000;
    act(() => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(server.puts).toHaveLength(2));
    expect(server.puts[1]).toEqual({ graph_source: 'json' });

    server.source = 'splash';
    clock += 16_000;
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(server.puts).toHaveLength(2); // hidden: no re-apply
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await waitFor(() => expect(server.puts).toHaveLength(3));
    delete (document as unknown as Record<string, unknown>).visibilityState;
  });

  it('refreshes the cached capabilities after the boot apply and after Save', async () => {
    const server = stubServer('splash');
    await renderApp();
    await waitFor(() => expect(server.puts).toHaveLength(1));
    await waitFor(() => expect(server.capGets).toBeGreaterThanOrEqual(2)); // initial read + refetch after apply
    const afterBoot = server.capGets;

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    fireEvent.click(await screen.findByRole('radio', { name: /Tiled Graph/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Save Preferences' }));
    await waitFor(() => expect(server.capGets).toBeGreaterThan(afterBoot));
    expect(window.localStorage.getItem(KEY)).toContain('splash_links');
  });

  it('keeps an explicit Tiled pick when the server reports json', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({
      graphSource: 'splash_links', settingsVersion: 2, graphSourceChosen: true,
    }));
    const server = stubServer('json');
    await renderApp();
    await waitFor(() => expect(server.puts).toEqual([{ graph_source: 'splash' }]));
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem(KEY)!).graphSource).toBe('splash_links'));
  });
});
