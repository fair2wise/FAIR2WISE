import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApprovalsTab, decisionHash, decisionLabels } from './ApprovalsTab';
import { normalizeItem } from './approvalsApi';

const SHA = 'b'.repeat(64);
const PLAN_SHA = 'c'.repeat(64);

const PLAN_ITEM = {
  kind: 'publication', id: 'job-1', job_id: 'job-1', job_kind: 'publish', corpus_scope: ['rsoxs'],
  target: 'http://127.0.0.1:8765/api#f2w', snapshot_ref: 'rsoxs/v1', reason: null,
  plan: { plan_id: 'p1', plan_sha256: SHA, snapshot_id: 'v1', base_revision: 'r0', creates: 3, links: 2, supersessions: 1, affected: 3 },
  submitted_at: '2026-11-09T10:00:00+00:00', requested_by: 'local-operator',
  approve: { method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } },
  reject: { method: 'POST', path: 'v1/jobs/job-1/cancel', body: null },
};
const CASE_ITEM = {
  kind: 'review_case', id: 'case-1', case_id: 'case-1', corpus: 'rsoxs', case_type: 'unresolved_term', reason: 'unknown_term',
  route: 'human', priority: 2, revision: 'rev-7', updated: 1,
  approve: { method: 'POST', path: 'v1/curation/cases/case-1/review', body: { bound_revision: 'rev-7', accept: true } },
  reject: { method: 'POST', path: 'v1/curation/cases/case-1/review', body: { bound_revision: 'rev-7', accept: false } },
};
const MERGE_ITEM = {
  kind: 'merge_apply', id: 'merge:rsoxs', corpus: 'rsoxs', accepted_cases: 1, case_ids: ['case-9'], plan: null,
  plan_route: { method: 'POST', path: 'v1/curation/merges/apply', body: { corpus: 'rsoxs', route: 'apply_migration', dry_run: true } },
  approve: { method: 'POST', path: 'v1/curation/merges/apply', body: { corpus: 'rsoxs', route: 'apply_migration', dry_run: false, plan_sha256: null } },
  reject: null,
};
const COST_ITEM = {
  kind: 'cost_control', id: 'cost-control', enabled: true, budget_cap_usd: 0.5, spend: { cap_exceeded: true, charged_usd: 0.75 },
  approve: null, reject: null, routes: [{ method: 'PUT', path: 'v1/curation/cost-control', body: { enabled: true, budget_cap_usd: '<new cap in USD>' } }],
};

const listing = (items: unknown[]) => ({
  items, total: items.length,
  counts: Object.fromEntries(items.map(i => [(i as { kind: string }).kind, 1])),
  includes: { plans: true, review_cases: true, cost_control: true, merge_apply: true },
  generated_at: '2026-11-09T10:05:00+00:00',
});

interface Route { status: number; body: unknown }
let routes: Record<string, Route | (() => Route)>;
let calls: { method: string; path: string; body: unknown }[];

