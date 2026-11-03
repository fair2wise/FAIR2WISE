import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryTab } from './LibraryTab';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const paper = (over: Record<string, unknown> = {}) => ({
  id: 'p1', relpath: 'a.pdf', folder: '', file_name: 'a.pdf', sha256: 'x', size_bytes: 1, title: 'T', doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null, ...over,
});

interface Route { status: number; body: unknown }
let routes: Record<string, Route>;
let calls: { method: string; path: string; search: string; body?: unknown }[];

beforeEach(() => {
  calls = [];
  routes = {
    'GET /v1/library': { status: 200, body: { mounted: true, paper_count: 1, indexed_count: null, quota_open: false } },
    'GET /v1/library/papers': { status: 200, body: { papers: [paper()], total: 1 } },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    calls.push({ method, path: url.pathname, search: url.search, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const hit = routes[`${method} ${url.pathname}`];
    return hit ? new Response(JSON.stringify(hit.body), { status: hit.status }) : new Response('{}', { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LibraryTab />
    </QueryClientProvider>,
  );
}

const pdf = () => new File(['%PDF'], 'new.pdf', { type: 'application/pdf' });

describe('LibraryTab', () => {
  it('lists papers and shows the indexed count only when the backend reports it', async () => {
    renderTab();
    expect(await screen.findByText('a.pdf')).toBeInTheDocument();
    expect(screen.queryByText(/^\d+ indexed$/)).not.toBeInTheDocument();
  });

  it('groups papers by top-level folder and filters the table by one', async () => {
    routes['GET /v1/library/papers'].body = {
      papers: [
        paper({ id: 'r1', file_name: 'r1.pdf', folder: 'rsoxs', relpath: 'rsoxs/r1.pdf' }),
        paper({ id: 't1', file_name: 't1.pdf', folder: 'tomo', relpath: 'tomo/t1.pdf' }),
      ],
      total: 2,
    };
    renderTab();
    expect(await screen.findByText('r1.pdf')).toBeInTheDocument();
    expect(screen.getByText('t1.pdf')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /tomo \(1\)/ }));
    expect(screen.queryByText('r1.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('t1.pdf')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: /All/ }));
    expect(screen.getByText('r1.pdf')).toBeInTheDocument();
  });

  it('hides the folder filter when there is only one folder', async () => {
    renderTab();
    expect(await screen.findByText('a.pdf')).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Folder' })).not.toBeInTheDocument();
  });

  it('shows the indexed count when reported', async () => {
    routes['GET /v1/library'].body = { mounted: true, paper_count: 1, indexed_count: 4, quota_open: false };
    renderTab();
    expect(await screen.findByText('4 indexed')).toBeInTheDocument();
  });

  it('shows the not-mounted state, disables actions and does not fetch papers', async () => {
    routes['GET /v1/library'].body = { mounted: false, paper_count: 0, indexed_count: null, quota_open: false };
    renderTab();
    expect(await screen.findByText(/not mounted/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify all' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Index for QA' })).toBeDisabled();
    expect(calls.some(c => c.path === '/v1/library/papers')).toBe(false);
  });

  it('shows the empty state', async () => {
    routes['GET /v1/library/papers'].body = { papers: [], total: 0 };
    renderTab();
    expect(await screen.findByText(/no papers yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify all' })).toBeDisabled();
  });

  it('rejects a non-PDF drop client-side without calling the server', async () => {
    renderTab();
    await screen.findByText('a.pdf');
    fireEvent.drop(screen.getByRole('button', { name: /upload pdf papers/i }), {
      dataTransfer: { files: [new File(['x'], 'n.txt', { type: 'text/plain' })] },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PDF files');
    expect(calls.some(c => c.method === 'POST')).toBe(false);
  });

  it('uploads a PDF and refetches the list', async () => {
    routes['POST /v1/library/papers'] = { status: 200, body: { paper: paper(), duplicate: false } };
    renderTab();
    await screen.findByText('a.pdf');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    await waitFor(() => expect(calls.filter(c => c.path === '/v1/library/papers' && c.method === 'GET').length).toBe(2));
  });

  it.each([
    [415, 'Not a PDF'],
    [413, 'File too large'],
    [422, 'bad file'],
  ])('shows the server message for upload HTTP %i', async (status, text) => {
    routes['POST /v1/library/papers'] = { status, body: { detail: 'bad file' } };
    renderTab();
    await screen.findByText('a.pdf');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
  });

  it('files an upload under the chosen category and creates a new one on demand', async () => {
    routes['GET /v1/library/papers'].body = {
      papers: [paper({ id: 'r1', file_name: 'r1.pdf', folder: 'rsoxs', relpath: 'rsoxs/r1.pdf' }), paper()],
      total: 2,
    };
    routes['POST /v1/library/papers'] = {
      status: 200,
      body: { paper: paper({ relpath: 'xray/2020/new.pdf', folder: 'xray' }), duplicate: false,
        manifest: { updated: true, path: 'xray/manifest.json', detail: 'added to the manifest' } },
    };
    renderTab();
    await screen.findByText('r1.pdf');
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    expect(screen.getByRole('button', { name: /upload pdf papers/i })).toHaveAttribute('aria-disabled', 'true');
    await userEvent.type(screen.getByLabelText('New category name'), 'xray');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.search === '?folder=xray')).toBe(true));
  });

  it('uploads to the library root when no category is chosen', async () => {
    routes['POST /v1/library/papers'] = { status: 200, body: { paper: paper(), duplicate: false } };
    renderTab();
    await screen.findByText('a.pdf');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.search === '')).toBe(true));
  });

  it('reports a duplicate upload via toast', async () => {
    const { toast } = await import('sonner');
    routes['POST /v1/library/papers'] = { status: 200, body: { paper: paper(), duplicate: true } };
    renderTab();
    await screen.findByText('a.pdf');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(expect.stringContaining('already in the library')));
  });

  it('switches to the not-mounted state when an upload answers 501', async () => {
    routes['POST /v1/library/papers'] = { status: 501, body: { detail: 'no mount' } };
    renderTab();
    await screen.findByText('a.pdf');
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf());
    expect(await screen.findByRole('status')).toHaveTextContent(/not mounted/i);
    expect(screen.getByRole('button', { name: 'Index for QA' })).toBeDisabled();
  });

  it('verifies one paper via POST .../verify', async () => {
    routes['POST /v1/library/papers/p1/verify'] = { status: 200, body: { paper: paper({ verify_status: 'verified' }) } };
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: 'Verify a.pdf' }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.path === '/v1/library/papers/p1/verify')).toBe(true));
  });

  it('warns when a verify returns quota_open (HTTP 200)', async () => {
    const { toast } = await import('sonner');
    routes['POST /v1/library/papers/p1/verify'] = { status: 200, body: { paper: paper({ verify_status: 'quota_open' }) } };
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: 'Verify a.pdf' }));
    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
  });

  it('Verify all submits a job with the nonce under inputs.params, then follows it to completion', async () => {
    const { toast } = await import('sonner');
    routes['POST /v1/jobs'] = { status: 200, body: { id: 'j1', kind: 'library_verify', state: 'queued' } };
    routes['GET /v1/jobs/j1'] = {
      status: 200,
      body: { id: 'j1', kind: 'library_verify', state: 'succeeded', progress: {}, error: null, created_at: '', updated_at: '' },
    };
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: 'Verify all' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Verify all finished.'));
    const post = calls.find(c => c.method === 'POST' && c.path === '/v1/jobs');
    expect(post?.body).toMatchObject({ kind: 'library_verify', inputs: { params: { nonce: expect.any(String) } } });
  });

  it('Index for QA submits library_index and disables actions while the job runs', async () => {
    routes['POST /v1/jobs'] = { status: 200, body: { id: 'j2', kind: 'library_index', state: 'queued' } };
    routes['GET /v1/jobs/j2'] = {
      status: 200,
      body: { id: 'j2', kind: 'library_index', state: 'running', progress: {}, error: null, created_at: '', updated_at: '' },
    };
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: 'Index for QA' }));
    expect(await screen.findByText('Indexing papers…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verify all' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Verify a.pdf' })).toBeDisabled();
    expect(calls.find(c => c.path === '/v1/jobs')?.body).toMatchObject({ kind: 'library_index' });
  });

  it('shows the quota notice and a truncation note when the server reports them', async () => {
    routes['GET /v1/library'].body = { mounted: true, paper_count: 1, indexed_count: null, quota_open: true };
    routes['GET /v1/library/papers'].body = { papers: [paper()], total: 5, truncated: true };
    renderTab();
    expect(await screen.findByText(/quota is exhausted/i)).toBeInTheDocument();
    expect(await screen.findByText('Showing 1 of 5 papers.')).toBeInTheDocument();
  });

  it('labels the section for assistive tech', async () => {
    renderTab();
    expect(screen.getByRole('region', { name: 'Library' })).toBeInTheDocument();
    await screen.findByText('a.pdf');
  });
});
