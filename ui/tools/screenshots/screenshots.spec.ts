import { test } from '@playwright/test';
import { askQuestion, mockAgent, openSettings, waitForTypedAnswer } from '../../e2e/fixtures/agent';
import { mockCandidate, mockLibrary, mockPaper } from '../../e2e/fixtures/library';

const DIR = process.env.SHOT_DIR || '/tmp/f2w_shots'; // set SHOT_DIR=<dir> per run (before, after)
const shot = (page: import('@playwright/test').Page, name: string) => page.screenshot({ path: `${DIR}/${name}.png` });
const SHA = 'd'.repeat(64);
const listing = {
  sources: [
    { id: 'json:matkg_rsoxs_v2', kind: 'json', label: 'matkg_rsoxs_v2.json', read_only: true, available: true, node_count: 7, edge_count: 5, revision: null, detail: null, graph_id: 'rsoxs_v2' },
  ], tiled: { status: 'ok', detail: null },
};
const report = { tool: 'kg_compute_statistics', status: 'ok', warnings: [], artifact_refs: [], data: { operation: 'kg_compute_statistics', method: 'kg-statistics/1', completeness: 'complete', population: 'all node and edge records of the graph', counting_units: { entities: 'distinct node ids' }, coverage: { exactness: 'exact', records_scanned: 12, reasons: [] }, metrics: { entities: 7 }, preview: {}, excluded: {}, warnings: [], display_truncated: false } };
const planItem = { kind: 'publication', id: 'job-1', job_id: 'job-1', job_kind: 'publish', corpus_scope: ['rsoxs'], target: 'http://127.0.0.1:8765/api#f2w', snapshot_ref: 'rsoxs/v1', reason: null,
  plan: { plan_id: 'p1', plan_sha256: SHA, snapshot_id: 'v1', base_revision: 'r0', creates: 3, links: 2, supersessions: 0, affected: 3 },
  submitted_at: '2026-11-09T10:00:00+00:00', requested_by: 'local-operator',
  approve: { method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } }, reject: { method: 'POST', path: 'v1/jobs/job-1/cancel', body: null } };
const approvals = { items: [planItem], total: 1, counts: { publication: 1 }, includes: { plans: true, review_cases: true, cost_control: true, merge_apply: false }, generated_at: '2026-11-09T10:05:00+00:00' };
const PAPERS = [mockPaper('rs-a', 'rsoxs', { relpath: 'rsoxs/2013/rs-a.pdf', doi: '10.1/rsoxs-a' }), mockPaper('tomo-a', 'tomo')];
const FINDS = [mockCandidate('oa1', { title: 'Open access RSoXS paper', status: 'downloaded', relpath: 'rsoxs/2021/oa1.pdf' }), mockCandidate('wall1', { title: 'Behind a paywall', status: 'paywalled' }), mockCandidate('bad', { title: 'Broken download', status: 'failed', detail: 'HTTP 403' })];

async function boot(page: import('@playwright/test').Page, extra: Record<string, unknown> = {}) {
  await mockAgent(page, { canPublish: true, canApprove: true, approvals: { pending: approvals }, graphSources: { listing, graphs: {} }, operations: { routes: { statistics: report } }, ...extra });
  await mockLibrary(page, { papers: PAPERS, bundles: ['demo'], harvestFinds: FINDS, publish: { posts: [] } });
  await page.goto('/');
  await page.waitForTimeout(500);
}

test('01 chat home', async ({ page }) => { await boot(page); await shot(page, '01-chat-home'); });
test('02 chat answer with graph', async ({ page }) => {
  await boot(page); await askQuestion(page, 'What is P3HT?'); await waitForTypedAnswer(page); await page.waitForTimeout(800); await shot(page, '02-chat-answer');
});
test('03 kg viewer', async ({ page }) => { await boot(page); await page.getByRole('button', { name: 'KG Viewer' }).click(); await page.waitForTimeout(1200); await shot(page, '03-kg-viewer'); });
test('04 settings dialog', async ({ page }) => { await boot(page); await openSettings(page); await page.waitForTimeout(400); await shot(page, '04-settings'); });
test('05 paper search dialog', async ({ page }) => { await boot(page); await page.getByRole('button', { name: 'Paper search' }).click(); await page.waitForTimeout(500); await shot(page, '05-paper-search'); });
test('06 search chats dialog', async ({ page }) => { await boot(page); await page.getByRole('button', { name: 'Search chats' }).click(); await page.waitForTimeout(500); await shot(page, '06-search-chats'); });
test('07 bookmarks sheet', async ({ page }) => { await boot(page); await page.getByRole('button', { name: 'Bookmarks' }).click(); await page.waitForTimeout(500); await shot(page, '07-bookmarks'); });
test('08 docs', async ({ page }) => { await boot(page); await page.getByRole('button', { name: 'Documentation' }).click(); await page.waitForTimeout(800); await shot(page, '08-docs'); });
test('09 library papers', async ({ page }) => { await boot(page); await page.getByRole('link', { name: 'Library' }).click(); await page.waitForTimeout(600); await shot(page, '09-library-papers'); });
test('10 harvester', async ({ page }) => { await boot(page); await page.getByRole('link', { name: 'Library' }).click(); await page.getByRole('tab', { name: 'Harvester' }).click(); await page.waitForTimeout(600); await shot(page, '10-harvester'); });
test('11 extract idle', async ({ page }) => { await boot(page); await page.getByRole('link', { name: 'Extract' }).click(); await page.waitForTimeout(600); await shot(page, '11-extract'); });
test('12 extract continue + publish dialog', async ({ page }) => {
  await boot(page); await page.getByRole('link', { name: 'Extract' }).click();
  await page.getByRole('radio', { name: 'Continue existing KG' }).click();
  await page.getByLabel('Existing KG', { exact: true }).selectOption('bundle:demo');
  await page.waitForTimeout(400); await shot(page, '12a-extract-continue');
  await page.getByRole('button', { name: 'Publish to Tiled' }).click(); await page.waitForTimeout(800); await shot(page, '12b-publish-dialog');
});
test('13 operations', async ({ page }) => {
  await boot(page); await page.getByRole('link', { name: 'Operations' }).click();
  await page.getByLabel('Graph', { exact: true }).selectOption('rsoxs_v2'); await page.getByRole('button', { name: 'Run' }).click();
  await page.waitForTimeout(700); await shot(page, '13-operations');
});
test('14 approvals + confirm dialog', async ({ page }) => {
  await boot(page); await page.getByRole('link', { name: 'Pending Approvals' }).click(); await page.waitForTimeout(600); await shot(page, '14a-approvals');
  await page.getByRole('button', { name: 'Approve', exact: true }).click(); await page.waitForTimeout(600); await shot(page, '14b-approve-confirm');
});
