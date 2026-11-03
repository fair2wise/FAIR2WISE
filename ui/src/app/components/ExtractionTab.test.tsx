import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExtractionTab } from './ExtractionTab';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('sonner', () => ({ toast: toastMock }));

const paper = (id: string) => ({
  id, relpath: `${id}.pdf`, folder: '', file_name: `${id}.pdf`, sha256: id, size_bytes: 1, title: `Title ${id}`, doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null,
});
const job = (state: string, extra: Record<string, unknown> = {}) => ({
  id: 'j1', kind: 'library_extract', state, progress: { done: 2, total: 4 }, error: null,
  created_at: '', updated_at: '', ...extra,
});

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; search: string; body?: unknown }[];

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/jobs': { status: 200, body: { jobs: [] } },
    'GET /v1/library/kgs': {
      status: 200,
      body: {
        bundles: [{ name: 'old', topic: null, schema: 'matkg_schema@0.3.1', model: null, backend: null,
          paper_count: 1, parent: null, reconstructed: false, updated_at: null }],
        sources: ['legacy.json'],
      },
    },
    'GET /v1/library/schemas': {
      status: 200,
      body: { schemas: [
        { name: 'matkg_schema@0.3.1', label: 'MatKG', description: null, version: '0.3.1' },
        { name: 'other@0.3.0', label: 'Other', description: null, version: '0.3.0' },
      ] },
    },
    'GET /v1/library/models': {
      status: 200, body: { available: true, models: ['m1', 'm2'], source: 'live', error: null, backend: 'openai' },
    },
    'GET /v1/library/papers': { status: 200, body: { papers: [paper('p1'), paper('p2')], total: 2 } },
    'POST /v1/library/estimate': {
      status: 200,
      body: { pages: 20, paper_count: 1, unknown_pages: 0, model: 'm1', estimated_tokens: 100, estimated_cost_usd: 1.5, confirmation: 'tok' },
    },
    'POST /v1/jobs': { status: 200, body: { id: 'j1', kind: 'library_extract', state: 'queued' } },
    'GET /v1/jobs/j1': { status: 200, body: job('running') },
    'GET /v1/jobs/j1/logs': { status: 200, body: { logs: [] } },
    'POST /v1/jobs/j1/cancel': { status: 200, body: job('cancelled') },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    calls.push({ method, path: url.pathname, search: url.search, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined });
    const hit = routes[`${method} ${url.pathname}`];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  }));
  toastMock.error.mockClear();
  toastMock.success.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExtractionTab />
    </QueryClientProvider>,
  );
}

const startButton = () => screen.getByRole('button', { name: /start extraction/i });

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('checkbox', { name: /Title p1/ });
  await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('m1'));
  await user.type(screen.getByLabelText(/KG name/), 'new-kg');
  await user.click(screen.getByRole('checkbox', { name: /Title p1/ }));
}

