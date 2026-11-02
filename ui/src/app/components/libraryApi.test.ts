import { describe, expect, it, vi } from 'vitest';
import { ApiError } from './apiHttp';
import {
  fetchLibraryPapers,
  fetchLibraryStatus,
  indexLibraryPapers,
  isPdfFile,
  normalizeLibraryStatus,
  uploadLibraryPaper,
  verifyAllLibraryPapers,
  verifyLibraryPaper,
} from './libraryApi';

function respond(status: number, body: unknown) {
  return vi.fn(async (_input?: unknown, _init?: unknown) => new Response(JSON.stringify(body), { status }));
}
const asFetch = (mock: unknown) => mock as unknown as typeof fetch;

describe('libraryApi', () => {
  it('reads the status and normalizes it', async () => {
    const fetcher = respond(200, { mounted: true, paper_count: 3, indexed_count: 1, quota_open: false });
    const status = await fetchLibraryStatus(asFetch(fetcher));
    expect(status).toEqual({ mounted: true, paper_count: 3, indexed_count: 1, quota_open: false });
    expect(String(fetcher.mock.calls[0][0])).toContain('/v1/library');
  });

  it('returns null (hide the tabs) for 401/403/404/405 and for a network failure', async () => {
    for (const status of [401, 403, 404, 405]) {
      expect(await fetchLibraryStatus(asFetch(respond(status, { detail: 'nope' })))).toBeNull();
    }
    const down = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(await fetchLibraryStatus(asFetch(down))).toBeNull();
  });

  it('throws ApiError for a server error', async () => {
    await expect(fetchLibraryStatus(asFetch(respond(500, { detail: 'boom' })))).rejects.toBeInstanceOf(ApiError);
  });

  it('normalizes garbage to a safe status', () => {
    expect(normalizeLibraryStatus(null)).toEqual({ mounted: false, paper_count: 0, indexed_count: 0, quota_open: false });
  });

  it('lists papers', async () => {
    const fetcher = respond(200, { papers: [], total: 0 });
    expect(await fetchLibraryPapers(asFetch(fetcher))).toEqual({ papers: [], total: 0 });
    expect(String(fetcher.mock.calls[0][0])).toContain('/v1/library/papers');
  });

  it('uploads a PDF as multipart field "file" with a POST', async () => {
    const fetcher = respond(200, { paper: { id: 'p1' }, duplicate: false });
    const file = new File(['%PDF-1.4'], 'a.pdf', { type: 'application/pdf' });
    await uploadLibraryPaper(file, asFetch(fetcher));
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/v1/library/papers');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
  });

  it('surfaces the server detail on an upload error', async () => {
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });
    const promise = uploadLibraryPaper(file, asFetch(respond(415, { detail: 'Not a PDF' })));
    await expect(promise).rejects.toMatchObject({ message: 'Not a PDF', status: 415 });
  });

  it('verifies one paper with an encoded id', async () => {
    const fetcher = respond(200, { paper: { id: 'a/b' } });
    await verifyLibraryPaper('a/b', asFetch(fetcher));
    expect(String(fetcher.mock.calls[0][0])).toContain('/v1/library/papers/a%2Fb/verify');
  });

  it('submits verify-all and index as jobs, with a nonce', async () => {
    const fetcher = respond(200, { id: 'j1', kind: 'library_verify', state: 'queued' });
    await verifyAllLibraryPapers('n1', asFetch(fetcher));
    await indexLibraryPapers(undefined, asFetch(fetcher));
    const first = JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    const second = JSON.parse((fetcher.mock.calls[1] as unknown as [string, RequestInit])[1].body as string);
    expect(first).toMatchObject({ kind: 'library_verify', nonce: 'n1' });
    expect(second.kind).toBe('library_index');
  });

  it('detects PDFs by MIME type or extension', () => {
    expect(isPdfFile({ name: 'x.PDF', type: '' })).toBe(true);
    expect(isPdfFile({ name: 'x', type: 'application/pdf' })).toBe(true);
    expect(isPdfFile({ name: 'x.txt', type: 'text/plain' })).toBe(false);
  });
});
