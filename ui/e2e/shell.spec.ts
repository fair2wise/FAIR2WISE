import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';

test.describe('shell', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
  });

  test('renders header, composer, and six hub actions', async ({ page }) => {
    await expect(page.getByRole('img', { name: 'FAIR2WISE' })).toBeVisible();
    await expect(page.getByText('FAIR2WISE').first()).toBeVisible();

    await expect(page.getByRole('button', { name: 'New chat' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Search chats' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Paper search' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Bookmarks' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Documentation' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Settings' })).toBeVisible();

    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();
    await expect(page.getByRole('group', { name: 'KG layout' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Fixed Layout' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Force-directed' })).toBeVisible();
  });
});
