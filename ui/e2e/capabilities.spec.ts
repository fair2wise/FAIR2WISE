import { expect, test } from '@playwright/test';
import { mockAgent, P3HT_NODE } from './fixtures/agent';

// T18D (Q13): Edit properties (and with it Save) is hidden only on an explicit can_save:false.
async function openNodeCard(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'KG Viewer' }).click();
  await page.locator(`[data-kg-node-id="${P3HT_NODE.id}"]`).click();
  await expect(page.getByText('Pinned')).toBeVisible();
}

test.describe('capabilities gate on node editing', () => {
  test('can_save true shows Edit properties', async ({ page }) => {
    await mockAgent(page, { canSave: true, settings: { graph_source: 'splash' } });
    await openNodeCard(page);
    await expect(page.getByRole('button', { name: 'Edit properties' })).toBeVisible();
  });

  test('can_save false hides Edit properties', async ({ page }) => {
    await mockAgent(page, { canSave: false, settings: { graph_source: 'splash' } });
    await openNodeCard(page);
    await expect(page.getByRole('button', { name: 'Edit properties' })).toHaveCount(0);
  });

  test('missing route (older server) keeps Edit properties', async ({ page }) => {
    await mockAgent(page, { canSave: 'absent', settings: { graph_source: 'splash' } });
    await openNodeCard(page);
    await expect(page.getByRole('button', { name: 'Edit properties' })).toBeVisible();
  });
});
