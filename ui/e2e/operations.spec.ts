import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';

// T19E frontend closure: the Operations tab runs the five read-only /v1/operations reads; it is shown only
// when GET /v1/capabilities confirms can_run_operations (the chat.ask entry capability of those routes).
const listing = {
  sources: [
    { id: 'json:matkg_rsoxs_v2', kind: 'json', label: 'matkg_rsoxs_v2.json', read_only: true, available: true,
      node_count: 7, edge_count: 5, revision: null, detail: null, graph_id: 'rsoxs_v2' },
    { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true,
      node_count: 268, edge_count: 126, revision: 'v1@abc', detail: null, graph_id: 'e2e_demo' },
  ],
  tiled: { status: 'ok', detail: null },
};

const report = (status: string, completeness: string) => ({
  tool: 'kg_compute_statistics', status, warnings: status === 'partial' ? ['partial: the scan did not cover the whole population'] : [],
  artifact_refs: [],
  data: {
    operation: 'kg_compute_statistics', method: 'kg-statistics/1', completeness,
    population: 'all node and edge records of the graph',
    counting_units: { entities: 'distinct node ids' },
    coverage: { exactness: 'exact', records_scanned: 12, reasons: [] },
    metrics: { entities: 7 }, preview: {}, excluded: {}, warnings: [], display_truncated: false,
  },
});

test.describe('Operations tab', () => {
  test('is hidden when the server does not confirm can_run_operations', async ({ page }) => {
    await mockAgent(page, { canRunOperations: false });
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'FAIR2WISE' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Operations' })).toHaveCount(0);
  });

  test('is hidden on an older server whose capabilities lack the field', async ({ page }) => {
    await mockAgent(page, { canRunOperations: 'omitted' });
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'FAIR2WISE' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Operations' })).toHaveCount(0);
  });

  test('runs statistics on a listed graph and shows status, units and coverage', async ({ page }) => {
    const requests: string[] = [];
    await mockAgent(page, {
      graphSources: { listing, graphs: {} },
      operations: { routes: { statistics: report('ok', 'complete') }, requests },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Operations' }).click();
    await page.getByLabel('Graph', { exact: true }).selectOption('rsoxs_v2');
    await page.getByRole('button', { name: 'Run' }).click();
    await expect(page.getByTestId('operation-status')).toHaveText('ok');
    await expect(page.getByRole('table', { name: 'Operation metrics' })).toContainText('distinct node ids');
    await expect(page.getByTestId('operation-coverage')).toContainText('12 records scanned');
    expect(requests).toEqual(['/v1/operations/statistics?graph_id=rsoxs_v2']);
  });

  test('a partial result stays visibly partial', async ({ page }) => {
    await mockAgent(page, {
      graphSources: { listing, graphs: {} },
      operations: { routes: { statistics: report('partial', 'partial') } },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Operations' }).click();
    await page.getByLabel('Graph', { exact: true }).selectOption('e2e_demo');
    await page.getByRole('button', { name: 'Run' }).click();
    await expect(page.getByTestId('operation-status')).toHaveText('partial');
    await expect(page.getByRole('list', { name: 'Warnings' })).toContainText('did not cover');
  });

  test('a server refusal (501 on a JSON graph) is shown, not hidden', async ({ page }) => {
    await mockAgent(page, {
      graphSources: { listing, graphs: {} },
      operations: { routes: { recent: { status: 501, code: 'unsupported_capability', message: 'recent entities need a live graph' } } },
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Operations' }).click();
    await page.getByLabel('Operation', { exact: true }).selectOption('kg_recent_entities');
    await page.getByLabel('Graph', { exact: true }).selectOption('rsoxs_v2');
    await page.getByRole('button', { name: 'Run' }).click();
    await expect(page.getByRole('alert')).toContainText('Not supported for this graph');
  });
});
