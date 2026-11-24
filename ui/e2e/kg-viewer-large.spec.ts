import { expect, test, type Page } from '@playwright/test';
import { mockAgent, useFixedLayout } from './fixtures/agent';
import { BIG_GRAPH_SPECS, syntheticGraph, type BigGraph, type BigGraphSpec } from './fixtures/bigGraph';

// Owner decision 2026-11-10: the two demo graphs (RSoXS KG v4, 22,012 nodes / 57,575 edges; 580-paper OPV KG,
// 15,815 nodes / 20,096 edges) must open on an overview, never on "All". Whole-graph rendering used to block the
// tab for ~26 s and create ~455,000 SVG elements (measured in docs/migration/reviews/H7/neighborhood_cap_evaluation.md).

/** Generous bounds: CI machines are slow; the unfixed code needed 26 s and was past any of these. */
const RENDER_BUDGET_MS = 15_000;
const MAX_NODE_ELEMENTS = 2_000;
const MAX_SVG_ELEMENTS = 20_000;

function sourceListing(id: string, graph: BigGraph) {
  return {
    sources: [{
      id, kind: 'json', label: id.slice(5), read_only: true, available: true,
      node_count: graph.nodes.length, edge_count: graph.edges.length, revision: null, detail: null,
    }],
    tiled: { status: 'ok', detail: null },
  };
}

async function openLarge(page: Page, spec: BigGraphSpec) {
  const graph = syntheticGraph(spec);
  const id = `json:${spec.graphId}`;
  await mockAgent(page, { graphSources: { listing: sourceListing(id, graph), graphs: { [id]: graph } } });
  await page.addInitScript(source => window.localStorage.setItem('fair2wise-graph-source-v1', source), id);
  await page.goto('/');
  const started = Date.now();
  await page.getByRole('button', { name: 'KG Viewer' }).click();
  await expect(page.locator('[data-kg-empty="false"]')).toBeVisible({ timeout: RENDER_BUDGET_MS });
  return { graph, renderMs: Date.now() - started };
}

async function domCounts(page: Page) {
  return page.evaluate(() => ({
    nodeElements: document.querySelectorAll('[data-kg-node-id]').length,
    circles: document.querySelectorAll('circle').length,
    svgElements: document.querySelectorAll('svg *').length,
    canvases: document.querySelectorAll('canvas').length,
  }));
}

/** Round trip of a timer through the page's main thread: a frozen tab never answers. */
async function mainThreadLatencyMs(page: Page) {
  return page.evaluate(() => new Promise<number>(resolve => {
    const start = performance.now();
    setTimeout(() => resolve(performance.now() - start), 0);
  }));
}

