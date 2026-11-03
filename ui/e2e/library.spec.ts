import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';
import { mockLibrary, mockPaper } from './fixtures/library';

const PAPERS = [
  mockPaper('rs-a', 'rsoxs', { relpath: 'rsoxs/2013/rs-a.pdf', doi: '10.1/rsoxs-a' }),
  mockPaper('rs-b', 'rsoxs'),
  mockPaper('tomo-a', 'tomo'),
  mockPaper('loose', ''),
];

// A8 (O-U4): the Library tab lists the mounted papers folder, grouped by its top-level folders.
test.describe('Library tab', () => {
  test('is hidden when the server has no library routes (ALS image)', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page);
    await page.goto('/');
    await expect(page.getByRole('link', { name: 'FAIR2WISE' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Library' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Extract' })).toHaveCount(0);
  });

  test('shows the folders as categories and filters the table by one', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS });
    await page.goto('/');
    await page.getByRole('link', { name: 'Library' }).click();

    const table = page.getByRole('table', { name: 'Library papers' });
    await expect(table.getByRole('row')).toHaveCount(PAPERS.length + 1);

    const folders = page.getByRole('radiogroup', { name: 'Folder' });
    await expect(folders.getByRole('radio', { name: /^All \(4\)/ })).toHaveAttribute('aria-checked', 'true');
    await expect(folders.getByRole('radio', { name: /^rsoxs \(2\)/ })).toBeVisible();
    await expect(folders.getByRole('radio', { name: /^tomo \(1\)/ })).toBeVisible();
    await expect(folders.getByRole('radio', { name: /^Uncategorized \(1\)/ })).toBeVisible();

    await folders.getByRole('radio', { name: /^rsoxs/ }).click();
    await expect(folders.getByRole('radio', { name: /^rsoxs/ })).toHaveAttribute('aria-checked', 'true');
    await expect(table.getByRole('row')).toHaveCount(3);
    await expect(table.getByText('rs-a.pdf')).toBeVisible();
    await expect(table.getByText('tomo-a.pdf')).toHaveCount(0);
    // The nested year folder stays inside its category; the cell tooltip shows the full path.
    await expect(table.getByRole('cell', { name: 'rsoxs' }).first()).toHaveAttribute('title', 'rsoxs/2013/rs-a.pdf');

    await folders.getByRole('radio', { name: /^All/ }).click();
    await expect(table.getByRole('row')).toHaveCount(PAPERS.length + 1);
  });

  test('uploads a PDF into the library root', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS });
    await page.goto('/');
    await page.getByRole('link', { name: 'Library' }).click();
    await expect(page.getByRole('table', { name: 'Library papers' })).toBeVisible();

    await page.locator('input[type="file"]').setInputFiles({
      name: 'fresh.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n'),
    });
    await expect(page.getByRole('table', { name: 'Library papers' }).getByText('fresh.pdf')).toBeVisible();
  });
});
