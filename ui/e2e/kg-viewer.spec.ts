import { expect, test } from '@playwright/test';
import {
  askQuestion,
  mockAgent,
  openSettings,
  P3HT_NODE,
  SCAN_NODE,
  useFixedLayout,
  waitForTypedAnswer,
} from './fixtures/agent';

test.describe('KG viewer', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
  });

  test('Force-directed is the default in the chat graph and the KG viewer, and the layout switch stays in F2W chrome', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Force-directed' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-kg-layout="force"]')).toBeVisible();

    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Force-directed' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-kg-layout="force"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Toggle weakly connected nodes' })).toBeVisible();
    await expect(page.locator('iframe')).toHaveCount(0);

    await page.getByRole('button', { name: 'Fixed Layout' }).click();
    await expect(page.locator('[data-kg-layout="existing"]')).toBeVisible();
  });

  test('KG Viewer loads the catalog, Nodes includes 1000/10000/All, and click shows label+type', async ({ page }) => {
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.getByRole('button', { name: 'Agent KG Viewer' })).toBeVisible();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await useFixedLayout(page);
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toBeVisible();
    await expect(page.locator(`[data-kg-node-id="${SCAN_NODE.id}"]`)).toHaveCount(0);

    const nodesSelect = page.getByLabel('Nodes to render');
    await expect(nodesSelect).toBeVisible();
    await expect(nodesSelect.locator('option[value="all"]')).toHaveCount(1);
    await expect(nodesSelect.locator('option[value="1000"]')).toHaveCount(1);
    await expect(nodesSelect.locator('option[value="10000"]')).toHaveCount(1);

    await page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`).click();
    await expect(page.getByText('Pinned')).toBeVisible();
    await expect(page.getByText('Material · RSoXS literature')).toBeVisible();
    await expect(page.getByText('Unknown')).toHaveCount(0);

    await page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`).hover();
    await expect(page.getByText('Poly(3-hexylthiophene)', { exact: false }).first()).toBeVisible();
  });

  test('View Knowledge Graph pins the retrieved subset', async ({ page }) => {
    await askQuestion(page, 'Show the P3HT neighborhood');
    await waitForTypedAnswer(page);
    await expect(page.getByRole('button', { name: 'View Knowledge Graph' })).toBeVisible();
    await expect(page.getByText('2 nodes')).toBeVisible();
    // Force-directed (the default) draws on a canvas with no per-node DOM; switch to the fixed SVG layout to count nodes.
    await useFixedLayout(page);
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toHaveCount(1);
  });

  test('Settings VIEW loads the checked JSON graph', async ({ page }) => {
    await openSettings(page);
    await page.getByRole('button', { name: 'View', exact: true }).first().click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0);
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await useFixedLayout(page);
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toBeVisible();
  });
});

test.describe('KG viewer graph sources', () => {
  const ALPHA = { id: 'm:alpha', label: 'Alpha film', type: 'Material', description: 'from Tiled', graph_id: 'e2e_demo' };
  const BETA = { id: 'm:beta', label: 'Beta resin', type: 'Process', description: '', graph_id: 'e2e_demo' };
  const TILED_GRAPH = {
    nodes: [ALPHA, BETA],
    edges: [{ source: 'm:alpha', target: 'm:beta', predicate: 'rel:related_to' }],
    source_path: 'source:tiled:e2e_demo',
  };
  const listing = (tiledStatus = 'ok') => ({
    sources: [
      { id: 'json:rsoxs_v1', kind: 'json', label: 'matkg_rsoxs_v1.json', read_only: true, available: true,
        node_count: null, edge_count: null, revision: null, detail: null },
      { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true,
        node_count: 2, edge_count: 1, revision: 'v1@abc', detail: null },
    ],
    tiled: { status: tiledStatus, detail: null },
  });

  test('selector is hidden when the server has no source listing', async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await expect(page.getByLabel('Graph source')).toHaveCount(0);
  });

  test('reading a Tiled source passes source on graph, search and node requests and is remembered', async ({ page }) => {
    const requests: string[] = [];
    await mockAgent(page, { graphSources: { listing: listing(), graphs: { 'tiled:e2e_demo': TILED_GRAPH }, requests } });
    await page.goto('/');
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    const select = page.getByLabel('Graph source');
    await expect(select).toBeVisible();
    await expect(page.getByTestId('graph-source-kind')).toHaveText('Default');

    await select.selectOption('tiled:e2e_demo');
    await expect(page.getByTestId('graph-source-kind')).toHaveText('Tiled · read-only');
    await useFixedLayout(page);
    await expect(page.locator('[data-kg-node-id="m:alpha"]')).toBeVisible();
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toHaveCount(0);
    expect(requests).toContain('GET /graph?source=tiled%3Ae2e_demo');

    await page.getByRole('button', { name: 'Search nodes' }).click();
    await page.getByPlaceholder('Describe a node…').fill('beta');
    await page.getByPlaceholder('Describe a node…').press('Enter');
    await expect(page.getByText('Beta resin')).toBeVisible();
    expect(requests).toContain('POST /graph/nodes/search?source=tiled%3Ae2e_demo');

    await page.locator('[data-kg-node-id="m:alpha"]').click();
    await expect.poll(() => requests.some(item => item.startsWith('GET /graph/node/m%3Aalpha?source=tiled%3Ae2e_demo'))).toBe(true);
    await expect(page.getByRole('button', { name: 'Edit properties' })).toHaveCount(0);

    await page.reload();
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.getByLabel('Graph source')).toHaveValue('tiled:e2e_demo');
    await useFixedLayout(page); // the layout choice is not remembered across a reload
    await expect(page.locator('[data-kg-node-id="m:alpha"]')).toBeVisible();

    await page.getByLabel('Graph source').selectOption('');
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toBeVisible();
    await expect(page.getByTestId('graph-source-kind')).toHaveText('Default');
  });

  test('an unavailable Tiled shows a clear state and no stale graph', async ({ page }) => {
    await mockAgent(page, {
      graphSources: {
        listing: listing('unavailable'),
        graphs: { 'tiled:e2e_demo': { status: 503, code: 'source_unavailable', message: 'Tiled graph is unavailable' } },
      },
    });
    await page.addInitScript(() => window.localStorage.setItem('fair2wise-graph-source-v1', 'tiled:e2e_demo'));
    await page.goto('/');
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.getByTestId('graph-source-error')).toHaveText('Tiled graph is unavailable');
    await expect(page.getByRole('option', { name: 'Tiled unavailable' })).toHaveCount(1);
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toHaveCount(0);
  });
});

