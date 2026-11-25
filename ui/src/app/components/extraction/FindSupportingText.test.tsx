import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import handleFixture from '../__fixtures__/passages_job_handle.json';
import summaryFixture from '../__fixtures__/passages_recovery_summary.json';
import { FindSupportingText } from './FindSupportingText';

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; body?: Record<string, unknown> }[];

const summary = (over: Record<string, unknown> = {}) => ({
  outcome: 'running', state: 'running', started_at: null, finished_at: null, elapsed_seconds: 1,
  stage: { name: 'run', status: 'running', phase: null }, current_paper: 'a.pdf',
  papers: { total: 4, done: 1, failed: 0, skipped: 0 }, growth: {}, counters: {}, params: { bundle: 'kg1' }, error: null,
  logs: { kept: 0, total: 0, cap: 2000 }, ...over,
});
const RECOVERY = {
  total: { terms: 7, with_passage_before: 0, located_added: 5, newly_covered: 4, still_uncovered: 3, papers_processed: 2,
    papers_skipped: 2 },
  papers: {
    'a.pdf': { terms: 4, with_passage_before: 0, located_added: 3, newly_covered: 3, still_uncovered: 1 },
    'b.pdf': { terms: 3, with_passage_before: 0, located_added: 2, newly_covered: 1, still_uncovered: 2 },
    'dup.pdf': { reason: 'ambiguous_name', text: 'server text' },
    'gone.pdf': { reason: 'missing', text: 'server text' },
  },
};

function setup(name: string | null = 'kg1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><FindSupportingText name={name} /></QueryClientProvider>);
}

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/jobs': { status: 200, body: { jobs: [] } },
    'POST /v1/library/kgs/kg1/passages': {
      status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'queued', submitted_at: 'now', settings_revision: null },
    },
    'GET /v1/jobs/j9': { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'running', submitted_at: 'now' } },
    'GET /v1/jobs/j9/progress': { status: 200, body: { job_id: 'j9', summary: summary() } },
    'POST /v1/jobs/j9/cancel': { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'cancelled' } },
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

