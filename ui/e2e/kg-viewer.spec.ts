import { expect, test } from '@playwright/test';
import {
  askQuestion,
  mockAgent,
  openSettings,
  P3HT_NODE,
  SCAN_NODE,
  waitForTypedAnswer,
} from './fixtures/agent';

test.describe('KG viewer', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
  });

  test('Fixed Layout is default and Force-directed stays in F2W chrome', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Fixed Layout' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-kg-layout="existing"]')).toBeVisible();

    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await page.getByRole('button', { name: 'Force-directed' }).click();
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
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toHaveCount(1);
  });

  test('Settings VIEW loads the checked JSON graph', async ({ page }) => {
    await openSettings(page);
    await page.getByRole('button', { name: 'View', exact: true }).first().click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0);
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await expect(page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`)).toBeVisible();
  });
});
