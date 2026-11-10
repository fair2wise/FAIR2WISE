import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './apiHttp';
import {
  actionUrl,
  approvalErrorMessage,
  fetchPendingApprovals,
  groupByKind,
  normalizeAction,
  normalizeItem,
  normalizePending,
  performApprovalAction,
  planMergeApply,
  shortHash,
} from './approvalsApi';

const SHA = 'a'.repeat(64);

const PLAN_ITEM = {
  kind: 'publication', id: 'job-1', job_id: 'job-1', job_kind: 'publish', corpus_scope: ['rsoxs'],
  target: 'http://127.0.0.1:8765/api#f2w', snapshot_ref: 'rsoxs/v1', reason: null,
  plan: { plan_id: 'p1', plan_sha256: SHA, snapshot_id: 'v1', base_revision: 'r0', creates: 3, links: 2, supersessions: 0, affected: 3 },
  submitted_at: '2026-11-09T10:00:00+00:00', requested_by: 'local-operator',
  approve: { method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } },
  reject: { method: 'POST', path: 'v1/jobs/job-1/cancel', body: null },
};

const MERGE_ITEM = {
  kind: 'merge_apply', id: 'merge:rsoxs', corpus: 'rsoxs', accepted_cases: 2, case_ids: ['c1', 'c2'], plan: null,
  plan_route: { method: 'POST', path: 'v1/curation/merges/apply', body: { corpus: 'rsoxs', route: 'apply_migration', dry_run: true } },
  approve: { method: 'POST', path: 'v1/curation/merges/apply', body: { corpus: 'rsoxs', route: 'apply_migration', dry_run: false, plan_sha256: null } },
  reject: null,
};

afterEach(() => vi.unstubAllGlobals());

describe('normalizePending', () => {
  it('keeps the typed fields and drops items without a kind or id', () => {
    const body = normalizePending({ items: [PLAN_ITEM, { kind: 'x' }, 'junk', { id: 'y' }], counts: { publication: 1, bad: 'x' },
      total: 1, includes: { plans: true, merge_apply: 'no' }, generated_at: '2026-11-09T10:00:00+00:00' });
    expect(body.items).toHaveLength(1);
    expect(body.items[0]).toMatchObject({ kind: 'publication', id: 'job-1', corpus_scope: ['rsoxs'], requested_by: 'local-operator' });
    expect(body.items[0].plan).toEqual(PLAN_ITEM.plan);
    expect(body.items[0].approve).toEqual(PLAN_ITEM.approve);
    expect(body.items[0].reject).toEqual({ method: 'POST', path: 'v1/jobs/job-1/cancel', body: null });
    expect(body.counts).toEqual({ publication: 1 });
    expect(body.includes).toEqual({ plans: true });
    expect(body.total).toBe(1);
  });

  it('tolerates a malformed body', () => {
    expect(normalizePending(null)).toEqual({ items: [], counts: {}, total: 0, includes: {}, generated_at: null });
    expect(normalizePending({ items: 'x', total: 'y' }).total).toBe(0);
    expect(normalizeItem({ kind: 'publication' })).toBeNull();
    expect(normalizeAction({ method: 'post', path: '' })).toBeNull();
    expect(normalizeAction({ method: 'post', path: 'v1/x', body: 'not an object' })).toEqual({ method: 'POST', path: 'v1/x', body: null });
  });

  it('keeps the merge dry-run route and the cost-control routes', () => {
    const merge = normalizeItem(MERGE_ITEM)!;
    expect(merge.plan_route?.body).toEqual({ corpus: 'rsoxs', route: 'apply_migration', dry_run: true });
    expect(merge.reject).toBeNull();
    const cost = normalizeItem({ kind: 'cost_control', id: 'cost-control', approve: null, reject: null, enabled: true, budget_cap_usd: 0.5,
      spend: { cap_exceeded: true }, routes: [{ method: 'PUT', path: 'v1/curation/cost-control', body: { enabled: true } }, 'junk'] })!;
    expect(cost.routes).toEqual([{ method: 'PUT', path: 'v1/curation/cost-control', body: { enabled: true } }]);
    expect(cost.enabled).toBe(true);
  });
});

describe('groupByKind', () => {
  it('groups in the fixed order and lists unknown kinds last', () => {
    const items = normalizePending({ items: [
      { kind: 'review_case', id: 'c1' }, { kind: 'something_new', id: 'n1' }, PLAN_ITEM, { kind: 'rollback', id: 'r1' },
    ] }).items;
    const groups = groupByKind(items);
    expect(groups.map(g => [g.kind, g.items.length])).toEqual([['publication', 1], ['rollback', 1], ['review_case', 1], ['something_new', 1]]);
    expect(groups[0].label).toBe('Publication plans');
    expect(groups[3].label).toBe('something_new');
    expect(groupByKind([])).toEqual([]);
  });
});

