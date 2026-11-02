import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useRunningExtraction } from './extractionHooks';
import { useLibraryProbe, useUploadPaper } from './libraryHooks';

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    Wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

function stubFetch(routes: Record<string, { status: number; body: unknown }>) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://x');
    const hit = routes[url.pathname];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useLibraryProbe', () => {
  it('is unavailable while loading, then available when /v1/library answers', async () => {
    stubFetch({ '/v1/library': { status: 200, body: { mounted: false, paper_count: 0 } } });
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useLibraryProbe(), { wrapper: Wrapper });
    expect(result.current).toMatchObject({ available: false, isLoading: true });
    await waitFor(() => expect(result.current.available).toBe(true));
    expect(result.current.status?.mounted).toBe(false);
  });

  it('hides the tabs when the probe returns 404 (ALS)', async () => {
    stubFetch({});
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useLibraryProbe(), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.available).toBe(false);
  });
});

describe('useUploadPaper', () => {
  it('invalidates the library queries after a successful upload', async () => {
    stubFetch({ '/v1/library/papers': { status: 200, body: { paper: { id: 'p' }, duplicate: false } } });
    const { client, Wrapper } = wrapper();
    const spy = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => useUploadPaper(), { wrapper: Wrapper });
    result.current.mutate(new File(['x'], 'a.pdf', { type: 'application/pdf' }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith({ queryKey: ['library'] });
  });
});

describe('useRunningExtraction', () => {
  it('returns the running library_extract job, or null', async () => {
    const mock = stubFetch({
      '/v1/jobs': { status: 200, body: { jobs: [{ id: 'j1', kind: 'library_extract', state: 'running', progress: {} }] } },
    });
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useRunningExtraction(true, 60_000), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.data?.id).toBe('j1'));
    expect(String(mock.mock.calls[0][0])).toContain('kind=library_extract');
  });

  it('returns null when none is running', async () => {
    stubFetch({ '/v1/jobs': { status: 200, body: { jobs: [] } } });
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useRunningExtraction(true, 60_000), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.data).toBeNull());
  });
});
