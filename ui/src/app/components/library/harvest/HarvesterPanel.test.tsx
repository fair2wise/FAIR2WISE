import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TabActiveProvider } from '../../tabActivity';
import { HarvesterPanel } from './HarvesterPanel';

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('sonner', () => ({ toast: toastMock }));

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; search: string; body?: unknown }[];

const paper = (id: string, folder: string) => ({
  id, relpath: `${folder}/${id}.pdf`, folder, file_name: `${id}.pdf`, sha256: id, size_bytes: 1, title: id, doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null,
});
const job = (state: string, over: Record<string, unknown> = {}) => ({
  id: 'h1', kind: 'library_harvest', state, progress: { done: 1, total: 4 }, error: null, created_at: '', updated_at: '',
  ...over,
});
const cand = (id: string, over: Record<string, unknown> = {}) => ({
  id, title: `Paper ${id}`, year: 2021, doi: `10.1/${id}`, arxiv_id: null, source: 'arxiv', tier: 'A', score: 1,
  status: 'downloaded', landing_url: `https://doi.org/10.1/${id}`, pdf_url: null, relpath: null, detail: null, ...over,
});
const RUN = {
  id: 'h1', topic: 'polymer blends', category: 'rsoxs', schema: null, sources: ['arxiv'], max_downloads: 20,
  created_at: '2026-11-03T10:00:00Z',
  counts: { total: 2, downloaded: 1, in_library: 0, paywalled: 1, filed: 0, failed: 0, skipped: 0 },
};

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/library/papers': { status: 200, body: { papers: [paper('a', 'rsoxs'), paper('b', 'tomo')], total: 2 } },
    'GET /v1/library/schemas': {
      status: 200,
      body: { schemas: [{ name: 'matkg_schema@0.3.1', label: 'MatKG', description: null, version: '0.3.1' }] },
    },
    'GET /v1/jobs': { status: 200, body: { jobs: [] } },
    'GET /v1/library/harvest/runs': { status: 200, body: { runs: [] } },
    'GET /v1/library/harvest/runs/h1/candidates': { status: 200, body: { candidates: [] } },
    // the real handle: the id is `job_id` (the mock used `id`, which hid a 'runs/undefined' request)
    'POST /v1/jobs': { status: 200, body: { job_id: 'h1', kind: 'library_harvest', state: 'queued', submitted_at: '2026-11-03T10:00:00Z', settings_revision: null } },
    'GET /v1/jobs/h1': { status: 200, body: job('running') },
    'GET /v1/jobs/h1/logs': { status: 200, body: { logs: [] } },
    'POST /v1/jobs/h1/cancel': { status: 200, body: job('cancelled') },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    calls.push({ method, path: url.pathname, search: url.search, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const hit = routes[`${method} ${url.pathname}`];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  }));
  Object.values(toastMock).forEach(fn => fn.mockClear());
});
afterEach(() => vi.unstubAllGlobals());

function renderPanel(tabActive = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TabActiveProvider value={tabActive}>
        <HarvesterPanel />
      </TabActiveProvider>
    </QueryClientProvider>,
  );
}
const startButton = () => screen.getByRole('button', { name: 'Start harvest' });

