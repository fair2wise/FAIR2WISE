import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import filedFixture from '../../__fixtures__/harvest_filed.json';
import needsFixture from '../../__fixtures__/harvest_needs_confirmation.json';
import undoFixture from '../../__fixtures__/harvest_undo.json';
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
    expect(screen.getByLabelText('Schema to record with the run')).toHaveValue('');
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
    await user.selectOptions(screen.getByLabelText('Schema to record with the run'), 'matkg_schema@0.3.1');
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
    expect(await screen.findByRole('list', { name: 'Papers needing a PDF' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Harvest results' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Papers found for “polymer blends”/ })).toBeInTheDocument();
  });

  // The real answers of POST .../candidates/{id}/pdf, pinned by tests/coverage_t27/test_manual_pdf_routes.py.
  const filedFor = (id: string, over: Record<string, unknown> = {}) => ({
    ...filedFixture,
    candidate: { ...filedFixture.candidate, id, title: `Paper ${id}`, status: 'filed' },
    paper: { ...filedFixture.paper, relpath: 'rsoxs/2021/saved.pdf' },
    ...over,
  });
  const needsFor = (id: string, kind: 'weak' | 'mismatch', over: Record<string, unknown> = {}) => ({
    ...needsFixture,
    candidate: { id, title: `Paper ${id}`, doi: `10.1/${id}` },
    verdict: { ...needsFixture.verdict, kind },
    other_candidate: null,
    ...over,
  });
  const saved = () => new File(['%PDF'], 'saved.pdf', { type: 'application/pdf' });
  const post = (path: string) => calls.filter(c => c.method === 'POST' && c.path === path);

  it('files a strong match at once, says what was filed against which paper, and offers Undo', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p') };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    const notice = await screen.findByRole('status');
    expect(notice).toHaveTextContent('Filed saved.pdf against Paper p (strong match)');
    expect(within(notice).getByRole('button', { name: 'Undo filing of saved.pdf' })).toBeInTheDocument();
    expect(screen.queryByTestId('harvest-confirm-dialog')).not.toBeInTheDocument();
    expect(post('/v1/library/harvest/candidates/p/pdf')[0].search).toBe('');
  });

  it('Undo calls the undo route, refetches the run and the Papers table, and removes the note', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p') };
    routes['POST /v1/library/harvest/candidates/p/undo'] = {
      status: 200, body: { ...undoFixture, candidate: { ...undoFixture.candidate, id: 'p', title: 'Paper p' } },
    };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    await user.click(await screen.findByRole('button', { name: 'Undo filing of saved.pdf' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo filing of saved.pdf' })).not.toBeInTheDocument());
    expect(post('/v1/library/harvest/candidates/p/undo')).toHaveLength(1);
    expect(toastMock.success).toHaveBeenCalledWith('Undone: saved.pdf was removed and Paper p is back in the queue.');
    const candidateReads = calls.filter(c => c.method === 'GET' && c.path === '/v1/library/harvest/runs/h1/candidates').length;
    expect(candidateReads).toBeGreaterThanOrEqual(3);   // opened, after filing, after undo
    expect(calls.filter(c => c.method === 'GET' && c.path === '/v1/library/papers').length).toBeGreaterThanOrEqual(3);
  });

  it('a failed undo says why and drops the stale note', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p') };
    routes['POST /v1/library/harvest/candidates/p/undo'] = { status: 409, body: { detail: 'no file supplied by the user is filed for this candidate' } };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    await user.click(await screen.findByRole('button', { name: 'Undo filing of saved.pdf' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('Could not undo: no file supplied by the user is filed for this candidate'));
    expect(screen.queryByRole('button', { name: 'Undo filing of saved.pdf' })).not.toBeInTheDocument();
  });

  it('a file already in the library is reported and offers no Undo', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p', { duplicate: true }) };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    await waitFor(() => expect(toastMock.info).toHaveBeenCalledWith('saved.pdf is already in the library (rsoxs/2021/saved.pdf).'));
    expect(screen.queryByRole('button', { name: /Undo filing/ })).not.toBeInTheDocument();
  });

  it('a weak match stores nothing, asks in a dialog and files only after "File anyway" (confirm=true)', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: needsFor('p', 'weak', { verdict: { ...needsFixture.verdict, kind: 'weak', reason: 'the PDF has no readable text', found_dois: [], found_title: '' } }) };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    const dialog = await screen.findByTestId('harvest-confirm-dialog');
    expect(within(dialog).getByText('Check this PDF before it is filed')).toBeInTheDocument();
    expect(within(within(dialog).getByTestId('confirm-paper')).getByText('Paper p')).toBeInTheDocument();
    expect(within(within(dialog).getByTestId('confirm-pdf')).getByText('No title found')).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Undo filing/ })).not.toBeInTheDocument();
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p', { verdict: { ...needsFixture.verdict, kind: 'weak' } }) };
    await user.click(within(dialog).getByRole('button', { name: 'File anyway' }));
    await waitFor(() => expect(screen.queryByTestId('harvest-confirm-dialog')).not.toBeInTheDocument());
    expect(post('/v1/library/harvest/candidates/p/pdf').map(c => c.search)).toEqual(['', '?confirm=true']);
    expect(await screen.findByRole('status')).toHaveTextContent('Filed saved.pdf against Paper p (confirmed by you)');
  });

  it('a mismatch shows both DOIs and titles side by side with a warning and needs an explicit tick', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: needsFor('p', 'mismatch') };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    const dialog = await screen.findByTestId('harvest-confirm-dialog');
    expect(within(dialog).getByText('This PDF names a different paper')).toBeInTheDocument();
    const paperSide = within(dialog).getByTestId('confirm-paper');
    const pdfSide = within(dialog).getByTestId('confirm-pdf');
    expect(paperSide).toHaveTextContent('Paper p');
    expect(paperSide).toHaveTextContent('DOI: 10.1/p');
    expect(pdfSide).toHaveTextContent(needsFixture.verdict.found_title);
    expect(pdfSide).toHaveTextContent(`DOI: ${needsFixture.verdict.found_dois[0]}`);
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/not the DOI of the paper in the queue/);
    const fileAnyway = within(dialog).getByRole('button', { name: 'File anyway' });
    expect(fileAnyway).toBeDisabled();
    await user.click(within(dialog).getByRole('checkbox', { name: /I have checked/ }));
    expect(fileAnyway).toBeEnabled();
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('p', { verdict: { ...needsFixture.verdict, kind: 'mismatch' } }) };
    await user.click(fileAnyway);
    expect(await screen.findByRole('status')).toHaveTextContent('(mismatch confirmed by you)');
    expect(post('/v1/library/harvest/candidates/p/pdf').map(c => c.search)).toEqual(['', '?confirm=true']);
  });

  it('cancelling the dialog stores nothing and sends no second request', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: needsFor('p', 'mismatch') };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    await user.click(within(await screen.findByTestId('harvest-confirm-dialog')).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByTestId('harvest-confirm-dialog')).not.toBeInTheDocument());
    expect(post('/v1/library/harvest/candidates/p/pdf')).toHaveLength(1);
  });

  it('offers to file under the other open paper whose DOI the PDF carries, and re-targets with to=', async () => {
    routes['GET /v1/library/harvest/runs/h1/candidates'].body = {
      candidates: [cand('p', { status: 'paywalled' }), cand('q', { status: 'blocked', title: 'The other paper' })],
    };
    routes['POST /v1/library/harvest/candidates/p/pdf'] = {
      status: 200,
      body: needsFor('p', 'mismatch', { other_candidate: { id: 'q', title: 'The other paper', doi: '10.1/q', status: 'blocked' } }),
    };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    const dialog = await screen.findByTestId('harvest-confirm-dialog');
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: filedFor('q', { candidate: { ...filedFixture.candidate, id: 'q', title: 'The other paper', status: 'filed' } }) };
    await user.click(within(dialog).getByRole('button', { name: 'File it under The other paper instead' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Filed saved.pdf against The other paper (strong match)');
    expect(post('/v1/library/harvest/candidates/p/pdf').map(c => c.search)).toEqual(['', '?to=q']);
  });

  it('no button when the PDF names no other open paper', async () => {
    routes['POST /v1/library/harvest/candidates/p/pdf'] = { status: 200, body: needsFor('p', 'mismatch') };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    await user.upload(await screen.findByTestId('harvest-file-p'), saved());
    const dialog = await screen.findByTestId('harvest-confirm-dialog');
    expect(within(dialog).queryByRole('button', { name: /instead/ })).not.toBeInTheDocument();
  });

  it.each(['paywalled', 'blocked', 'failed'])('a %s paper gets Open at publisher, the drop zone and Dismiss', async status => {
    routes['GET /v1/library/harvest/runs/h1/candidates'].body = {
      candidates: [cand('x', { status, open_url: 'https://ezproxy.example.edu/login?url=https://doi.org/10.1/x' })],
    };
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: /polymer blends/ }));
    const link = await screen.findByRole('link', { name: 'Open at publisher: Paper x' });
    expect(link).toHaveAttribute('href', 'https://ezproxy.example.edu/login?url=https://doi.org/10.1/x');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByTestId('harvest-file-x')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss Paper x' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Needs a PDF (1 left)' })).toBeInTheDocument();
    expect(screen.getByTestId('harvest-rights-note')).toHaveTextContent(/responsibility to confirm/);
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