test.describe('KG viewer on the large demo graphs', () => {
  // One worker for this file: each test builds a 22k-node graph and the 5,000-node step is deliberately slow.
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

  test('22k-node RSoXS KG opens on a 300-node overview and the page stays responsive', async ({ page }) => {
    const { graph, renderMs } = await openLarge(page, BIG_GRAPH_SPECS.rsoxs22k);
    expect(graph.nodes).toHaveLength(22_012);
    expect(renderMs).toBeLessThan(RENDER_BUDGET_MS);

    await expect(page.getByTestId('kg-overview-banner')).toContainText(
      'Showing the 300 most connected of 22,012 nodes: search or focus a node to explore, or show more.',
    );
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '300');
    await expect(page.getByText('of 22,012', { exact: false }).first()).toBeVisible();
    await expect(page.getByLabel('Nodes to render')).toHaveValue('300');

    const counts = await domCounts(page);
    expect(counts.nodeElements).toBeLessThanOrEqual(MAX_NODE_ELEMENTS);
    expect(counts.circles).toBeLessThanOrEqual(MAX_NODE_ELEMENTS);
    expect(counts.svgElements).toBeLessThanOrEqual(MAX_SVG_ELEMENTS);
    expect(await mainThreadLatencyMs(page)).toBeLessThan(2_000);

    // Interaction still works: the toolbar answers promptly.
    const clickStarted = Date.now();
    await page.getByRole('button', { name: 'Force-directed' }).click();
    await expect(page.locator('[data-kg-layout="force"]')).toBeVisible();
    expect(Date.now() - clickStarted).toBeLessThan(5_000);
  });

  test('Show more steps the overview 300 -> 1,000 -> 2,000 and keeps the selector in step', async ({ page }) => {
    await openLarge(page, BIG_GRAPH_SPECS.rsoxs22k);
    await page.getByTestId('kg-overview-show-more').click();
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '1000');
    await expect(page.getByLabel('Nodes to render')).toHaveValue('1000');
    await expect(page.getByTestId('kg-overview-banner')).toContainText('Showing the 1,000 most connected of 22,012 nodes');
    await page.getByTestId('kg-overview-show-more').click();
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '2000');
    await expect(page.getByLabel('Nodes to render')).toHaveValue('2000');
    await expect(page.getByTestId('kg-overview-slow-warning')).toHaveCount(0);
    const counts = await domCounts(page);
    expect(counts.nodeElements).toBeLessThanOrEqual(MAX_NODE_ELEMENTS);
    expect(await mainThreadLatencyMs(page)).toBeLessThan(2_000);
  });

  test('beyond 2,000 nodes a warning shows and force-directed layout is disabled', async ({ page }) => {
    await openLarge(page, BIG_GRAPH_SPECS.rsoxs22k);
    await page.getByLabel('Nodes to render').selectOption('5000');
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '5000', { timeout: 150_000 });
    await expect(page.getByTestId('kg-overview-slow-warning')).toContainText('layout will be slow');
    await expect(page.getByRole('button', { name: 'Force-directed' })).toBeDisabled();
    await expect(page.locator('[data-kg-layout="existing"]')).toBeVisible();
    await expect(page.getByTestId('kg-overview-show-more')).toHaveCount(0);
  });

  test('search covers all nodes and focusing one shows its neighbourhood, with a way back', async ({ page }) => {
    const { graph } = await openLarge(page, BIG_GRAPH_SPECS.rsoxs22k);
    await useFixedLayout(page);
    const overviewIds = await page.locator('[data-kg-node-id]').evaluateAll(
      elements => new Set(elements.map(element => element.getAttribute('data-kg-node-id'))).size,
    );
    expect(overviewIds).toBeLessThanOrEqual(300);
    // A late, low-degree node: certainly outside the 300 most connected.
    const target = graph.nodes[graph.nodes.length - 1];
    await page.getByRole('button', { name: 'Search nodes' }).click();
    await page.getByPlaceholder('Describe a node…').fill(target.label);
    await page.getByPlaceholder('Describe a node…').press('Enter');
    await page.getByRole('button', { name: new RegExp(target.label) }).first().click();
    await expect(page.locator('[data-kg-overview]')).toHaveAttribute('data-kg-overview', 'false');
    await expect(page.locator(`[data-kg-node-id="${target.id}"]`)).toBeVisible();
    const count = Number(await page.locator('[data-kg-node-count]').getAttribute('data-kg-node-count'));
    expect(count).toBeGreaterThanOrEqual(1);
    expect(count).toBeLessThanOrEqual(500);
    await page.getByTestId('kg-overview-back').click();
    await expect(page.getByTestId('kg-overview-banner')).toBeVisible();
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '300');
  });

  test('a hub neighbourhood above 500 nodes is capped with a notice', async ({ page }) => {
    const { graph } = await openLarge(page, BIG_GRAPH_SPECS.rsoxs22k);
    // Hub n0 has 670 links to typed nodes (the largest degree of the real RSoXS v4 KG), above the 500-node cap.
    const hub = graph.nodes[0];
    await page.getByRole('button', { name: 'Search nodes' }).click();
    await page.getByPlaceholder('Describe a node…').fill(hub.label);
    await page.getByPlaceholder('Describe a node…').press('Enter');
    await page.getByRole('button', { name: new RegExp(`^${hub.label}\\b`) }).first().click();
    await expect(page.getByTestId('graph-neighborhood-truncated')).toContainText('Neighbourhood limited to 500 nodes');
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '500');
  });

  test('580-paper OPV KG hides Unknown placeholders by default and can show them', async ({ page }) => {
    await openLarge(page, BIG_GRAPH_SPECS.opv15k);
    await expect(page.getByTestId('kg-overview-banner')).toContainText('of 15,815 nodes');
    await expect(page.getByTestId('kg-overview-banner')).toContainText('Unknown placeholder nodes are hidden');
    await expect(page.locator('svg text', { hasText: /^Unknown \d+/ })).toHaveCount(0);
    await page.getByTestId('kg-overview-include-unknown').check();
    await expect(page.getByTestId('kg-overview-banner')).not.toContainText('are hidden');
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '300');
    const counts = await domCounts(page);
    expect(counts.nodeElements).toBeLessThanOrEqual(MAX_NODE_ELEMENTS);
  });

  test('a small graph source is unchanged: Nodes defaults to All and there is no banner', async ({ page }) => {
    await openLarge(page, { nodes: 400, edges: 700, graphId: 'small400' });
    await expect(page.getByTestId('kg-overview-banner')).toHaveCount(0);
    await expect(page.getByLabel('Nodes to render')).toHaveValue('all');
    await expect(page.locator('[data-kg-node-count]')).toHaveAttribute('data-kg-node-count', '400');
  });
});
