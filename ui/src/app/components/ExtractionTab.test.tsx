import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExtractionTab } from './ExtractionTab';
import { TabActiveProvider } from './tabActivity';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('sonner', () => ({ toast: toastMock }));

const paper = (id: string) => ({
  id, relpath: `${id}.pdf`, folder: '', file_name: `${id}.pdf`, sha256: id, size_bytes: 1, title: `Title ${id}`, doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null,
});
// The job handle as the server sends it (`job_id`, no counters) ...
const job = (state: string, extra: Record<string, unknown> = {}) => ({
  job_id: 'j1', kind: 'library_extract', state, submitted_at: '2026-11-23T10:00:00+00:00', settings_revision: null, ...extra,
});
// ... and its live summary from GET /v1/jobs/{id}/progress.
const summary = (over: Record<string, unknown> = {}) => ({
  outcome: 'running', state: 'running', started_at: '2026-11-23T10:00:00+00:00', finished_at: null, elapsed_seconds: 61,
  stage: { name: 'run', status: 'running', phase: 'extracting' }, current_paper: 'a.pdf',
  papers: { total: 4, done: 2, failed: 0, skipped: 0 },
  growth: { entities_new: 9, entities_merged: 3, relations_new: 4, relations_merged: 1, entities_total: 12, relations_total: 5 },
  counters: { paper_pages_done: 1, paper_pages_total: 6 },
  params: { bundle: 'kg-live', model: 'm1', backend: 'cborg' }, error: null,
  logs: { kept: 1, total: 1, cap: 2000 }, ...over,
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
    'POST /v1/jobs': { status: 200, body: job('queued') },
    'GET /v1/jobs/j1': { status: 200, body: job('running') },
    'GET /v1/jobs/j1/progress': { status: 200, body: { job_id: 'j1', summary: summary() } },
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
    expect(calls.some(c => c.path === '/v1/jobs' && c.search.includes('kind=library_extract') && c.search.includes('detail=summary'))).toBe(true);
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

  it('CBORG refused this address: shows the specific reason and still lists the Ollama models', async () => {
    routes['GET /v1/library/models'].body = {
      available: false, models: [], source: 'unavailable', backend: 'cborg',
      error: 'CBORG refused this network address; connect via LBLnet/VPN or authorize the address',
      error_code: 'cborg_ip_not_authorized',
      ollama: { state: 'ok', models: ['llama3:8b', 'qwen2.5:7b'], error: null },
    };
    const user = userEvent.setup();
    renderTab();
    await screen.findByRole('checkbox', { name: /Title p1/ });
    await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('ollama:llama3:8b'));
    expect(screen.queryByTestId('no-backend')).not.toBeInTheDocument();
    expect(screen.getByTestId('model-source')).toHaveTextContent('CBORG refused this network address; connect via LBLnet/VPN');
    expect(screen.getByTestId('model-source')).toHaveTextContent('2 Ollama models');
    expect(screen.getByLabelText('Model')).toBeEnabled();
    await user.type(screen.getByLabelText(/KG name/), 'new-kg');
    await user.click(screen.getByRole('checkbox', { name: /Title p1/ }));
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await user.click(await screen.findByRole('checkbox', { name: /confirm this cost/i }));
    await user.click(startButton());
    await waitFor(() => expect(screen.getByTestId('extraction-run')).toBeInTheDocument());
    expect(calls.find(c => c.path === '/v1/library/estimate')?.body).toMatchObject({ model: 'llama3:8b' });
    expect(calls.find(c => c.method === 'POST' && c.path === '/v1/jobs')?.body).toMatchObject({
      inputs: { params: { model: 'llama3:8b', backend: 'ollama' } },
    });
  });

  it('groups CBORG and Ollama models when both are offered; a CBORG choice carries no backend', async () => {
    routes['GET /v1/library/models'].body = {
      available: true, models: ['m1'], source: 'live', error: null, backend: 'cborg',
      ollama: { state: 'ok', models: ['llama3:8b'], error: null },
    };
    const user = userEvent.setup();
    renderTab();
    await fillValidForm(user);
    const select = screen.getByLabelText('Model');
    expect(within(select).getAllByRole('group').map(g => g.getAttribute('label'))).toEqual([
      'CBORG (free models)', 'Ollama (local server)',
    ]);
    await user.click(screen.getByRole('button', { name: /estimate cost/i }));
    await user.click(await screen.findByRole('checkbox', { name: /confirm this cost/i }));
    await user.click(startButton());
    await waitFor(() => expect(screen.getByTestId('extraction-run')).toBeInTheDocument());
    const params = (calls.find(c => c.method === 'POST' && c.path === '/v1/jobs')?.body as { inputs: { params: Record<string, unknown> } }).inputs.params;
    expect(params.model).toBe('m1');
    expect('backend' in params).toBe(false);
  });

  it('with neither provider usable it says why for each', async () => {
    routes['GET /v1/library/models'].body = {
      available: false, models: [], source: 'unavailable', error: 'no key', backend: null,
      ollama: { state: 'unreachable', models: [], error: 'Ollama did not answer' },
    };
    renderTab();
    const alert = await screen.findByTestId('no-backend');
    expect(alert).toHaveTextContent('no key');
    expect(alert).toHaveTextContent('Ollama is configured but did not answer');
    expect(screen.getByLabelText('Model')).toBeDisabled();
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
  const listed = (state: string, over: Record<string, unknown> = {}) => ({
    ...job(state), summary: summary({ state, outcome: state, ...over }),
  });

  it('re-attaches to a running job after a reload and shows its progress, growth and current paper from the server', async () => {
    routes['GET /v1/jobs'].body = { jobs: [listed('running')] };
    renderTab();
    expect(await screen.findByTestId('extraction-run')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('run-progress-text')).toHaveTextContent('2 of 4 papers done (50%)'));
    expect(screen.getByTestId('run-current-paper')).toHaveTextContent('Now: a.pdf (page 1 of 6)');
    expect(screen.getByTestId('run-growth')).toHaveTextContent('9 new · 3 merged');
    expect(screen.getByRole('heading', { name: 'Extracting kg-live' })).toBeInTheDocument();
    expect(screen.queryByTestId('extraction-idle')).not.toBeInTheDocument();
  });

  it('shows the most recent finished run when none is running, with its history, and lists the runs newest first', async () => {
    routes['GET /v1/jobs'].body = {
      jobs: [
        { ...job('succeeded'), summary: summary({ state: 'succeeded', outcome: 'succeeded', current_paper: null }) },
        { ...job('failed', { job_id: 'j0' }), summary: summary({ state: 'failed', outcome: 'failed', params: { bundle: 'old-run' } }) },
      ],
    };
    routes['GET /v1/jobs/j1'].body = job('succeeded');
    routes['GET /v1/jobs/j1/progress'].body = {
      job_id: 'j1', summary: summary({ state: 'succeeded', outcome: 'succeeded', current_paper: null, logs: { kept: 2, total: 2, cap: 2000 } }),
    };
    routes['GET /v1/jobs/j1/logs'].body = {
      logs: [
        { seq: 0, at: '2026-11-23T10:00:01Z', level: 'info', stage: 'run', message: 'paper 1 of 4: extracting a.pdf' },
        { seq: 1, at: '2026-11-23T10:00:09Z', level: 'warning', stage: 'run', message: 'b.pdf: the PDF could not be read' },
      ],
      kept: 2, total: 2,
    };
    renderTab();
    expect(await screen.findByText(/paper 1 of 4: extracting a.pdf/)).toBeInTheDocument(); // history from the store
    expect(screen.getByTestId('run-state')).toHaveTextContent('Succeeded');
    const list = screen.getByRole('navigation', { name: 'Extraction runs' });
    expect(within(list).getAllByRole('button').map(b => b.textContent)).toEqual([
      expect.stringContaining('kg-live'), expect.stringContaining('old-run'),
    ]);
    // the log was fetched as a tail (cursor derived from the store), not from component state
    const logCall = calls.find(c => c.path === '/v1/jobs/j1/logs');
    expect(logCall?.search).toContain('tail=true');
  });

  it('opens another run from the list', async () => {
    const user = userEvent.setup();
    routes['GET /v1/jobs'].body = {
      jobs: [
        listed('running'),
        { ...job('failed', { job_id: 'j0' }), summary: summary({ state: 'failed', outcome: 'failed', params: { bundle: 'old-run' } }) },
      ],
    };
    routes['GET /v1/jobs/j0'] = { status: 200, body: job('failed', { job_id: 'j0' }) };
    routes['GET /v1/jobs/j0/progress'] = {
      status: 200, body: { job_id: 'j0', summary: summary({ state: 'failed', outcome: 'failed', params: { bundle: 'old-run' }, error: 'incomplete coverage', papers: { total: 3, done: 0, failed: 3, skipped: 0, failed_list: [] } }) },
    };
    routes['GET /v1/jobs/j0/logs'] = { status: 200, body: { logs: [], kept: 0, total: 0 } };
    renderTab();
    await screen.findByRole('heading', { name: 'Extracting kg-live' });
    await user.click(within(screen.getByRole('navigation', { name: 'Extraction runs' })).getByRole('button', { name: /old-run/ }));
    expect(await screen.findByRole('heading', { name: 'Extraction old-run' })).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('incomplete coverage');
  });

  it('shows log lines and cancels', async () => {
    routes['GET /v1/jobs'].body = { jobs: [listed('running')] };
    routes['GET /v1/jobs/j1/logs'].body = {
      logs: [{ seq: 0, at: '2026-11-02T10:00:00Z', level: 'info', stage: 'extract', message: 'reading p1' }], kept: 1, total: 1,
    };
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByText(/reading p1/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.path === '/v1/jobs/j1/cancel')).toBe(true));
  });

  it('shows the failed state with the error, and New extraction returns to the idle form', async () => {
    routes['GET /v1/jobs'].body = { jobs: [listed('running')] };
    routes['GET /v1/jobs/j1'].body = job('failed');
    routes['GET /v1/jobs/j1/progress'].body = {
      job_id: 'j1', summary: summary({ state: 'failed', outcome: 'failed', error: 'worker crashed', current_paper: null }),
    };
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByRole('alert')).toHaveTextContent('worker crashed');
    await user.click(screen.getByRole('button', { name: /new extraction/i }));
    expect(await screen.findByTestId('extraction-idle')).toBeInTheDocument();
  });

  it('shows the partially failed state with the failed papers', async () => {
    const papers = { total: 3, done: 2, failed: 1, skipped: 0, failed_list: [{ name: 'b.pdf', reason: 'the PDF could not be read' }] };
    routes['GET /v1/jobs'].body = { jobs: [listed('failed', { outcome: 'partially_failed', papers })] };
    routes['GET /v1/jobs/j1'].body = job('failed');
    routes['GET /v1/jobs/j1/progress'].body = {
      job_id: 'j1', summary: summary({ state: 'failed', outcome: 'partially_failed', papers, current_paper: null }),
    };
    renderTab();
    await waitFor(() => expect(screen.getByTestId('run-state')).toHaveTextContent('Partially failed (1 of 3 papers failed)'));
    expect(screen.getByTestId('failed-papers')).toHaveTextContent('b.pdf: the PDF could not be read');
  });

  it('shows the finished state', async () => {
    routes['GET /v1/jobs'].body = { jobs: [listed('running')] };
    routes['GET /v1/jobs/j1'].body = job('succeeded');
    routes['GET /v1/jobs/j1/progress'].body = {
      job_id: 'j1', summary: summary({ state: 'succeeded', outcome: 'succeeded', current_paper: null }),
    };
    renderTab();
    await waitFor(() => expect(screen.getByTestId('run-state')).toHaveTextContent('Succeeded'));
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
  });

  it('still works against a server that sends no summary yet (job state and plain progress only)', async () => {
    routes['GET /v1/jobs'].body = { jobs: [job('running')] };
    routes['GET /v1/jobs/j1/progress'] = { status: 200, body: { job_id: 'j1', phases: {} } };
    renderTab();
    expect(await screen.findByTestId('extraction-run')).toBeInTheDocument();
    expect(screen.getByTestId('run-state')).toHaveTextContent('running');
    expect(screen.getByTestId('run-progress-text')).toHaveTextContent('Waiting for progress');
  });
});

describe('ExtractionTab hidden', () => {
  it('makes no request while its tab is not the one on screen, and fetches when shown', async () => {
    routes['GET /v1/jobs'].body = { jobs: [{ ...job('running'), summary: summary() }] };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tree = (active: boolean) => (
      <QueryClientProvider client={client}>
        <TabActiveProvider value={active}>
          <ExtractionTab />
        </TabActiveProvider>
      </QueryClientProvider>
    );
    const { rerender } = render(tree(false));
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(calls.filter(c => c.path === '/v1/jobs' || c.path.startsWith('/v1/jobs/'))).toEqual([]);
    rerender(tree(true));
    expect(await screen.findByTestId('extraction-run')).toBeInTheDocument();
    expect(calls.some(c => c.path === '/v1/jobs')).toBe(true);
  });
});