describe('actions', () => {
  it('builds the URL from the API base and the relative path the server gave', () => {
    expect(actionUrl({ method: 'POST', path: 'v1/publications/job-1/approval', body: null })).toMatch(/\/v1\/publications\/job-1\/approval$/);
    expect(actionUrl({ method: 'POST', path: '/v1/jobs/j/cancel', body: null })).toMatch(/[^/]\/v1\/jobs\/j\/cancel$/);
  });

  it('sends the published body with the override merged, or no body at all', async () => {
    const fetcher = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    await performApprovalAction({ method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } }, {}, fetcher as unknown as typeof fetch);
    let [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/publications\/job-1\/approval$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ plan_sha256: SHA });
    expect(init.credentials).toBe('same-origin');

    await performApprovalAction({ method: 'POST', path: 'v1/jobs/job-1/cancel', body: null }, {}, fetcher as unknown as typeof fetch);
    [url, init] = fetcher.mock.calls[1] as unknown as [string, RequestInit];
    expect(init.body).toBeUndefined();

    await performApprovalAction({ method: 'POST', path: 'v1/curation/merges/apply', body: { corpus: 'r', dry_run: false, plan_sha256: null } },
      { plan_sha256: SHA }, fetcher as unknown as typeof fetch);
    [url, init] = fetcher.mock.calls[2] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ corpus: 'r', dry_run: false, plan_sha256: SHA });
  });

  it('rejects with the typed code and reason of the body', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ code: 'stale_approval', message: 'the plan changed', retryable: false }), { status: 409 }));
    const error = await performApprovalAction(PLAN_ITEM.approve, {}, fetcher as unknown as typeof fetch).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: 'stale_approval', message: 'the plan changed' });
    const gated = vi.fn(async () => new Response(JSON.stringify({ code: 'unsupported_capability', message: 'gate closed', reason: 'gate_closed' }), { status: 501 }));
    const refused = await performApprovalAction(PLAN_ITEM.approve, {}, gated as unknown as typeof fetch).catch(e => e);
    expect(refused).toMatchObject({ status: 501, code: 'unsupported_capability', reason: 'gate_closed' });
  });

  it('planMergeApply returns the dry-run hash', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ report: { plan_sha256: SHA, dry_run: true } }), { status: 200 }));
    const item = normalizeItem(MERGE_ITEM)!;
    expect(await planMergeApply(item, fetcher as unknown as typeof fetch)).toBe(SHA);
    const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ corpus: 'rsoxs', route: 'apply_migration', dry_run: true });
    const empty = vi.fn(async () => new Response(JSON.stringify({ report: { plan_sha256: null } }), { status: 200 }));
    expect(await planMergeApply(item, empty as unknown as typeof fetch)).toBeNull();
    expect(await planMergeApply({ ...item, plan_route: null })).toBeNull();
  });
});

describe('fetchPendingApprovals', () => {
  it('reads /v1/approvals/pending with same-origin credentials', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ items: [PLAN_ITEM], total: 1, counts: { publication: 1 }, includes: {} }), { status: 200 }));
    const body = await fetchPendingApprovals(fetcher as unknown as typeof fetch);
    expect(body.total).toBe(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/approvals\/pending$/);
    expect(init.credentials).toBe('same-origin');
  });

  it.each([401, 403, 404])('rejects with the status on HTTP %i', async status => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ code: 'denied', message: 'no' }), { status }));
    await expect(fetchPendingApprovals(fetcher as unknown as typeof fetch)).rejects.toMatchObject({ status });
  });
});

describe('approvalErrorMessage', () => {
  it('names the typed bodies', () => {
    expect(approvalErrorMessage(new ApiError('the plan changed', 409, 'stale_approval'))).toMatch(/plan changed since it was shown \(stale_approval\)/);
    expect(approvalErrorMessage(new ApiError('job is owned', 409, 'conflict'))).toBe('Conflict (conflict): job is owned');
    expect(approvalErrorMessage(new ApiError('missing capability', 403, 'denied'))).toBe('Not permitted (denied): missing capability');
    expect(approvalErrorMessage(new ApiError('gate', 501, 'unsupported_capability', 'gate_closed')))
      .toBe('Not supported on this deployment (unsupported_capability: gate_closed): gate');
    expect(approvalErrorMessage(new ApiError('x', 401))).toBe('Sign in required.');
    expect(approvalErrorMessage(new ApiError('no plan yet', 404, 'not_found'))).toBe('Not found (not_found): no plan yet');
    expect(approvalErrorMessage(new ApiError('odd', 422))).toBe('odd');
    expect(approvalErrorMessage(new Error('boom'))).toBe('boom');
    expect(shortHash(SHA)).toBe(`${'a'.repeat(12)}...${'a'.repeat(6)}`);
    expect(shortHash(null)).toBe('-');
  });
});
