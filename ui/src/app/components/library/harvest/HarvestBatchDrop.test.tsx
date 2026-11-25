import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import filedFixture from '../../__fixtures__/harvest_filed.json';
import needsFixture from '../../__fixtures__/harvest_needs_confirmation.json';
import unmatchedFixture from '../../__fixtures__/harvest_unmatched.json';
import { HarvestBatchDrop } from './HarvestBatchDrop';

interface Call { method: string; path: string; search: string; file: string }
let calls: Call[];
let inFlight: number;
let peak: number;
let handler: (call: Call) => Promise<{ status: number; body: unknown }>;

const pdf = (name: string) => new File(['%PDF'], name, { type: 'application/pdf' });
const filedFor = (file: string, title: string) => ({
  ...filedFixture, candidate: { ...filedFixture.candidate, id: `id-${file}`, title },
});

beforeEach(() => {
  calls = [];
  inFlight = 0;
  peak = 0;
  handler = async () => ({ status: 200, body: unmatchedFixture });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const body = init?.body;
    const file = body instanceof FormData ? (body.get('file') as File).name : '';
    const call = { method: init?.method ?? 'GET', path: url.pathname, search: url.search, file };
    calls.push(call);
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await new Promise(resolve => setTimeout(resolve, 5));
    const answer = await handler(call);
    inFlight -= 1;
    return new Response(JSON.stringify(answer.body), { status: answer.status });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function setup(onFiled = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><HarvestBatchDrop runId="run1" onFiled={onFiled} /></QueryClientProvider>);
  return onFiled;
}
const input = () => screen.getByTestId('harvest-batch-input');

describe('HarvestBatchDrop', () => {
  it('sends each file as its own request to the run route, two at a time, and lists a row per file', async () => {
    setup();
    await userEvent.upload(input(), ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf', 'e.pdf'].map(pdf));
    const table = await screen.findByRole('table', { name: 'Dropped files' });
    await waitFor(() => expect(within(table).getAllByText('No match: not stored')).toHaveLength(5));
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(5);
    expect(calls.every(c => c.path === '/v1/library/harvest/runs/run1/pdf' && c.file !== '')).toBe(true);
    expect(new Set(calls.map(c => c.file)).size).toBe(5);   // one file per request, never a multi-file body
    expect(peak).toBeLessThanOrEqual(2);
    expect(peak).toBe(2);
  });

  it('rejects more than 20 files whole with a plain message and sends nothing', async () => {
    setup();
    fireEvent.change(input(), { target: { files: Array.from({ length: 21 }, (_, i) => pdf(`${i}.pdf`)) } });
    expect(await screen.findByTestId('harvest-batch-refused')).toHaveTextContent(
      'You dropped 21 files; at most 20 can be dropped at once. Nothing was sent. Drop fewer files.',
    );
    expect(calls).toHaveLength(0);
    expect(screen.queryByRole('table', { name: 'Dropped files' })).not.toBeInTheDocument();
  });

  it('accepts exactly 20 files', async () => {
    setup();
    await userEvent.upload(input(), Array.from({ length: 20 }, (_, i) => pdf(`${i}.pdf`)));
    await waitFor(() => expect(calls).toHaveLength(20));
    expect(screen.queryByTestId('harvest-batch-refused')).not.toBeInTheDocument();
  });

  it('a strong match files and is reported to the panel (for Undo); weak waits for Confirm; unmatched is never stored', async () => {
    handler = async call => {
      if (call.path.endsWith('/runs/run1/pdf')) {
        if (call.file === 'strong.pdf') return { status: 200, body: filedFor('strong', 'Strong paper') };
        if (call.file === 'weak.pdf') return { status: 200, body: { ...needsFixture, verdict: { ...needsFixture.verdict, kind: 'weak' } } };
        return { status: 200, body: unmatchedFixture };
      }
      return { status: 200, body: { ...filedFor('weak', needsFixture.candidate.title), verdict: { ...filedFixture.verdict, kind: 'weak' } } };
    };
    const onFiled = setup();
    await userEvent.upload(input(), ['strong.pdf', 'weak.pdf', 'none.pdf'].map(pdf));
    const table = await screen.findByRole('table', { name: 'Dropped files' });
    // The files are sent in turn: wait for every row's result, not only the first.
    await waitFor(() => expect(within(table).getByText('Strong match: filed')).toBeInTheDocument());
    await waitFor(() => expect(within(table).getByText('Weak match: not filed')).toBeInTheDocument());
    await waitFor(() => expect(within(table).getByText('No match: not stored')).toBeInTheDocument());
    expect(within(table).getByText('Strong paper')).toBeInTheDocument();
    expect(onFiled).toHaveBeenCalledTimes(1);
    expect(onFiled.mock.calls[0][0].name).toBe('strong.pdf');
    expect(calls.some(c => c.search !== '')).toBe(false);   // nothing confirmed yet

    await userEvent.click(within(table).getByRole('button', { name: 'Confirm weak.pdf' }));
    await waitFor(() => expect(within(table).getByText('Filed (confirmed by you)')).toBeInTheDocument());
    const confirm = calls.find(c => c.search !== '');
    expect(confirm).toMatchObject({ method: 'POST', path: `/v1/library/harvest/candidates/${needsFixture.candidate.id}/pdf`, search: '?confirm=true' });
    expect(onFiled).toHaveBeenCalledTimes(2);
    expect(within(table).queryByRole('button', { name: 'Confirm weak.pdf' })).not.toBeInTheDocument();
  });

  it('a failed file does not stop the others and shows its own error', async () => {
    handler = async call => (call.file === 'big.pdf'
      ? { status: 413, body: { detail: 'upload exceeds the 52428800 byte limit' } }
      : { status: 200, body: unmatchedFixture });
    setup();
    await userEvent.upload(input(), ['a.pdf', 'big.pdf', 'c.pdf', 'd.pdf'].map(pdf));
    const table = await screen.findByRole('table', { name: 'Dropped files' });
    await waitFor(() => expect(within(table).getByText(/Failed: File too large/)).toBeInTheDocument());
    await waitFor(() => expect(within(table).getAllByText('No match: not stored')).toHaveLength(3));
    expect(calls).toHaveLength(4);
  });

  it('lists a non-PDF as not sent without a request', async () => {
    setup();
    fireEvent.change(input(), { target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' }), pdf('a.pdf')] } });
    const table = await screen.findByRole('table', { name: 'Dropped files' });
    expect(within(table).getByText('Not sent: only PDF files can be filed')).toBeInTheDocument();
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].file).toBe('a.pdf');
  });

  it('accepts files dropped on the area', async () => {
    setup();
    fireEvent.drop(screen.getByTestId('harvest-batch').firstElementChild as HTMLElement, { dataTransfer: { files: [pdf('a.pdf')] } });
    await waitFor(() => expect(calls).toHaveLength(1));
  });
});
