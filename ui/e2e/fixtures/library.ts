import type { Page, Route } from '@playwright/test';
import { AGENT_ORIGIN, PREFIXED_API_BASE } from './agent';

/** A library paper as `GET /v1/library/papers` returns it (A8, T35). */
export interface MockPaper {
  id: string;
  relpath: string;
  folder: string;
  file_name: string;
  title: string | null;
  doi: string | null;
}

/** One mocked job: its record and the log lines `GET /v1/jobs/{id}/logs` returns. */
export interface MockJob {
  id: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress?: Record<string, number>;
  logs?: string[];
}

export interface MockLibraryOptions {
  /** Unset: `/v1/library` answers 404, like an ALS image (the tabs hide). */
  papers?: MockPaper[];
  /** `GET /v1/library/kgs` bundles (names) and plain-KG sources. */
  bundles?: string[];
  /** Models offered by `GET /v1/library/models`; empty: no backend. */
  models?: string[];
  /** Jobs the server already knows (e.g. a running extraction to re-attach to). */
  jobs?: MockJob[];
  /** Captures `POST /v1/jobs` bodies. */
  jobPosts?: Array<Record<string, unknown>>;
  /** Captures `POST /v1/library/estimate` bodies. */
  estimatePosts?: Array<Record<string, unknown>>;
  /** Captures `POST /v1/jobs/{id}/cancel` job ids. */
  cancels?: string[];
}

/** Build a paper under `folder` (`''` is the library root). */
export function mockPaper(id: string, folder: string, over: Partial<MockPaper> = {}): MockPaper {
  const file_name = `${id}.pdf`;
  return {
    id, folder, file_name, relpath: folder ? `${folder}/${file_name}` : file_name,
    title: `Title of ${id}`, doi: null, ...over,
  };
}

const json = (data: unknown, status = 200) => ({
  status, contentType: 'application/json', body: JSON.stringify(data),
});

const jobRecord = (job: MockJob) => ({
  id: job.id, kind: 'library_extract', state: job.state, progress: job.progress ?? {}, error: null,
  created_at: '2026-11-03T00:00:00Z', updated_at: '2026-11-03T00:00:00Z',
});

/**
 * Mock the A8 `/v1/library` and `/v1/jobs` routes. Register it after `mockAgent` (Playwright tries the
 * most recently registered route first), so these routes win and everything else stays mocked there.
 */