describe('FindSupportingText', () => {
  it('is disabled until a KG is chosen and shows nothing else', () => {
    setup(null);
    expect(screen.getByRole('button', { name: 'Find supporting text' })).toBeDisabled();
    expect(screen.queryByTestId('find-supporting-text')).toBeNull();
  });

  it('explains what it does in plain words', () => {
    setup();
    expect(screen.getByTestId('find-supporting-text')).toHaveTextContent(
      'Searches the source papers for the sentences that mention each term; no model is used; existing text is kept.');
  });

  it('starts the job with a fresh nonce and shows progress per paper with Cancel', async () => {
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    await waitFor(() => expect(screen.getByTestId('passages-progress')).toHaveTextContent('1 of 4 papers searched'));
    expect(screen.getByTestId('passages-progress')).toHaveTextContent('now a.pdf');
    const post = calls.find(c => c.method === 'POST' && c.path.endsWith('/passages'));
    expect(typeof post?.body?.nonce).toBe('string');
    expect(screen.getByRole('button', { name: 'Find supporting text' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(calls.some(c => c.path === '/v1/jobs/j9/cancel')).toBe(true));
  });

  it('shows the coverage block at the end: counts and the skipped papers with the reason in plain words', async () => {
    routes['GET /v1/jobs/j9'] = { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'succeeded' } };
    routes['GET /v1/jobs/j9/progress'] = {
      status: 200, body: { job_id: 'j9', summary: summary({ state: 'succeeded', outcome: 'succeeded', recovery: RECOVERY,
        papers: { total: 4, done: 4, failed: 0, skipped: 0 } }) },
    };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    const result = await screen.findByTestId('recovery-result');
    expect(screen.getByTestId('recovery-summary')).toHaveTextContent(
      'Found supporting text for 4 more terms (5 passages). 3 terms still have no supporting text. 2 papers searched, 2 skipped.');
    expect(result).toHaveTextContent('dup.pdf: Two library files have this name and the KG does not say which one was used');
    expect(result).toHaveTextContent('gone.pdf: This paper is not in the library, so it was skipped.');
    expect(result).not.toHaveTextContent('server text');   // the plain-words reason wins over the server's text
    expect(screen.queryByTestId('passages-progress')).toBeNull();
    expect(screen.getByRole('button', { name: 'Find supporting text' })).toBeEnabled();
  });

  it('says so when nothing new was found', async () => {
    routes['GET /v1/jobs/j9'] = { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'succeeded' } };
    routes['GET /v1/jobs/j9/progress'] = {
      status: 200, body: { job_id: 'j9', summary: summary({ state: 'succeeded', outcome: 'succeeded', recovery: {
        total: { ...RECOVERY.total, located_added: 0, newly_covered: 0 }, papers: {} } }) },
    };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    expect(await screen.findByTestId('recovery-summary')).toHaveTextContent('Nothing new to add.');
  });

  it('renders server text as plain text', async () => {
    routes['GET /v1/jobs/j9'] = { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'succeeded' } };
    routes['GET /v1/jobs/j9/progress'] = {
      status: 200, body: { job_id: 'j9', summary: summary({ state: 'succeeded', outcome: 'succeeded', recovery: {
        total: RECOVERY.total, papers: { '<img src=x onerror=alert(1)>.pdf': { reason: 'odd', text: '<b>bold</b>' } } } }) },
    };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    const skipped = await screen.findByTestId('recovery-skipped');
    expect(skipped.textContent).toContain('<b>bold</b>');
    expect(skipped.querySelector('b')).toBeNull();
    expect(document.querySelector('img')).toBeNull();
  });

  it.each([
    ['bundle_busy', 'Another job is working on this KG now. Try again when it ends.'],
    ['passages_in_progress', 'Supporting text is already being searched for this KG.'],
    ['no_source_papers', 'This KG does not record which papers its terms came from, so there is nothing to search.'],
  ])('answers the typed refusal %s in plain words', async (reason, text) => {
    routes['POST /v1/library/kgs/kg1/passages'] = { status: 409, body: { code: 'conflict', message: 'raw', reason } };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(screen.queryByTestId('passages-progress')).toBeNull();
  });

  it('picks up a running job of this KG after a page reload', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [
      { job_id: 'j9', kind: 'library_passages', state: 'running', submitted_at: 'now', summary: summary() },
      { job_id: 'j1', kind: 'library_passages', state: 'running', submitted_at: 'now', summary: summary({ params: { bundle: 'other' } }) },
    ] } };
    setup();
    await waitFor(() => expect(screen.getByTestId('passages-progress')).toHaveTextContent('1 of 4 papers searched'));
  });

  it('shows a cancelled run as cancelled with what was kept', async () => {
    routes['GET /v1/jobs/j9'] = { status: 200, body: { job_id: 'j9', kind: 'library_passages', state: 'cancelled' } };
    routes['GET /v1/jobs/j9/progress'] = { status: 200, body: { job_id: 'j9', summary: summary({ state: 'cancelled', outcome: 'cancelled' }) } };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    expect(await screen.findByText(/Cancelled\. The papers searched before the stop keep what was found/)).toBeInTheDocument();
  });

  it('reads the shapes the real routes answer (fixtures checked against them in tests/api_v1)', async () => {
    const id = handleFixture.job_id;
    routes['POST /v1/library/kgs/kg1/passages'] = { status: 200, body: handleFixture };
    routes[`GET /v1/jobs/${id}`] = { status: 200, body: { ...handleFixture, state: 'succeeded' } };
    routes[`GET /v1/jobs/${id}/progress`] = { status: 200, body: { job_id: id, summary: summaryFixture } };
    setup();
    await userEvent.click(screen.getByRole('button', { name: 'Find supporting text' }));
    const result = await screen.findByTestId('recovery-result');
    expect(result).toHaveTextContent('Found supporting text for 1 more term (1 passage).');
    expect(result).toHaveTextContent('blank.pdf: This PDF has no text layer');
    expect(result).toHaveTextContent('gone.pdf: This paper is not in the library, so it was skipped.');
  });
});
