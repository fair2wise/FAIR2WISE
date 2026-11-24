import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HARVEST_POLL_FAST_MS, HARVEST_POLL_IDLE_MS, useRunningHarvest, useStartHarvest } from './harvestHooks';

let running: boolean;
let jobGets: number;

beforeEach(() => {
  vi.useFakeTimers();
  running = false;
  jobGets = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    if (url.pathname === '/v1/jobs' && (init?.method ?? 'GET') === 'GET') {
      jobGets += 1;
      const jobs = running
        ? [{ id: 'h1', kind: 'library_harvest', state: 'running', progress: null, error: null, created_at: '', updated_at: '' }]
        : [];
      return new Response(JSON.stringify({ jobs }));
    }
    if (url.pathname === '/v1/jobs' && init?.method === 'POST') {
      return new Response(JSON.stringify({ id: 'h1', kind: 'library_harvest', state: 'queued' }));
    }
    return new Response('{}', { status: 404 });
  }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
const settle = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe('useRunningHarvest polling', () => {
  it('backs off to 30 s while no harvest runs', async () => {
    const client = new QueryClient();
    renderHook(() => useRunningHarvest(true), { wrapper: wrapperFor(client) });
    await settle(10);
    expect(jobGets).toBe(1);
    await settle(HARVEST_POLL_FAST_MS * 5); // 25 s: still idle, no second fetch
    expect(jobGets).toBe(1);
    await settle(HARVEST_POLL_IDLE_MS - HARVEST_POLL_FAST_MS * 5 + 100);
    expect(jobGets).toBe(2);
  });

  it('polls every 5 s while a harvest runs and falls back to 30 s when it ends', async () => {
    running = true;
    const client = new QueryClient();
    renderHook(() => useRunningHarvest(true), { wrapper: wrapperFor(client) });
    await settle(10);
    await settle(HARVEST_POLL_FAST_MS * 3);
    expect(jobGets).toBe(4);
    running = false;
    await settle(HARVEST_POLL_FAST_MS); // this fetch sees no job
    const afterEnd = jobGets;
    await settle(HARVEST_POLL_FAST_MS * 4);
    expect(jobGets).toBe(afterEnd);
    await settle(HARVEST_POLL_IDLE_MS);
    expect(jobGets).toBeGreaterThan(afterEnd);
  });

  it('returns to the fast interval right after the user starts a run', async () => {
    const client = new QueryClient();
    const { result } = renderHook(() => ({ poll: useRunningHarvest(true), start: useStartHarvest() }), {
      wrapper: wrapperFor(client),
    });
    await settle(10);
    expect(jobGets).toBe(1);
    // The job list does not show the new run yet (still queued): the poll must be fast anyway.
    await act(async () => {
      result.current.start.mutate({ topic: 't', category: 'c', sources: ['arxiv'], max_downloads: 1 } as never);
      await vi.advanceTimersByTimeAsync(10);
    });
    const afterStart = jobGets; // the invalidation refetched at once
    expect(afterStart).toBe(2);
    await settle(HARVEST_POLL_FAST_MS + 100);
    expect(jobGets).toBe(afterStart + 1);
    running = true;
    await settle(HARVEST_POLL_FAST_MS * 2 + 100);
    expect(jobGets).toBeGreaterThanOrEqual(afterStart + 3);
  });
});