export async function mockLibrary(page: Page, options: MockLibraryOptions = {}) {
  const papers = [...(options.papers ?? [])];
  const jobs = new Map((options.jobs ?? []).map(job => [job.id, { ...job }]));
  let nextJob = 1;

  const handle = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = PREFIXED_API_BASE && url.pathname.startsWith(`${PREFIXED_API_BASE}/`)
      ? url.pathname.slice(PREFIXED_API_BASE.length)
      : url.pathname;
    if (!path.startsWith('/v1/library') && !path.startsWith('/v1/jobs')) {
      await route.fallback();
      return;
    }
    if (options.papers === undefined) {
      await route.fulfill({ status: 404, body: 'not found' });
      return;
    }

    if (path === '/v1/library' && method === 'GET') {
      await route.fulfill(json({ mounted: true, paper_count: papers.length, indexed_count: 0, quota_open: false }));
    } else if (path === '/v1/library/papers' && method === 'GET') {
      await route.fulfill(json({
        papers: papers.map(paper => ({
          ...paper, sha256: paper.id, size_bytes: 1000, verify_status: 'unverified', verified_at: null,
          index_status: 'not_indexed', pages: 10, metadata_status: 'ok',
        })),
        total: papers.length, pending_metadata: 0, truncated: false,
      }));
    } else if (path === '/v1/library/papers' && method === 'POST') {
      const name = /filename="([^"]+)"/.exec(request.postData() ?? '')?.[1] ?? 'upload.pdf';
      const paper = mockPaper(name.replace(/\.pdf$/, ''), '');
      papers.push(paper);
      await route.fulfill(json({
        paper: { ...paper, sha256: paper.id, size_bytes: 1000, verify_status: 'unverified', verified_at: null,
          index_status: 'not_indexed' },
        duplicate: false,
      }));
    } else if (path === '/v1/library/kgs' && method === 'GET') {
      await route.fulfill(json({
        bundles: (options.bundles ?? []).map(name => ({
          name, topic: null, schema: 'matkg_schema@0.3.1', model: null, backend: null, paper_count: 1,
          parent: null, reconstructed: false, updated_at: null,
        })),
        sources: [],
      }));
    } else if (path === '/v1/library/schemas') {
      await route.fulfill(json({ schemas: [
        { name: 'matkg_schema@0.3.1', label: 'MatKG', description: null, version: '0.3.1' },
      ] }));
    } else if (path === '/v1/library/models') {
      const models = options.models ?? ['cheap-model'];
      await route.fulfill(json({ available: models.length > 0, models, source: 'live', error: null, backend: 'cborg' }));
    } else if (path === '/v1/library/estimate' && method === 'POST') {
      const body = request.postDataJSON() as { paper_ids: string[]; model: string };
      options.estimatePosts?.push(body);
      await route.fulfill(json({
        pages: body.paper_ids.length * 10, paper_count: body.paper_ids.length, unknown_pages: 0, model: body.model,
        estimated_tokens: 1000, estimated_cost_usd: 0.5, confirmation: 'e2e-confirmation',
      }));
    } else if (path === '/v1/jobs' && method === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      options.jobPosts?.push(body);
      const job: MockJob = {
        id: `job${nextJob++}`, state: 'running', progress: { done: 0, total: 1 },
        logs: ['extraction started'],
      };
      jobs.set(job.id, job);
      await route.fulfill(json({ id: job.id, kind: body.kind, state: job.state }));
    } else if (path === '/v1/jobs' && method === 'GET') {
      const state = url.searchParams.get('state');
      const list = [...jobs.values()].filter(job => state === null || job.state === state);
      await route.fulfill(json({ jobs: list.map(jobRecord) }));
    } else if (/^\/v1\/jobs\/[^/]+\/logs$/.test(path)) {
      const job = jobs.get(path.split('/')[3]);
      const after = Number(url.searchParams.get('after') ?? 0);
      const logs = (job?.logs ?? []).map((message, index) => ({
        seq: index + 1, at: '2026-11-03T12:00:00Z', level: 'info', stage: 'extract', message,
      })).filter(entry => entry.seq > after);
      await route.fulfill(json({ logs }));
    } else if (/^\/v1\/jobs\/[^/]+\/stream$/.test(path)) {
      await route.fulfill({ status: 404, body: 'no stream in the mock; the tab polls the logs' });
    } else if (/^\/v1\/jobs\/[^/]+\/cancel$/.test(path) && method === 'POST') {
      const job = jobs.get(path.split('/')[3]);
      if (job) job.state = 'cancelled';
      options.cancels?.push(path.split('/')[3]);
      await route.fulfill(json(jobRecord(job ?? { id: 'none', state: 'cancelled' })));
    } else if (/^\/v1\/jobs\/[^/]+$/.test(path) && method === 'GET') {
      const job = jobs.get(path.split('/')[3]);
      await route.fulfill(job ? json(jobRecord(job)) : json({ code: 'not_found', message: 'no such job' }, 404));
    } else {
      await route.fulfill({ status: 404, body: 'not mocked' });
    }
  };

  if (PREFIXED_API_BASE) {
    await page.route(url => url.pathname.startsWith(`${PREFIXED_API_BASE}/v1/`), handle);
    return;
  }
  await page.route(`${AGENT_ORIGIN}/v1/**`, handle);
  await page.route('http://localhost:8090/v1/**', handle);
}
