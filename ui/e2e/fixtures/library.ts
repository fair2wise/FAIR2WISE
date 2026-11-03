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
  /** Job kind; defaults to `library_extract`. */
  kind?: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress?: Record<string, number>;
  logs?: string[];
}

/** A harvest candidate as `GET /v1/library/harvest/runs/{id}/candidates` returns it (A8, T45). */
export interface MockHarvestCandidate {
  id: string;
  title: string;
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  source: string;
  tier: 'A' | 'B' | 'C';
  score: number;
  status: 'downloaded' | 'in_library' | 'paywalled' | 'filed' | 'failed' | 'skipped' | 'dismissed';
  landing_url: string | null;
  pdf_url: string | null;
  relpath: string | null;
  detail: string | null;
}

/** A harvest run the server already has (its job id is the run id). */
export interface MockHarvestRun {
  id: string;
  topic: string;
  category: string;
  candidates: MockHarvestCandidate[];
}

/** Build a harvest candidate; `status` defaults to `downloaded`. */
export function mockCandidate(id: string, over: Partial<MockHarvestCandidate> = {}): MockHarvestCandidate {
  return {
    id, title: `Paper ${id}`, year: 2021, doi: `10.1/${id}`, arxiv_id: null, source: 'openalex', tier: 'A', score: 0.9,
    status: 'downloaded', landing_url: `https://doi.org/10.1/${id}`, pdf_url: null, relpath: null, detail: null, ...over,
  };
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
  /** Captures uploads: file name and the `folder` query parameter (`''` when absent). */
  uploads?: Array<{ name: string; folder: string }>;
  /** Harvest runs the server already has. */
  harvestRuns?: MockHarvestRun[];
  /** Candidates every newly started harvest finds (default: none). */
  harvestFinds?: MockHarvestCandidate[];
  /** Captures `POST .../harvest/candidates/{id}/pdf`: candidate id and file name. */
  harvestFiles?: Array<{ id: string; name: string }>;
  /** Captures `POST .../harvest/candidates/{id}/dismiss` candidate ids. */
  harvestDismisses?: string[];
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
  id: job.id, kind: job.kind ?? 'library_extract', state: job.state, progress: job.progress ?? {}, error: null,
  created_at: '2026-11-03T00:00:00Z', updated_at: '2026-11-03T00:00:00Z',
});

/**
 * Mock the A8 `/v1/library` and `/v1/jobs` routes. Register it after `mockAgent` (Playwright tries the
 * most recently registered route first), so these routes win and everything else stays mocked there.
 */
export async function mockLibrary(page: Page, options: MockLibraryOptions = {}) {
  const papers = [...(options.papers ?? [])];
  const jobs = new Map((options.jobs ?? []).map(job => [job.id, { ...job }]));
  const runs = new Map<string, MockHarvestRun & { schema: string | null; sources: string[]; max_downloads: number }>(
    (options.harvestRuns ?? []).map(run => [run.id, { ...run, schema: null, sources: ['arxiv', 'openalex'], max_downloads: 20 }]),
  );
  const find = (candidateId: string) => {
    for (const run of runs.values()) {
      const candidate = run.candidates.find(item => item.id === candidateId);
      if (candidate) return { run, candidate };
    }
    return null;
  };
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
      const folder = url.searchParams.get('folder') ?? '';
      options.uploads?.push({ name, folder });
      const base = mockPaper(name.replace(/\.pdf$/, ''), folder);
      // The server files a category upload under a year folder (`unknown` when the year cannot be read).
      const paper = folder ? { ...base, relpath: `${folder}/unknown/${name}` } : base;
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
      const kind = String(body.kind);
      const params = ((body.inputs as { params?: Record<string, unknown> } | undefined)?.params ?? {});
      const job: MockJob = {
        id: `job${nextJob++}`, kind, state: 'running', progress: { done: 0, total: 1 },
        logs: [kind === 'library_harvest' ? 'harvest started' : 'extraction started'],
      };
      jobs.set(job.id, job);
      if (kind === 'library_harvest') {
        runs.set(job.id, {
          id: job.id, topic: String(params.topic), category: String(params.category),
          schema: (params.schema as string | undefined) ?? null, sources: (params.sources as string[]) ?? [],
          max_downloads: Number(params.max_downloads ?? 20),
          candidates: (options.harvestFinds ?? []).map(item => ({ ...item })),
        });
      }
      await route.fulfill(json({ id: job.id, kind: body.kind, state: job.state }));
    } else if (path === '/v1/jobs' && method === 'GET') {
      const state = url.searchParams.get('state');
      const kind = url.searchParams.get('kind');
      const list = [...jobs.values()].filter(job => (state === null || job.state === state)
        && (kind === null || (job.kind ?? 'library_extract') === kind));
      await route.fulfill(json({ jobs: list.map(jobRecord) }));
    } else if (path === '/v1/library/harvest/runs' && method === 'GET') {
      const list = [...runs.values()].reverse().map(run => {
        const count = (status: string) => run.candidates.filter(item => item.status === status).length;
        return {
          id: run.id, topic: run.topic, category: run.category, schema: run.schema, sources: run.sources,
          max_downloads: run.max_downloads, created_at: '2026-11-03T12:00:00Z',
          counts: {
            total: run.candidates.length, downloaded: count('downloaded'), in_library: count('in_library'),
            paywalled: count('paywalled'), filed: count('filed'), failed: count('failed'), skipped: count('skipped'),
            dismissed: count('dismissed'),
          },
        };
      });
      await route.fulfill(json({ runs: list }));
    } else if (/^\/v1\/library\/harvest\/runs\/[^/]+\/candidates$/.test(path)) {
      await route.fulfill(json({ candidates: runs.get(path.split('/')[5])?.candidates ?? [] }));
    } else if (/^\/v1\/library\/harvest\/candidates\/[^/]+\/pdf$/.test(path) && method === 'POST') {
      const hit = find(path.split('/')[5]);
      if (!hit || hit.candidate.status !== 'paywalled') {
        await route.fulfill(json({ detail: 'candidate is not paywalled' }, 409));
        return;
      }
      const name = /filename="([^"]+)"/.exec(request.postData() ?? '')?.[1] ?? 'saved.pdf';
      options.harvestFiles?.push({ id: hit.candidate.id, name });
      hit.candidate.status = 'filed';
      hit.candidate.relpath = `${hit.run.category}/${hit.candidate.year ?? 'unknown'}/${name}`;
      const paper = mockPaper(name.replace(/\.pdf$/, ''), hit.run.category, { relpath: hit.candidate.relpath });
      papers.push(paper);
      await route.fulfill(json({
        candidate: hit.candidate,
        paper: { ...paper, sha256: paper.id, size_bytes: 1000, verify_status: 'unverified', verified_at: null,
          index_status: 'not_indexed' },
        duplicate: false,
      }));
    } else if (/^\/v1\/library\/harvest\/candidates\/[^/]+\/dismiss$/.test(path) && method === 'POST') {
      const hit = find(path.split('/')[5]);
      if (!hit || hit.candidate.status !== 'paywalled') {
        await route.fulfill(json({ detail: 'candidate is not paywalled' }, 409));
        return;
      }
      options.harvestDismisses?.push(hit.candidate.id);
      hit.candidate.status = 'dismissed';
      await route.fulfill(json({ candidate: hit.candidate }));
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
