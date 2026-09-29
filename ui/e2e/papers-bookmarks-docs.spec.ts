import { expect, test } from '@playwright/test';
import { mockAgent, PAPER_PUB } from './fixtures/agent';

test.describe('papers, bookmarks, and docs', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
  });

  test('paper search does not fire on empty query, then returns title/authors/year', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByRole('button', { name: 'Paper search' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Paper search' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Search' })).toBeDisabled();

    await dialog.getByPlaceholder("Describe what you're writing about or paste a paragraph...").fill('P3HT morphology');
    await dialog.getByRole('button', { name: 'Search' }).click();
    await expect(dialog.getByText(PAPER_PUB.paper_title)).toBeVisible();
    await expect(dialog.getByText(/Ada Lovelace.*2020/)).toBeVisible();
    await dialog.getByRole('button', { name: 'Copy citation' }).click();
    await expect(dialog.getByRole('button', { name: 'Copied citation' })).toBeVisible();
  });

  test('bookmarking a search result fills the Bookmarks sheet', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByRole('button', { name: 'Bookmarks' }).click();
    await expect(page.getByText('No bookmarked publications yet')).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();

    await page.getByRole('button', { name: 'Paper search' }).click();
    const searchDialog = page.getByRole('dialog');
    await searchDialog.getByPlaceholder("Describe what you're writing about or paste a paragraph...").fill('P3HT');
    await searchDialog.getByRole('button', { name: 'Search' }).click();
    await expect(searchDialog.getByText(PAPER_PUB.paper_title)).toBeVisible();
    await searchDialog.getByRole('button', { name: 'Bookmark publication' }).click();
    await searchDialog.getByRole('button', { name: 'Close' }).click();

    await page.getByRole('button', { name: 'Bookmarks' }).click();
    await expect(page.getByText(PAPER_PUB.paper_title)).toBeVisible();
    await expect(page.getByText('1 bookmarked publication')).toBeVisible();
  });

  test('Docs sheet keeps the rest of the app visible', async ({ page }) => {
    await page.route('**/docs/**', async route => {
      const url = route.request().url();
      if (url.includes('/docs/') && !url.split('/docs/')[1]?.includes('.')) {
        await route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: '<!doctype html><html><body><h1>FAIR2WISE Documentation</h1><p>Home</p></body></html>',
        });
        return;
      }
      await route.continue();
    });
    await page.getByRole('button', { name: 'Documentation' }).click();
    await expect(page.getByRole('heading', { name: 'Documentation' })).toBeVisible();
    await expect(page.getByTitle('FAIR2WISE Documentation')).toBeVisible();
    const frame = page.frameLocator('iframe[title="FAIR2WISE Documentation"]');
    await expect(frame.getByRole('heading', { name: 'FAIR2WISE Documentation' })).toBeVisible();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
  });
});
