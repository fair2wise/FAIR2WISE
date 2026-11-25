import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import handleFixture from '../__fixtures__/relations_job_handle.json';
import summaryFixture from '../__fixtures__/relations_repair_summary.json';
import { RepairRelations, repairCounts, repairSentence } from './RepairRelations';

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; body?: Record<string, unknown> }[];

const running = () => ({ ...summaryFixture, state: 'running', outcome: 'running', counters: {} });

function setup(name: string | null = 'kg1', schema: string | null = 'user/plecos@v1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RepairRelations name={name} schema={schema} /></QueryClientProvider>);
}

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/jobs': { status: 200, body: { jobs: [] } },
    'POST /v1/library/kgs/kg1/relations': { status: 200, body: { ...handleFixture, job_id: 'j9' } },
    'GET /v1/jobs/j9': { status: 200, body: { job_id: 'j9', kind: 'library_relations', state: 'running', submitted_at: 'now' } },
    'GET /v1/jobs/j9/progress': { status: 200, body: { job_id: 'j9', summary: running() } },
    'POST /v1/jobs/j9/cancel': { status: 200, body: { job_id: 'j9', kind: 'library_relations', state: 'cancelled' } },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : undefined;
    calls.push({ method, path: url.pathname, body });
    const hit = routes[`${method} ${url.pathname}`];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe('RepairRelations', () => {
  it('renders nothing for a KG on a shipped schema or with nothing chosen', () => {
    setup('kg1', 'matkg_schema@0.3.1');
    expect(screen.queryByRole('button', { name: 'Repair relations' })).toBeNull();
    setup(null, 'user/plecos@v1');
    expect(screen.queryByRole('button', { name: 'Repair relations' })).toBeNull();
  });

  it('starts the job with a fresh nonce, shows it running with Cancel, then one sentence of counts', async () => {
    setup();
    expect(screen.getByTestId('repair-relations')).toHaveTextContent('No model is used; the previous files are kept.');
    await userEvent.click(screen.getByRole('button', { name: 'Repair relations' }));
    await waitFor(() => expect(screen.getByTestId('relations-progress')).toHaveTextContent('Repairing the relations of kg1'));
    const post = calls.find(c => c.method === 'POST' && c.path.endsWith('/relations'));
    expect(typeof post?.body?.nonce).toBe('string');
    expect(screen.getByRole('button', { name: 'Repair relations' })).toBeDisabled();
    routes['GET /v1/jobs/j9'] = { status: 200, body: { job_id: 'j9', kind: 'library_relations', state: 'succeeded' } };
    routes['GET /v1/jobs/j9/progress'] = { status: 200, body: { job_id: 'j9', summary: summaryFixture } };
    const result = await screen.findByTestId('relations-result', undefined, { timeout: 4000 });
    expect(result).toHaveTextContent('1 relation was the wrong way round and now read from the right term; 2 were values and are now stored as properties of the term; 1 left unverified; 3 terms changed.');
    expect(result).toHaveTextContent('one generation back');
    expect(screen.getByRole('button', { name: 'Repair relations' })).toBeEnabled();
  });

  it('cancels through the job route', async () => {
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Repair relations' }));
    await screen.findByTestId('relations-progress');
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(calls.some(c => c.path === '/v1/jobs/j9/cancel')).toBe(true));
  });

  it.each([
    ['not_user_schema', 'This KG is on a shipped schema; its relations are already judged the way that schema defines them.'],
    ['bundle_busy', 'Another job is working on this KG now. Try again when it ends.'],
    ['relations_in_progress', 'The relations of this KG are already being repaired.'],
  ])('answers the typed refusal %s in plain words', async (reason, text) => {
    routes['POST /v1/library/kgs/kg1/relations'] = { status: 409, body: { code: 'conflict', message: 'raw', reason } };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Repair relations' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
  });

  it('reads the counters of the real summary shape and words a no-op run', () => {
    const counts = repairCounts(summaryFixture as never);
    expect(counts).toMatchObject({ swapped: 1, attributes: 2, invalid: 1, relations: 6 });
    expect(repairSentence({ relations: 6, swapped: 0, attributes: 0, invalid: 1, unknown_object: 1, unchanged: 4, terms_changed: 0 }))
      .toBe('Nothing to change: 6 relations judged, 1 left unverified (they fit the schema neither way), 1 point at a term this KG does not have.');
    expect(repairCounts(null)).toBeNull();
  });
});