describe('HarvesterPanel form', () => {
  it('starts disabled, looks for a running harvest and offers existing categories, schema and both sources', async () => {
    renderPanel();
    expect(await screen.findByRole('form', { name: 'Start a harvest' })).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('option', { name: 'MatKG (0.3.1)' })).toBeInTheDocument());
    expect(screen.getAllByRole('option').map(o => o.textContent)).toEqual(
      expect.arrayContaining(['Choose a category…', 'rsoxs', 'tomo', 'New category…', 'None']),
    );
    expect(screen.getByRole('checkbox', { name: 'arXiv' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'OpenAlex' })).toBeChecked();
    expect(screen.getByLabelText('Max downloads')).toHaveValue(20);
    expect(screen.getByLabelText('Schema (label only)')).toHaveValue('');
    expect(calls.some(c => c.path === '/v1/jobs' && c.search.includes('state=running') && c.search.includes('kind=library_harvest'))).toBe(true);
  });

  it('keeps Start disabled until topic, category and a source are valid', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.type(screen.getByLabelText('Topic'), 'polymer blends');
    expect(startButton()).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Category'), 'rsoxs');
    expect(startButton()).toBeEnabled();
    await user.click(screen.getByRole('checkbox', { name: 'arXiv' }));
    await user.click(screen.getByRole('checkbox', { name: 'OpenAlex' }));
    expect(startButton()).toBeDisabled();
    expect(screen.getByTestId('harvest-problem')).toHaveTextContent(/source/i);
    await user.click(screen.getByRole('checkbox', { name: 'OpenAlex' }));
    expect(startButton()).toBeEnabled();
    await user.clear(screen.getByLabelText('Max downloads'));
    await user.type(screen.getByLabelText('Max downloads'), '500');
    expect(startButton()).toBeDisabled();
  });

  it('needs a valid name for a new category', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.type(screen.getByLabelText('Topic'), 'x');
    await user.selectOptions(screen.getByLabelText('Category'), 'New category…');
    expect(startButton()).toBeDisabled();
    await user.type(screen.getByLabelText('New category name'), 'bad name');
    expect(screen.getByLabelText('New category name')).toHaveAttribute('aria-invalid', 'true');
    expect(startButton()).toBeDisabled();
    await user.clear(screen.getByLabelText('New category name'));
    await user.type(screen.getByLabelText('New category name'), 'xray');
    expect(startButton()).toBeEnabled();
  });

  it('submits the params under inputs.params and shows the run view', async () => {
    const user = userEvent.setup();
    routes['GET /v1/library/harvest/runs'].body = { runs: [RUN] };
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.type(screen.getByLabelText('Topic'), '  polymer blends ');
    await user.selectOptions(screen.getByLabelText('Category'), 'New category…');
    await user.type(screen.getByLabelText('New category name'), 'xray');
    await waitFor(() => expect(screen.getByRole('option', { name: 'MatKG (0.3.1)' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Schema (label only)'), 'matkg_schema@0.3.1');
    await user.click(screen.getByRole('checkbox', { name: 'OpenAlex' }));
    await user.clear(screen.getByLabelText('Max downloads'));
    await user.type(screen.getByLabelText('Max downloads'), '7');
    await user.click(startButton());

    expect(await screen.findByTestId('harvest-run')).toBeInTheDocument();
    const post = calls.find(c => c.method === 'POST' && c.path === '/v1/jobs');
    const body = post?.body as { kind: string; inputs: { params: Record<string, unknown> }; nonce?: unknown };
    expect(body.kind).toBe('library_harvest');
    expect(body.nonce).toBeUndefined();
    expect(body.inputs.params).toMatchObject({
      topic: 'polymer blends', category: 'xray', schema: 'matkg_schema@0.3.1', sources: ['arxiv'], max_downloads: 7,
    });
    expect(typeof body.inputs.params.nonce).toBe('string');
    expect(screen.getByTestId('harvest-state')).toHaveTextContent('running');
  });

  it('omits schema when none is chosen and toasts a refused submission', async () => {
    const user = userEvent.setup();
    routes['POST /v1/jobs'] = { status: 422, body: { detail: 'bad topic' } };
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.type(screen.getByLabelText('Topic'), 'x');
    await user.selectOptions(screen.getByLabelText('Category'), 'tomo');
    await user.click(startButton());
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('bad topic'));
    const post = calls.find(c => c.method === 'POST' && c.path === '/v1/jobs');
    expect((post?.body as { inputs: { params: Record<string, unknown> } }).inputs.params).not.toHaveProperty('schema');
    expect(screen.queryByTestId('harvest-run')).not.toBeInTheDocument();
  });
});

describe('HarvesterPanel run', () => {
  it('re-attaches to a running harvest after a reload and cancels it', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    routes['GET /v1/library/harvest/runs'].body = { runs: [RUN] };
    const user = userEvent.setup();
    renderPanel();
    expect(await screen.findByTestId('harvest-run')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /Harvesting/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.path === '/v1/jobs/h1/cancel')).toBe(true));
  });

  it('follows the log by cursor polling (tail first, then after=seq) and never opens a stream', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    const entry = (seq: number) => ({ seq, at: '2026-11-03T10:00:00Z', level: 'info', stage: 'search', message: `line ${seq}` });
    routes['GET /v1/jobs/h1/logs'] = { status: 200, body: { logs: [entry(1), entry(2)], total: 2 } };
    renderPanel();
    expect(await screen.findByText(/line 2/)).toBeInTheDocument();
    const first = calls.find(c => c.path === '/v1/jobs/h1/logs');
    expect(first?.search).toContain('tail=true');
    expect(calls.some(c => c.path.endsWith('/stream'))).toBe(false);
    expect(screen.getByText(/· polling/)).toBeInTheDocument();
  });

  it('opening a past run shows its log, with the failure of a source marked as an error', async () => {
    routes['GET /v1/library/harvest/runs'] = {
      status: 200,
      body: { runs: [{ ...RUN, id: 'p1', topic: 'plecostomus', sources: ['arxiv', 'openalex'],
        counts: { total: 0, downloaded: 0, in_library: 0, paywalled: 0, filed: 0, failed: 0, skipped: 0 },
        source_status: { arxiv: { state: 'ok', returned: 0, message: '' },
          openalex: { state: 'error', returned: 0, message: 'OpenAlex refused the request (HTTP 401).' } } }] },
    };
    routes['GET /v1/library/harvest/runs/p1/candidates'] = { status: 200, body: { candidates: [] } };
    routes['GET /v1/jobs/p1/logs'] = {
      status: 200,
      body: { logs: [{ seq: 1, at: '2026-11-03T10:00:00Z', level: 'error', stage: 'search', message: 'openalex search failed: OpenAlex refused the request (HTTP 401).' }], total: 1 },
    };
    renderPanel();
    expect(await screen.findByTestId('harvest-source-error')).toHaveTextContent('OpenAlex could not be searched');
    await userEvent.setup().click(await screen.findByRole('button', { name: /plecostomus/ }));
    expect(await screen.findByText(/openalex search failed/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Run log \(1 lines\)/ })).toBeInTheDocument();
  });

  it('drops the polling note once the run is terminal (the terminal stop is tested in useJobLogPolling)', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    routes['GET /v1/jobs/h1'] = { status: 200, body: job('succeeded') };
    renderPanel();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Harvest finished.'));
    await waitFor(() => expect(screen.queryByText(/· polling/)).not.toBeInTheDocument());
    expect(screen.getByTestId('harvest-state')).toHaveTextContent('succeeded');
  });

  it('makes no job or log request while the tab is hidden', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    renderPanel(false);
    await new Promise(resolve => setTimeout(resolve, 100));
    expect(calls.some(c => c.path.startsWith('/v1/jobs'))).toBe(false);
  });

  it('toasts and offers the form again when the run ends, keeping its results open', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    routes['GET /v1/jobs/h1'] = { status: 200, body: job('succeeded') };
    routes['GET /v1/library/harvest/runs'].body = { runs: [RUN] };
    routes['GET /v1/library/harvest/runs/h1/candidates'].body = { candidates: [cand('d')] };
    const user = userEvent.setup();
    renderPanel();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Harvest finished.'));
    expect(await screen.findByRole('table', { name: 'Harvest results' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back to the form' }));
    expect(await screen.findByRole('form', { name: 'Start a harvest' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Harvest results' })).toBeInTheDocument();
  });

  it('toasts a failed run', async () => {
    routes['GET /v1/jobs'] = { status: 200, body: { jobs: [job('running')] } };
    routes['GET /v1/jobs/h1'] = { status: 200, body: job('failed', { error: 'no source' }) };
    renderPanel();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Harvest failed: no source'));
  });
});

describe('HarvesterPanel results', () => {
  beforeEach(() => {
    routes['GET /v1/library/harvest/runs'].body = { runs: [RUN] };
    routes['GET /v1/library/harvest/runs/h1/candidates'].body = {
      candidates: [cand('d', { relpath: 'rsoxs/2021/d.pdf' }), cand('p', { status: 'paywalled' })],
    };
  });

  it('lists past runs and opens one', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    expect(await screen.findByRole('list', { name: 'Paywalled papers' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Harvest results' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Papers found for “polymer blends”/ })).toBeInTheDocument();
  });

  it('files a PDF for a paywalled candidate and toasts the new path', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = {
      status: 200,
      body: { candidate: cand('p', { status: 'filed' }), paper: { ...paper('p', 'rsoxs'), relpath: 'rsoxs/2021/saved.pdf' }, duplicate: false },
    };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), new File(['%PDF'], 'saved.pdf', { type: 'application/pdf' }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Filed saved.pdf as rsoxs/2021/saved.pdf.'));
    expect(calls.some(c => c.method === 'POST' && c.path === '/v1/library/harvest/candidates/p/pdf')).toBe(true);
  });

  it('shows the server message when filing is refused', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 409, body: { detail: 'candidate is not paywalled' } };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), new File(['%PDF'], 'saved.pdf', { type: 'application/pdf' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('candidate is not paywalled'));
  });

  it('dismisses a paywalled candidate', async () => {
    routes['POST /v1/library/harvest/candidates/p/dismiss'] = { status: 200, body: { candidate: cand('p', { status: 'dismissed' }) } };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.click(await screen.findByRole('button', { name: 'Dismiss Paper p' }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.path === '/v1/library/harvest/candidates/p/dismiss')).toBe(true));
  });

  it('Enter in the new-category field creates and selects it without starting the harvest', async () => {
    const user = userEvent.setup();
    routes['GET /v1/library/categories'] = { status: 200, body: { categories: [{ name: 'empty', count: 0 }] } };
    routes['POST /v1/library/categories'] = { status: 200, body: { name: 'xray', created: true, count: 0 } };
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.type(screen.getByLabelText('Topic'), 'polymer blends');
    await waitFor(() => expect(screen.getByRole('option', { name: 'empty' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Category'), 'New category…');
    routes['GET /v1/library/categories'].body = { categories: [{ name: 'empty', count: 0 }, { name: 'xray', count: 0 }] };
    await user.type(screen.getByLabelText('New category name'), 'xray{Enter}');
    await waitFor(() => expect(screen.getByLabelText('Category')).toHaveValue('xray'));
    expect(calls.some(c => c.method === 'POST' && c.path === '/v1/jobs')).toBe(false);
    expect(calls.find(c => c.method === 'POST' && c.path === '/v1/library/categories')?.body).toEqual({ name: 'xray' });
    expect(startButton()).toBeEnabled();
  });

  it('shows the server error for a category that cannot be created', async () => {
    const user = userEvent.setup();
    routes['POST /v1/library/categories'] = { status: 422, body: { detail: 'that name is taken by something that is not a folder' } };
    renderPanel();
    await screen.findByRole('form', { name: 'Start a harvest' });
    await user.selectOptions(screen.getByLabelText('Category'), 'New category…');
    await user.type(screen.getByLabelText('New category name'), 'taken');
    await user.click(screen.getByRole('button', { name: 'Add category' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('not a folder');
    expect(screen.getByLabelText('Category')).toHaveValue('\u0000new');
  });

  it('says OpenAlex runs without a key, at a reduced rate, only when the server says so', async () => {
    routes['GET /v1/library/harvest/runs'].body = { runs: [], openalex_keyed: false };
    renderPanel();
    expect(await screen.findByText('running without a key, reduced rate')).toBeInTheDocument();
  });

  it.each([{ runs: [], openalex_keyed: true }, { runs: [] }])('shows no key note for %j', async body => {
    routes['GET /v1/library/harvest/runs'].body = body;
    renderPanel();
    await waitFor(() => expect(calls.some(call => call.path === '/v1/library/harvest/runs')).toBe(true));
    expect(screen.queryByText('running without a key, reduced rate')).not.toBeInTheDocument();
  });
});
