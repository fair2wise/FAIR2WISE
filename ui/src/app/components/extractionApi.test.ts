import { describe, expect, it, vi } from 'vitest';
import {
  cancelJob,
  continueKg,
  isValidKgName,
  estimateExtraction,
  exportKgBundle,
  fetchExtractionModels,
  fetchJobLogs,
  fetchJobs,
  fetchJobSummary,
  normalizeJob,
  fetchKgBundles,
  startExtraction,
  streamJobEvents,
} from './extractionApi';
import type { SseBlock } from './data/liveAgent';

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}
const asFetch = (mock: unknown) => mock as unknown as typeof fetch;
const call = (mock: ReturnType<typeof vi.fn>, i = 0) => mock.mock.calls[i] as unknown as [string, RequestInit];

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('extractionApi', () => {
  it('lists bundles and models', async () => {
    const bundles = respond(200, { bundles: [] });
    await fetchKgBundles(asFetch(bundles));
    expect(call(bundles)[0]).toContain('/v1/library/kgs');
    const models = respond(200, { available: false, models: [], source: 'unavailable', error: 'no backend' });
    expect((await fetchExtractionModels(asFetch(models))).available).toBe(false);
    expect(call(models)[0]).toContain('/v1/library/models');
  });

  it('posts the estimate request', async () => {
    const fetcher = respond(200, { pages: 10, model: 'm', estimated_tokens: 1, estimated_cost_usd: 0.1, confirmation: 'c' });
    await estimateExtraction({ paper_ids: ['a'], model: 'm', schema: 's' }, asFetch(fetcher));
    const [url, init] = call(fetcher);
    expect(url).toContain('/v1/library/estimate');
    expect(JSON.parse(init.body as string)).toEqual({ paper_ids: ['a'], model: 'm', schema: 's' });
  });

  it('starts a library_extract job with the model in the inputs, never PUT /settings', async () => {
    const fetcher = respond(200, { job_id: 'j', kind: 'library_extract', state: 'queued', submitted_at: '2026-11-23T10:00:00+00:00' });
    const started = await startExtraction(
      {
        bundle: 'kg1', topic: 't', schema: 's', model: 'm',
        papers: ['a'], confirmation: 'c', nonce: 'n',
      },
      asFetch(fetcher),
    );
    const [url, init] = call(fetcher);
    expect(url).toContain('/v1/jobs');
    expect(url).not.toContain('settings');
    expect(JSON.parse(init.body as string)).toEqual({
      kind: 'library_extract',
      inputs: {
        params: {
          bundle: 'kg1', topic: 't', schema: 's', model: 'm', papers: ['a'],
          confirmation: 'c', nonce: 'n',
        },
      },
    });
    expect(started).toEqual({ id: 'j', kind: 'library_extract', state: 'queued' }); // the server's job_id becomes id
  });

  it('normalizes the server handle (job_id, submitted_at, optional summary) and the older id shape', () => {
    const summary = { papers: { total: 4, done: 3, failed: 1, skipped: 0 }, error: 'incomplete coverage' };
    expect(normalizeJob({ job_id: 'a', kind: 'k', state: 'failed', submitted_at: 'T', summary })).toEqual({
      id: 'a', kind: 'k', state: 'failed', progress: { done: 3, total: 4 }, error: 'incomplete coverage',
      created_at: 'T', updated_at: 'T', summary,
    });
    expect(normalizeJob({ id: 'b', kind: 'k', state: 'running', progress: { done: 1, total: 2 }, error: null, created_at: 'c', updated_at: 'u' }))
      .toMatchObject({ id: 'b', progress: { done: 1, total: 2 }, created_at: 'c', updated_at: 'u' });
    expect(normalizeJob({ job_id: 'c', kind: 'k', state: 'queued' }).progress).toEqual({});
  });

  it('asks for the runs list with the compact summary, newest first, bounded', async () => {
    const fetcher = respond(200, { jobs: [{ job_id: 'j1', kind: 'library_extract', state: 'running' }] });
    const { jobs } = await fetchJobs({ kind: 'library_extract', detail: 'summary', limit: 20 }, asFetch(fetcher));
    const url = new URL(call(fetcher)[0], 'http://x');
    expect(url.searchParams.get('detail')).toBe('summary');
    expect(url.searchParams.get('limit')).toBe('20');
    expect(jobs[0].id).toBe('j1');
  });

  it('reads the live summary from the progress route, null when the server sends none', async () => {
    const withSummary = respond(200, { job_id: 'j1', summary: { outcome: 'running' } });
    expect(await fetchJobSummary('j 1', asFetch(withSummary))).toEqual({ outcome: 'running' });
    expect(call(withSummary)[0]).toContain('/v1/jobs/j%201/progress');
    expect(await fetchJobSummary('j1', asFetch(respond(200, { job_id: 'j1', phases: {} })))).toBeNull();
  });

  it('fetches the log tail with a limit, and from the start with after=-1 (the first line has seq 0)', async () => {
    const logs = respond(200, { logs: [], kept: 0, total: 0 });
    await fetchJobLogs('j1', undefined, asFetch(logs), { limit: 1000, tail: true });
    const url = new URL(call(logs)[0], 'http://x');
    expect(url.searchParams.get('after')).toBe('-1');
    expect(url.searchParams.get('limit')).toBe('1000');
    expect(url.searchParams.get('tail')).toBe('true');
  });

  it('filters running jobs by state and kind (reload re-attach)', async () => {
    const fetcher = respond(200, { jobs: [] });
    await fetchJobs({ state: 'running', kind: 'library_extract' }, asFetch(fetcher));
    const url = new URL(call(fetcher)[0], 'http://x');
    expect(url.pathname).toBe('/v1/jobs');
    expect(url.searchParams.get('state')).toBe('running');
    expect(url.searchParams.get('kind')).toBe('library_extract');
  });

  it('fetches logs after a sequence number, cancels and exports', async () => {
    const logs = respond(200, { logs: [] });
    await fetchJobLogs('j 1', 7, asFetch(logs));
    expect(call(logs)[0]).toContain('/v1/jobs/j%201/logs?after=7');
    const cancel = respond(200, {});
    await cancelJob('j1', asFetch(cancel));
    expect(call(cancel)[0]).toContain('/v1/jobs/j1/cancel');
    expect(call(cancel)[1].method).toBe('POST');
    const exp = respond(200, { name: 'kg', file_name: 'matkg_kg.json' });
    await exportKgBundle('kg', asFetch(exp));
    expect(call(exp)[0]).toContain('/v1/library/kgs/kg/export');
    expect(JSON.parse(call(exp)[1].body as string)).toEqual({ confirm: true });
  });

  it('continues a bundle or a source file', async () => {
    const fetcher = respond(200, { name: 'child', warnings: ['w'] });
    const res = await continueKg({ name: 'child', parent: 'kg' }, asFetch(fetcher));
    expect(res.warnings).toEqual(['w']);
    const [url, init] = call(fetcher);
    expect(url).toContain('/v1/library/kgs/continue');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ name: 'child', parent: 'kg' });
  });

  it('validates KG names', () => {
    expect(isValidKgName('kg_1-a')).toBe(true);
    expect(isValidKgName('a'.repeat(64))).toBe(true);
    expect(isValidKgName('a'.repeat(65))).toBe(false);
    expect(isValidKgName('_kg')).toBe(false);
    expect(isValidKgName('')).toBe(false);
    expect(isValidKgName('a b')).toBe(false);
  });

  it('surfaces a refused export', async () => {
    const fetcher = respond(400, { detail: 'outside release dir' });
    await expect(exportKgBundle('kg', asFetch(fetcher))).rejects.toMatchObject({ message: 'outside release dir', status: 400 });
  });

  it('streams job events through the shared SSE reader and sends Last-Event-ID', async () => {
    const fetcher = vi.fn(async () =>
      sseResponse([
        'id: 5\nevent: job_log\ndata: {"seq":5,"message":"he',
        'llo"}\n\nevent: progress\ndata: {"done":1}\n\n',
      ]),
    );
    const events: SseBlock[] = [];
    await streamJobEvents('j1', { onEvent: block => events.push(block), lastEventId: '4', fetcher: asFetch(fetcher) });
    expect(events).toEqual([
      { event: 'job_log', id: '5', data: { seq: 5, message: 'hello' } },
      { event: 'progress', data: { done: 1 } },
    ]);
    const [url, init] = call(fetcher as unknown as ReturnType<typeof vi.fn>);
    expect(url).toContain('/v1/jobs/j1/stream');
    expect((init.headers as Record<string, string>)['Last-Event-ID']).toBe('4');
  });
});
