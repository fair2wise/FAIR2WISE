import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { canEditNode } from './capabilitiesApi';
import { useCapabilities } from './capabilitiesHooks';

const FULL = { role: 'user', profile: 'als', can_edit: false, can_save: false, can_upload: false, can_run_operations: true };

afterEach(() => vi.unstubAllGlobals());

describe('useCapabilities', () => {
  function wrap() {
    const client = new QueryClient();
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  it('exposes data on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(FULL), { status: 200 })));
    const { result } = renderHook(() => useCapabilities(), { wrapper: wrap() });
    await waitFor(() => expect(result.current?.can_save).toBe(false));
  });

  it.each([404, 401, 500])('stays undefined (keeps showing) on HTTP %i', async status => {
    const fetchMock = vi.fn(async () => new Response('{}', { status }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useCapabilities(), { wrapper: wrap() });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(result.current).toBeUndefined();
    expect(canEditNode('tiled', result.current)).toBe(true);
  });

  it('stays undefined on a network error', async () => {
    const fetchMock = vi.fn(async () => { throw new TypeError('down'); });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useCapabilities(), { wrapper: wrap() });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(canEditNode('tiled', result.current)).toBe(true);
  });
});
