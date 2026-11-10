import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PublishToTiled } from './PublishToTiled';

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; body?: Record<string, unknown> }[];

const PREVIEW = {
  confirmed: false, name: 'demo', corpus: 'demo', snapshot_id: 'v2', snapshot_ref: 'demo/v2',
  target: 'http://127.0.0.1:8765', release_file: 'matkg_demo.json', previous: null, review_page: 'Pending Approvals',
  problems: [],
};
const STARTED = { ...PREVIEW, confirmed: true, job_id: 'p1', kind: 'library_publish', state: 'queued', reused: false };
const view = (over: Record<string, unknown> = {}, job = 'running') => ({
  job: { job_id: 'p1', state: job }, ui_state: job,
  library_publish: {
    bundle: 'demo', corpus: 'demo', snapshot_id: 'v2', snapshot_ref: 'demo/v2', target: 'http://127.0.0.1:8765',
    stages: { export: 'completed', adopt: 'completed' }, publication_job_id: null, publication_state: null, plan: null,
    review_page: 'Pending Approvals', failure: null, ...over,
  },
});

function setup(name: string | null = 'demo') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublishToTiled name={name} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/capabilities': { status: 200, body: { role: 'local_operator', can_publish: true } },
    'POST /v1/library/kgs/demo/publish': { status: 200, body: PREVIEW },
    'GET /v1/jobs/p1/view': { status: 200, body: view() },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : undefined;
    calls.push({ method, path: url.pathname, body });
    let hit = routes[`${method} ${url.pathname}`];
    if (method === 'POST' && body?.confirm === true && routes['CONFIRM']) hit = routes['CONFIRM'];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe('PublishToTiled', () => {
  it.each([
    ['an older server without the field', { status: 200, body: { role: 'local_operator' } }],
    ['can_publish false (ALS or no publication)', { status: 200, body: { can_publish: false } }],
    ['a 403', { status: 403, body: { code: 'denied', message: 'no' } }],
    ['a 404', { status: 404, body: {} }],
  ])('is hidden for %s', async (_label, route) => {
    routes['GET /v1/capabilities'] = route;
    setup();
    await waitFor(() => expect(calls.some(c => c.path === '/v1/capabilities')).toBe(true));
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(screen.queryByRole('button', { name: 'Publish to Tiled' })).not.toBeInTheDocument();
  });

  it('is disabled until a bundle is chosen', async () => {
    setup(null);
    expect(await screen.findByRole('button', { name: 'Publish to Tiled' })).toBeDisabled();
  });

  it('previews, states what will happen and that approval is still needed, then submits and links to Pending Approvals', async () => {
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    const dialog = await screen.findByTestId('publish-dialog');
    expect(await screen.findByTestId('publish-ref')).toHaveTextContent('demo/v2');
    expect(dialog).toHaveTextContent('matkg_demo.json');
    expect(dialog).toHaveTextContent('The plan still needs your approval');
    expect(calls.filter(c => c.path.endsWith('/publish')).map(c => c.body)).toEqual([{ confirm: false, again: false }]);

    routes['CONFIRM'] = { status: 200, body: STARTED };
    const confirmButtons = screen.getAllByRole('button', { name: 'Publish to Tiled' });
    await userEvent.click(confirmButtons[confirmButtons.length - 1]);
    await screen.findByTestId('publish-progress');
    expect(calls.filter(c => c.path.endsWith('/publish')).pop()?.body).toEqual({ confirm: true, again: false });

    routes['GET /v1/jobs/p1/view'] = {
      status: 200,
      body: view({
        stages: { export: 'completed', adopt: 'completed', stage: 'completed', plan: 'completed' },
        publication_job_id: 'pub1', publication_state: 'staged',
        plan: { plan_id: 'pl', plan_sha256: 'abcdef0123456789'.repeat(4), creates: 5, links: 3, supersessions: 0 },
      }, 'succeeded'),
    };
    const success = await screen.findByTestId('publish-success', undefined, { timeout: 4000 });
    expect(success).toHaveTextContent('demo/v2');
    expect(success).toHaveTextContent('abcdef012345');
    expect(success).toHaveTextContent('5 creates, 3 links');
    expect(success).toHaveTextContent('Nothing is published yet');
    expect(screen.getByRole('link', { name: 'Open Pending Approvals' })).toHaveAttribute('href', '/approvals');
    // nothing on this surface approves: no approval or publications route was called
    expect(calls.some(c => c.path.includes('/approval') || c.path.startsWith('/v1/publications'))).toBe(false);
  }, 10000);

  it('shows preview problems and keeps Confirm disabled', async () => {
    routes['POST /v1/library/kgs/demo/publish'] = {
      status: 200,
      body: { ...PREVIEW, problems: [{ reason: 'no_commit_label', code: 'unsupported_capability',
        message: 'no_commit_label: this image has no commit label (F2W_CODE_COMMIT)' }] },
    };
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This image has no commit label');
    const buttons = screen.getAllByRole('button', { name: 'Publish to Tiled' });
    expect(buttons[buttons.length - 1]).toBeDisabled();
  });

  it('shows the refused disclosure rule from a failed adopt stage', async () => {
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    await screen.findByTestId('publish-ref');
    routes['CONFIRM'] = { status: 200, body: STARTED };
    routes['GET /v1/jobs/p1/view'] = {
      status: 200,
      body: view({
        stages: { export: 'completed', adopt: 'failed' },
        failure: { stage: 'adopt', reason: 'disclosure_refused', code: 'invalid_input',
          message: 'disclosure_refused: the commit-ready disclosure check refused this KG (R7 x2); nothing was adopted' },
      }, 'failed'),
    };
    const buttons = screen.getAllByRole('button', { name: 'Publish to Tiled' });
    await userEvent.click(buttons[buttons.length - 1]);
    const alert = await screen.findByRole('alert', undefined, { timeout: 4000 });
    expect(alert).toHaveTextContent('Refused by the disclosure check');
    expect(alert).toHaveTextContent('R7 x2');
    expect(screen.queryByTestId('publish-success')).not.toBeInTheDocument();
  }, 10000);

  it('shows a typed error of the confirmed call (a plan already pending)', async () => {
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    await screen.findByTestId('publish-ref');
    routes['CONFIRM'] = { status: 409, body: { code: 'conflict', reason: 'plan_pending', retryable: false,
      message: 'plan_pending: a publication plan for corpus demo already waits for approval' } };
    const buttons = screen.getAllByRole('button', { name: 'Publish to Tiled' });
    await userEvent.click(buttons[buttons.length - 1]);
    expect(await screen.findByRole('alert')).toHaveTextContent('already pending');
  });

  it('offers a new version when this exact KG was published before', async () => {
    routes['POST /v1/library/kgs/demo/publish'] = {
      status: 200, body: { ...PREVIEW, previous: { job_id: 'old1', state: 'succeeded' } },
    };
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Publish a new version anyway' }));
    await waitFor(() => expect(calls.filter(c => c.path.endsWith('/publish')).pop()?.body).toEqual({ confirm: false, again: true }));
  });

  it('hides the button when the preview answers 404', async () => {
    routes['POST /v1/library/kgs/demo/publish'] = { status: 404, body: {} };
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Publish to Tiled' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Publish to Tiled' })).not.toBeInTheDocument());
  });
});
