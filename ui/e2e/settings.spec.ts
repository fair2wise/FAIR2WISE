import { expect, test } from '@playwright/test';
import { mockAgent, openSettings, OPS_PATH, RSOXS_PATH, XRAY_DEMO_PATH } from './fixtures/agent';

test.describe('settings', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await openSettings(page);
  });

  test('JSON is default with multi-select graphs, hops, max nodes, Tiled, and Source RAG', async ({ page }) => {
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('radio', { name: /JSON/ })).toBeChecked();
    await expect(dialog.getByRole('radio', { name: /Tiled Graph/ })).not.toBeChecked();

    await expect(dialog.getByRole('checkbox', { name: /matkg_rsoxs_v3\.json/ })).toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: /matkg_bl1101_v9\.json/ })).toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: /matkg_xray_papers_cborg_chat\.json/ })).not.toBeChecked();
    await expect(page.getByText('literature / science')).toBeVisible();
    await expect(page.getByText('11.0.1.2 ops')).toBeVisible();
    await expect(page.getByText('x-ray demo (opt-in)')).toBeVisible();

    const hops = page.locator('#kg-query-hops');
    await expect(hops).toHaveValue('20');
    await expect(hops.locator('option')).toHaveCount(20);
    await hops.selectOption('1');
    await expect(hops).toHaveValue('1');

    const maxNodes = page.locator('#kg-query-max-nodes');
    await expect(maxNodes).toHaveValue('1000');
    for (const value of ['50', '100', '250', '500', '1000']) {
      await expect(maxNodes.locator(`option[value="${value}"]`)).toHaveCount(1);
    }

    await expect(dialog.getByRole('checkbox', { name: /Live Tiled Graph/ })).toBeChecked();
    await expect(dialog.getByText(/Status:/)).toContainText('ok');
    await expect(dialog.getByRole('checkbox', { name: /Source RAG/ })).not.toBeChecked();

    expect(RSOXS_PATH).toContain('rsoxs');
    expect(OPS_PATH).toContain('bl1101');
    expect(XRAY_DEMO_PATH).toContain('xray');
  });

  test('Save keeps Source RAG; discard without Save restores the last saved draft', async ({ page }) => {
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('checkbox', { name: /Source RAG/ }).check();
    await dialog.getByRole('button', { name: 'Close' }).click();
    await openSettings(page);
    await expect(page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ })).not.toBeChecked();

    await page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ }).check();
    await page.getByRole('button', { name: 'Save Preferences' }).click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0);
    await openSettings(page);
    await expect(page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ })).toBeChecked();
  });
});

test.describe('graph source apply on load', () => {
  test('server in splash mode: the UI sends one partial PUT with its own JSON source', async ({ page }) => {
    const puts: Array<Record<string, unknown>> = [];
    await mockAgent(page, { settings: { graph_source: 'splash' }, settingsPuts: puts });
    await page.goto('/');
    await expect.poll(() => puts).toEqual([{ graph_source: 'json' }]);
  });

  test('server already in JSON mode: no PUT on load', async ({ page }) => {
    const puts: Array<Record<string, unknown>> = [];
    await mockAgent(page, { settingsPuts: puts });
    await page.goto('/');
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await page.waitForTimeout(500);
    expect(puts).toEqual([]);
  });
});
