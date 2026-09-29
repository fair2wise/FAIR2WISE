import { expect, test } from '@playwright/test';
import {
  askQuestion,
  downloadPendingResponse,
  extractionPendingResponse,
  mockAgent,
  openSettings,
} from './fixtures/agent';

test.describe('pending ingest', () => {
  test('download candidates use chat-to-choose copy, not Yes/No', async ({ page }) => {
    await mockAgent(page, { chatQueue: [downloadPendingResponse()] });
    await page.goto('/');
    await askQuestion(page, 'Find a paper about P3HT morphology');
    await expect(page.getByText('Candidate Papers:')).toBeVisible();
    await expect(page.getByText('Resonant soft X-ray scattering of P3HT blends')).toBeVisible();
    await expect(page.getByText(/Ask in chat which paper to download/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Run extraction' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Skip' })).toHaveCount(0);
  });

  test('extraction pending shows Run extraction and Skip', async ({ page }) => {
    await mockAgent(page, { chatQueue: [extractionPendingResponse()] });
    await page.goto('/');
    await askQuestion(page, 'Extract the downloaded P3HT paper');
    await expect(page.getByRole('button', { name: 'Run extraction' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Skip' })).toBeVisible();
  });

  test('JSON settings still warn that download and extract will not run', async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await openSettings(page);
    await expect(page.getByText('JSON mode is strictly for retrieval. The download and extraction agents will not run.')).toBeVisible();
  });
});
