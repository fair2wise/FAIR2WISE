import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';

// Pending Approvals (owner instruction 2026-11-09): the page lists what GET /v1/approvals/pending says waits for a
// person and decides each item through the existing route the item names; the sidebar entry shows only when
// GET /v1/capabilities confirms can_approve.
const SHA = 'd'.repeat(64);

const planItem = {
  kind: 'publication', id: 'job-1', job_id: 'job-1', job_kind: 'publish', corpus_scope: ['rsoxs'],
  target: 'http://127.0.0.1:8765/api#f2w', snapshot_ref: 'rsoxs/v1', reason: null,
  plan: { plan_id: 'p1', plan_sha256: SHA, snapshot_id: 'v1', base_revision: 'r0', creates: 3, links: 2, supersessions: 0, affected: 3 },
  submitted_at: '2026-11-09T10:00:00+00:00', requested_by: 'local-operator',
  approve: { method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } },
  reject: { method: 'POST', path: 'v1/jobs/job-1/cancel', body: null },
};

const listing = (items: Array<Record<string, unknown>>) => ({
  items, total: items.length, counts: items.length ? { publication: items.length } : {},
  includes: { plans: true, review_cases: true, cost_control: true, merge_apply: false },
  generated_at: '2026-11-09T10:05:00+00:00',
});

test.describe('Pending Approvals', () => {
  test('is hidden when the server does not confirm can_approve', async ({ page }) => {
    await mockAgent(page, { canApprove: false, approvals: { pending: listing([planItem]) } });
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'FAIR2WISE' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Pending Approvals' })).toHaveCount(0);
  });

  test('is hidden on an older server whose capabilities lack the field', async ({ page }) => {
    await mockAgent(page, { canApprove: 'omitted' });
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'FAIR2WISE' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Pending Approvals' })).toHaveCount(0);
  });

  test('approves a staged plan through the route it names, confirming the hash first', async ({ page }) => {
    const requests: string[] = [];
    let approved = false;
    await mockAgent(page, {
      canApprove: true,
      approvals: {
        pending: () => listing(approved ? [] : [planItem]),
        answers: { 'POST /v1/publications/job-1/approval': { approval: { plan_sha256: SHA }, job: { state: 'queued' } } },
        requests,
      },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Pending Approvals' }).click();
    await expect(page.getByRole('heading', { name: 'Publication plans (1)' })).toBeVisible();
    await expect(page.getByTestId('approval-details')).toContainText('3 creates, 2 links, 0 supersessions');
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog.getByTestId('confirm-hash')).toHaveText(SHA);
    page.once('request', request => {
      if (request.method() === 'POST') approved = true;
    });
    await dialog.getByRole('button', { name: 'Confirm approve' }).click();
    await expect(page.getByTestId('approval-outcome').or(page.getByTestId('approvals-empty')).first()).toBeVisible();
    expect(requests).toEqual([`POST /v1/publications/job-1/approval {"plan_sha256":"${SHA}"}`]);
    await expect(page.getByTestId('approvals-empty')).toHaveText('Nothing is waiting for approval.');
  });

  test('a stale approval is refused with the typed code and the item stays listed', async ({ page }) => {
    await mockAgent(page, {
      canApprove: true,
      approvals: {
        pending: listing([planItem]),
        answers: { 'POST /v1/publications/job-1/approval': { status: 409, code: 'stale_approval', message: 'the plan changed since it was shown; review the current plan' } },
      },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Pending Approvals' }).click();
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Confirm approve' }).click();
    const outcome = page.getByTestId('approval-outcome');
    await expect(outcome).toContainText('stale_approval');
    await expect(outcome).toContainText('Nothing was approved');
    await expect(page.getByTestId('approval-item')).toHaveCount(1);
  });

  test('rejects through the cancel route the item names', async ({ page }) => {
    const requests: string[] = [];
    await mockAgent(page, {
      canApprove: true,
      approvals: {
        pending: listing([planItem]),
        answers: { 'POST /v1/jobs/job-1/cancel': { job_id: 'job-1', state: 'cancelled' } },
        requests,
      },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Pending Approvals' }).click();
    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Confirm reject' }).click();
    await expect(page.getByTestId('approval-outcome')).toContainText('Reject: done');
    expect(requests).toEqual(['POST /v1/jobs/job-1/cancel ']);
  });
});
