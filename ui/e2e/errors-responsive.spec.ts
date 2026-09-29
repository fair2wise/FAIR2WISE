import { expect, test } from '@playwright/test';
import { askQuestion, mockAgent } from './fixtures/agent';

test.describe('errors and responsive shell', () => {
  test('failed chat fetch shows Agent run failed without unmounting the header', async ({ page }) => {
    await mockAgent(page, { failChat: 'network' });
    await page.goto('/');
    await askQuestion(page, 'Trigger a network failure');
    await expect(page.getByText('Agent run failed')).toBeVisible();
    await expect(page.getByRole('img', { name: 'FAIR2WISE' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
  });

  test('backend-down still renders the shell', async ({ page }) => {
    await mockAgent(page, { failSettings: true });
    await page.goto('/');
    await expect(page.getByRole('img', { name: 'FAIR2WISE' })).toBeVisible();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
  });

  test('390px header actions stay clickable and the layout does not overflow', async ({ page }) => {
    await mockAgent(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();

    await page.getByRole('button', { name: 'New chat' }).click();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeEnabled();
  });
});
