/** Test helpers of the schema panel: a route-table fetch stub and a query-client wrapper (test code only). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { vi } from 'vitest';
import diffFixture from '../__fixtures__/schema_diff.json';
import generateFixture from '../__fixtures__/schema_generate.json';
import previewEstimateFixture from '../__fixtures__/schema_preview_estimate.json';
import previewResultFixture from '../__fixtures__/schema_preview_result.json';
import reportOk from '../__fixtures__/schema_report_ok.json';
import saveFixture from '../__fixtures__/schema_save.json';
import storedFixture from '../__fixtures__/schema_stored.json';
import userListFixture from '../__fixtures__/schema_user_list.json';

export { diffFixture, generateFixture, previewEstimateFixture, previewResultFixture, reportOk, saveFixture, storedFixture, userListFixture };

export interface Hit { status: number; body: unknown }
export type Route = Hit | ((body: unknown, url: URL) => Hit | Promise<Hit>);
export interface Call { method: string; path: string; search: string; body?: unknown; signal?: AbortSignal | null }

export const paper = (id: string) => ({
  id, relpath: `${id}.pdf`, folder: '', file_name: `${id}.pdf`, sha256: id, size_bytes: 1, title: `Title ${id}`, doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null,
});

/** Default routes: schema routes present, one user schema with two versions, two models, two papers. */
export function defaultRoutes(): Record<string, Route> {
  return {
    'GET /v1/capabilities': { status: 200, body: { role: 'local_operator', profile: 'local' } },
    'GET /v1/library/schemas/user': { status: 200, body: userListFixture },
    'GET /v1/library/schemas': { status: 200, body: { schemas: [{ name: 'matkg_schema@0.3.1', label: 'MatKG', description: null, version: '0.3.1' }] } },
    'GET /v1/library/models': { status: 200, body: { available: true, models: ['lbl/cborg-chat', 'm2'], source: 'live', error: null, backend: 'cborg' } },
    'GET /v1/library/papers': { status: 200, body: { papers: [paper('p1'), paper('p2')], total: 2 } },
    'POST /v1/library/schemas/validate': { status: 200, body: reportOk },
  };
}

/** Install a fetch stub over `routes`; `calls` records every request. */
export function stubFetch(routes: Record<string, Route>) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: url.pathname, search: url.search, body, signal: init?.signal });
    const route = routes[`${method} ${url.pathname}`];
    if (!route) return new Response('{}', { status: 404 });
    const hit = typeof route === 'function' ? await route(body, url) : route;
    return new Response(JSON.stringify(hit.body), { status: hit.status });
  }));
  return calls;
}

export function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