beforeEach(() => {
  calls = [];
  routes = { 'GET /v1/approvals/pending': { status: 200, body: listing([]) } };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x');
    const method = init?.method ?? 'GET';
    calls.push({ method, path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : null });
    const hit = routes[`${method} ${url.pathname}`];
    const answer = typeof hit === 'function' ? hit() : hit;
    return answer
      ? new Response(JSON.stringify(answer.body), { status: answer.status })
      : new Response(JSON.stringify({ code: 'not_found', message: 'no route' }), { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApprovalsTab />
    </QueryClientProvider>,
  );
}

const pendingReads = () => calls.filter(c => c.method === 'GET' && c.path === '/v1/approvals/pending').length;

describe('ApprovalsTab helpers', () => {
  it('binds a decision to the plan hash, or to the merge dry-run hash the page holds', () => {
    expect(decisionHash(normalizeItem(PLAN_ITEM)!, {})).toBe(SHA);
    expect(decisionHash(normalizeItem(MERGE_ITEM)!, {})).toBeNull();
    expect(decisionHash(normalizeItem(MERGE_ITEM)!, { 'merge:rsoxs': PLAN_SHA })).toBe(PLAN_SHA);
    expect(decisionHash(normalizeItem(CASE_ITEM)!, {})).toBeNull();
  });
  it('labels decisions per kind', () => {
    expect(decisionLabels(normalizeItem(PLAN_ITEM)!)).toEqual({ approve: 'Approve', reject: 'Reject' });
    expect(decisionLabels(normalizeItem(CASE_ITEM)!)).toEqual({ approve: 'Accept', reject: 'Reject' });
    expect(decisionLabels(normalizeItem({ kind: 'paused_job', id: 'j' })!)).toEqual({ approve: 'Resume', reject: 'Cancel job' });
  });
});

describe('ApprovalsTab', () => {
  it('shows the empty state', async () => {
    renderTab();
    expect(await screen.findByTestId('approvals-empty')).toHaveTextContent('Nothing is waiting for approval.');
    expect(screen.getByRole('heading', { name: 'Pending Approvals' })).toBeInTheDocument();
    expect(screen.getByText(/0 waiting/)).toBeInTheDocument();
  });

  it('groups items by kind with the plan details', async () => {
    routes['GET /v1/approvals/pending'] = { status: 200, body: listing([CASE_ITEM, PLAN_ITEM, COST_ITEM]) };
    renderTab();
    const groups = await screen.findAllByTestId('approval-group');
    expect(groups.map(g => within(g).getByRole('heading', { level: 3 }).textContent)).toEqual([
      'Publication plans (1)', 'Curation cases awaiting review (1)', 'Remote-cost cap exceeded (1)',
    ]);
    const plan = within(groups[0]).getByTestId('approval-details');
    expect(plan).toHaveTextContent('3 creates, 2 links, 1 supersessions, 3 affected ids');
    expect(plan).toHaveTextContent(SHA);
    expect(plan).toHaveTextContent('http://127.0.0.1:8765/api#f2w');
    expect(plan).toHaveTextContent('local-operator');
    // The case shows ids and metadata only (no text), the cost item has no one-click button.
    expect(within(groups[1]).getByTestId('approval-details')).toHaveTextContent('rev-7');
    expect(within(groups[1]).getByRole('button', { name: 'Accept' })).toBeEnabled();
    expect(within(groups[2]).queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(within(groups[2]).getByText(/needs a typed value/)).toBeInTheDocument();
    expect(within(groups[2]).getByTestId('approval-details')).toHaveTextContent('PUT v1/curation/cost-control');
  });

  it('approves a plan through the route the item names after a confirmation that shows the hash, then refreshes', async () => {
    let approved = false;
    routes['GET /v1/approvals/pending'] = () => ({ status: 200, body: listing(approved ? [] : [PLAN_ITEM]) });
    routes['POST /v1/publications/job-1/approval'] = () => {
      approved = true;
      return { status: 200, body: { approval: { plan_sha256: SHA }, job: { state: 'queued' } } };
    };
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByTestId('confirm-hash')).toHaveTextContent(SHA);
    expect(dialog).toHaveTextContent('POST v1/publications/job-1/approval');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm approve' }));
    expect(await screen.findByTestId('approvals-empty')).toBeInTheDocument();
    const sent = calls.find(c => c.method === 'POST');
    expect(sent).toEqual({ method: 'POST', path: '/v1/publications/job-1/approval', body: { plan_sha256: SHA } });
    expect(pendingReads()).toBeGreaterThanOrEqual(2);
  });

  it('shows a stale-approval refusal with its typed code and keeps the item', async () => {
    routes['GET /v1/approvals/pending'] = { status: 200, body: listing([PLAN_ITEM]) };
    routes['POST /v1/publications/job-1/approval'] = { status: 409, body: { code: 'stale_approval', message: 'the plan changed since it was shown', retryable: false } };
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Confirm approve' }));
    const outcome = await screen.findByTestId('approval-outcome');
    expect(outcome).toHaveAttribute('role', 'alert');
    expect(outcome).toHaveTextContent('stale_approval');
    expect(outcome).toHaveTextContent('Nothing was approved');
    expect(screen.getAllByTestId('approval-item')).toHaveLength(1);
  });

  it('shows a 403 and a 501 with their codes', async () => {
    routes['GET /v1/approvals/pending'] = { status: 200, body: listing([PLAN_ITEM, CASE_ITEM]) };
    routes['POST /v1/publications/job-1/approval'] = { status: 403, body: { code: 'denied', message: 'approver is not a verified Tiled admin' } };
    routes['POST /v1/curation/cases/case-1/review'] = { status: 501, body: { code: 'unsupported_capability', message: 'curation review is not configured', reason: 'not_configured' } };
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Confirm approve' }));
    expect(await screen.findByText(/Not permitted \(denied\): approver is not a verified Tiled admin/)).toBeInTheDocument();
    const caseItem = screen.getAllByTestId('approval-item').find(el => el.dataset.kind === 'review_case')!;
    await user.click(within(caseItem).getByRole('button', { name: 'Reject' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).queryByTestId('confirm-hash')).toBeNull();
    expect(dialog).toHaveTextContent('"accept":false');
    await user.click(within(dialog).getByRole('button', { name: 'Confirm reject' }));
    expect(await screen.findByText(/Not supported on this deployment \(unsupported_capability: not_configured\)/)).toBeInTheDocument();
    const review = calls.find(c => c.path === '/v1/curation/cases/case-1/review');
    expect(review?.body).toEqual({ bound_revision: 'rev-7', accept: false });
  });

  it('rejects through the cancel route without a body', async () => {
    routes['GET /v1/approvals/pending'] = { status: 200, body: listing([PLAN_ITEM]) };
    routes['POST /v1/jobs/job-1/cancel'] = { status: 200, body: { job_id: 'job-1', state: 'cancelled' } };
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole('button', { name: 'Reject' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Confirm reject' }));
    await waitFor(() => expect(calls.some(c => c.path === '/v1/jobs/job-1/cancel')).toBe(true));
    expect(calls.find(c => c.path === '/v1/jobs/job-1/cancel')).toMatchObject({ method: 'POST', body: null });
    expect(await screen.findByText(/Reject: done/)).toBeInTheDocument();
  });

  it('a merge item plans first (dry run), then approves with the planned hash; it has no reject route', async () => {
    routes['GET /v1/approvals/pending'] = { status: 200, body: listing([MERGE_ITEM]) };
    routes['POST /v1/curation/merges/apply'] = () => {
      const last = calls[calls.length - 1];
      const dry = (last.body as { dry_run?: boolean }).dry_run;
      return dry
        ? { status: 200, body: { report: { plan_sha256: PLAN_SHA, dry_run: true } } }
        : { status: 501, body: { code: 'unsupported_capability', message: 'closed until the C4 rerun', reason: 'gate_closed' } };
    };
    const user = userEvent.setup();
    renderTab();
    const approve = await screen.findByRole('button', { name: 'Approve apply' });
    expect(approve).toBeDisabled();
    expect(screen.getByText('no reject route')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Plan (dry run)' }));
    await waitFor(() => expect(approve).toBeEnabled());
    expect(screen.getByTestId('approval-outcome')).toHaveTextContent('Planned: hash');
    await user.click(approve);
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByTestId('confirm-hash')).toHaveTextContent(PLAN_SHA);
    await user.click(within(dialog).getByRole('button', { name: 'Confirm approve apply' }));
    expect(await screen.findByText(/gate_closed/)).toBeInTheDocument();
    const real = calls.filter(c => c.path === '/v1/curation/merges/apply').map(c => c.body);
    expect(real).toEqual([
      { corpus: 'rsoxs', route: 'apply_migration', dry_run: true },
      { corpus: 'rsoxs', route: 'apply_migration', dry_run: false, plan_sha256: PLAN_SHA },
    ]);
  });

  it('says so when the route is refused or absent', async () => {
    routes['GET /v1/approvals/pending'] = { status: 403, body: { code: 'denied', message: 'missing capability: graph.publish' } };
    renderTab();
    expect(await screen.findByRole('alert')).toHaveTextContent('not available to this caller');
  });
});