describe('ExtractionTab idle', () => {
  it('shows the idle form with Start disabled', async () => {
    renderTab();
    expect(await screen.findByTestId('extraction-idle')).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    expect(calls.some(c => c.path === '/v1/jobs' && c.search.includes('state=running') && c.search.includes('kind=library_extract'))).toBe(true);
  });

  it('keeps Start disabled until name, paper and confirmed cost are all present, then submits params', async () => {
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    expect(startButton()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    expect(await screen.findByTestId('estimate-summary')).toHaveTextContent('$1.50');
    expect(startButton()).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /confirm this cost/i }));
    expect(startButton()).toBeEnabled();
    await user.click(startButton());
    await waitFor(() => expect(screen.getByTestId('extraction-run')).toBeInTheDocument());
    const post = calls.find(c => c.method === 'POST' && c.path === '/v1/jobs');
    expect(post?.body).toMatchObject({
      kind: 'library_extract',
      inputs: { params: { bundle: 'new-kg', papers: ['p1'], model: 'm1', schema: 'matkg_schema@0.3.1', confirmation: 'tok', topic: null } },
    });
    expect(calls.some(c => c.path === '/v1/library/kgs/continue')).toBe(false);
    expect(calls.some(c => c.method === 'PUT')).toBe(false);
  });

  it('rejects an invalid name', async () => {
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await user.click(await screen.findByRole('checkbox', { name: /confirm this cost/i }));
    await user.clear(screen.getByLabelText(/KG name/));
    await user.type(screen.getByLabelText(/KG name/), '_bad name');
    expect(startButton()).toBeDisabled();
    expect(screen.getByLabelText(/KG name/)).toHaveAttribute('aria-invalid', 'true');
  });

  it('refuses a name that already exists', async () => {
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    await user.clear(screen.getByLabelText(/KG name/));
    await user.type(screen.getByLabelText(/KG name/), 'old');
    expect(screen.getByText('A KG with this name already exists.')).toBeInTheDocument();
  });

  it('drops the cost confirmation when the selection changes', async () => {
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await user.click(await screen.findByRole('checkbox', { name: /confirm this cost/i }));
    expect(startButton()).toBeEnabled();
    await user.click(screen.getByRole('checkbox', { name: /Title p2/ }));
    expect(startButton()).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /confirm this cost/i })).toBeDisabled();
  });

  it('offers continue or new as a radiogroup and locks the schema of a continued bundle', async () => {
    const user = userEvent.setup();
    renderTab();
    const group = await screen.findByRole('radiogroup', { name: 'Continue or new' });
    expect(within(group).getByRole('radio', { name: 'Start new KG' })).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => expect(screen.getByLabelText('Schema')).toBeEnabled());
    await user.click(within(group).getByRole('radio', { name: 'Continue existing KG' }));
    expect(within(group).getByRole('radio', { name: 'Continue existing KG' })).toHaveAttribute('aria-checked', 'true');
    await user.selectOptions(screen.getByLabelText('Existing KG'), 'bundle:old');
    expect(screen.getByLabelText('Schema')).toBeDisabled();
    expect(screen.getByLabelText('Schema')).toHaveValue('matkg_schema@0.3.1');
  });

  it('keeps the schema selectable when continuing a plain KG file', async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole('radio', { name: 'Continue existing KG' }));
    await user.selectOptions(screen.getByLabelText('Existing KG'), 'source:legacy.json');
    await waitFor(() => expect(screen.getByLabelText('Schema')).toBeEnabled());
  });

  it('continues a bundle through POST /kgs/continue before the job', async () => {
    const user = userEvent.setup();
    routes['POST /v1/library/kgs/continue'] = { status: 200, body: { name: 'child', warnings: ['careful'] } };
    renderTab();
    await user.click(await screen.findByRole('radio', { name: 'Continue existing KG' }));
    await user.selectOptions(screen.getByLabelText('Existing KG'), 'bundle:old');
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await user.click(await screen.findByRole('checkbox', { name: /confirm this cost/i }));
    await user.click(startButton());
    await waitFor(() => expect(screen.getByTestId('extraction-run')).toBeInTheDocument());
    expect(calls.find(c => c.path === '/v1/library/kgs/continue')?.body).toEqual({ name: 'new-kg', parent: 'old' });
    expect(toastMock.warning).toHaveBeenCalledWith('careful');
    expect(calls.find(c => c.method === 'POST' && c.path === '/v1/jobs')?.body).toMatchObject({
      inputs: { params: { bundle: 'new-kg' } },
    });
  });

  it('exports the chosen bundle with confirm: true', async () => {
    const user = userEvent.setup();
    routes['POST /v1/library/kgs/old/export'] = { status: 200, body: { name: 'old', file_name: 'matkg_old.json' } };
    renderTab();
    await user.click(await screen.findByRole('radio', { name: 'Continue existing KG' }));
    expect(screen.getByRole('button', { name: /export/i })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Existing KG'), 'bundle:old');
    await user.click(screen.getByRole('button', { name: /export/i }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Exported matkg_old.json'));
    expect(calls.find(c => c.path === '/v1/library/kgs/old/export')?.body).toEqual({ confirm: true });
  });

  it('shows the no-backend state and cannot estimate or start', async () => {
    routes['GET /v1/library/models'].body = { available: false, models: [], source: 'unavailable', error: 'no key', backend: null };
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByTestId('no-backend')).toHaveTextContent('no key');
    await user.click(await screen.findByRole('checkbox', { name: /Title p1/ }));
    expect(screen.getByLabelText('Model')).toBeDisabled();
    expect(screen.getByRole('button', { name: /estimate cost/i })).toBeDisabled();
    expect(startButton()).toBeDisabled();
  });

  it('never calls PUT /settings and reports a failed estimate', async () => {
    routes['POST /v1/library/estimate'] = { status: 422, body: { detail: 'bad schema' } };
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(calls.some(c => c.path.includes('settings'))).toBe(false);
  });
});

describe('ExtractionTab run', () => {
  it('re-attaches to a running job after a reload and shows progress', async () => {
    routes['GET /v1/jobs'].body = { jobs: [job('running')] };
    renderTab();
    expect(await screen.findByTestId('extraction-run')).toBeInTheDocument();
    expect(await screen.findByTestId('run-progress-text')).toHaveTextContent('2 of 4 papers (50%)');
    expect(screen.queryByTestId('extraction-idle')).not.toBeInTheDocument();
  });

  it('streams log lines and cancels', async () => {
    routes['GET /v1/jobs'].body = { jobs: [job('running')] };
    routes['GET /v1/jobs/j1/logs'].body = {
      logs: [{ seq: 1, at: '2026-11-02T10:00:00Z', level: 'info', stage: 'extract', message: 'reading p1' }],
    };
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByText(/reading p1/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.path === '/v1/jobs/j1/cancel')).toBe(true));
  });

  it('shows the failed state with the error, and New extraction returns to the idle form', async () => {
    routes['GET /v1/jobs'].body = { jobs: [job('running')] };
    routes['GET /v1/jobs/j1'].body = job('failed', { error: 'worker crashed' });
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByRole('alert')).toHaveTextContent('worker crashed');
    routes['GET /v1/jobs'].body = { jobs: [] };
    await user.click(screen.getByRole('button', { name: /new extraction/i }));
    expect(await screen.findByTestId('extraction-idle')).toBeInTheDocument();
  });

  it('shows the finished state', async () => {
    routes['GET /v1/jobs'].body = { jobs: [job('running')] };
    routes['GET /v1/jobs/j1'].body = job('succeeded');
    renderTab();
    await waitFor(() => expect(screen.getByTestId('run-state')).toHaveTextContent('succeeded'));
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
  });
});
