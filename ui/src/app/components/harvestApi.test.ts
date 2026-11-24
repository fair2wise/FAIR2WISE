import { describe, expect, it, vi } from 'vitest';
import jobHandle from './__fixtures__/job_handle.json';
import { ApiError } from './apiHttp';
import {
  dismissHarvestCandidate,
  fetchHarvestCandidates,
  fetchHarvestRuns,
  fileHarvestPdf,
  harvestFormProblem,
  isOpenableUrl,
  startHarvest,
} from './harvestApi';

function respond(status: number, body: unknown) {
  return vi.fn(async (_input?: unknown, _init?: unknown) => new Response(JSON.stringify(body), { status }));
}
const asFetch = (mock: unknown) => mock as unknown as typeof fetch;
const call = (mock: ReturnType<typeof respond>) => mock.mock.calls[0] as unknown as [string, RequestInit];

describe('harvestApi', () => {
  it('submits a library_harvest job with the params under inputs.params (no top-level nonce)', async () => {
    const fetcher = respond(200, { id: 'j1', kind: 'library_harvest', state: 'queued' });
    const request = { topic: 'rsoxs', category: 'rsoxs', sources: ['arxiv' as const], max_downloads: 5, nonce: 'n' };
    expect(await startHarvest(request, asFetch(fetcher))).toEqual({ id: 'j1', kind: 'library_harvest', state: 'queued' });
    const [url, init] = call(fetcher);
    expect(url).toContain('/v1/jobs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ kind: 'library_harvest', inputs: { params: request } });
  });

  it('reads the run id from the real POST /v1/jobs body, whose id is named job_id (pilot 2026-11-24)', async () => {
    // __fixtures__/job_handle.json is pinned to the real response by tests/api_v1/test_v1_library.py.
    const fetcher = respond(200, jobHandle);
    const ref = await startHarvest({ topic: 'x', category: 'c', sources: ['arxiv'], max_downloads: 1 }, asFetch(fetcher));
    expect(ref).toEqual({ id: jobHandle.job_id, kind: 'library_harvest', state: 'queued' });
    expect(ref.id).not.toBe('');
  });

  it('lists runs and a run\'s candidates', async () => {
    const runs = respond(200, { runs: [] });
    await fetchHarvestRuns(asFetch(runs));
    expect(call(runs)[0]).toContain('/v1/library/harvest/runs');
    const candidates = respond(200, { candidates: [] });
    await fetchHarvestCandidates('job 1', asFetch(candidates));
    expect(call(candidates)[0]).toContain('/v1/library/harvest/runs/job%201/candidates');
  });

  it('files a PDF as multipart field "file" and dismisses with a POST', async () => {
    const filed = respond(200, { candidate: {}, paper: {}, duplicate: false });
    await fileHarvestPdf('c1', new File(['%PDF'], 'a.pdf', { type: 'application/pdf' }), asFetch(filed));
    const [url, init] = call(filed);
    expect(url).toContain('/v1/library/harvest/candidates/c1/pdf');
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeInstanceOf(File);
    const dismissed = respond(200, { candidate: {} });
    await dismissHarvestCandidate('c1', asFetch(dismissed));
    expect(call(dismissed)[0]).toContain('/v1/library/harvest/candidates/c1/dismiss');
    expect(call(dismissed)[1].method).toBe('POST');
  });

  it('surfaces the server detail (409 for a candidate that is not paywalled)', async () => {
    const promise = fileHarvestPdf('c1', new File(['x'], 'a.pdf'), asFetch(respond(409, { detail: 'not paywalled' })));
    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ status: 409 });
  });

  it('only opens http(s) links', () => {
    expect(isOpenableUrl('https://doi.org/10.1/x')).toBe(true);
    expect(isOpenableUrl('http://arxiv.org/abs/1')).toBe(true);
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'not a url', '', null, undefined]) {
      expect(isOpenableUrl(bad)).toBe(false);
    }
  });

  it('validates the form one problem at a time', () => {
    const ok = { topic: 'x', category: 'rsoxs', sources: ['arxiv' as const], maxDownloads: 20 };
    expect(harvestFormProblem(ok)).toBeNull();
    expect(harvestFormProblem({ ...ok, topic: '  ' })).toMatch(/topic/i);
    expect(harvestFormProblem({ ...ok, topic: 'x'.repeat(201) })).toMatch(/200/);
    expect(harvestFormProblem({ ...ok, category: null })).toMatch(/category/i);
    expect(harvestFormProblem({ ...ok, sources: [] })).toMatch(/source/i);
    for (const maxDownloads of [0, 201, 1.5, Number.NaN]) {
      expect(harvestFormProblem({ ...ok, maxDownloads })).toMatch(/Max downloads/);
    }
  });
});
