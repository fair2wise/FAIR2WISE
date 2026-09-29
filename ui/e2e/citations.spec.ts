import { expect, test } from '@playwright/test';
import { askQuestion, mockAgent, waitForTypedAnswer } from './fixtures/agent';

test.describe('citations', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await askQuestion(page, 'Cite P3HT, a PDF, ops notes, and Materials Project.');
    await waitForTypedAnswer(page);
  });

  test('chips keep surrounding words and open a labeled node', async ({ page }) => {
    await expect(page.getByText(/P3HT/).first()).toBeVisible();
    await expect(page.getByText(/is used for resonant soft X-ray scattering/)).toBeVisible();
    await expect(page.getByText(/See /)).toBeVisible();
    await expect(page.getByText(/and beamline notes/)).toBeVisible();

    const chips = page.locator('[data-citation-chip]');
    await expect(chips).toHaveCount(4);

    await page.getByRole('button', { name: 'Citation 1: P3HT' }).hover();
    await expect(page.getByRole('tooltip')).toContainText('P3HT');
    await expect(page.getByRole('tooltip')).toContainText('Material');
    await expect(page.getByRole('tooltip')).toContainText(/rsoxs_v3|KG/);

    await page.getByRole('button', { name: 'Citation 1: P3HT' }).click();
    await expect(page.locator('[data-cite-focus-id="matkg:p3ht"]')).toBeVisible();
    await expect(page.getByText('Pinned')).toBeVisible();
    await expect(page.getByText('P3HT').nth(0)).toBeVisible();
    await expect(page.getByText('Material', { exact: false }).first()).toBeVisible();
    await expect(page.getByText('Unknown')).toHaveCount(0);
  });

  test('bibliography lists KG, PDF, ops, and Materials Project', async ({ page }) => {
    await expect(page.getByText('References')).toBeVisible();
    await expect(page.locator('[data-citation-ref="1"]')).toContainText(/KG/i);
    await expect(page.locator('[data-citation-ref="2"]')).toContainText(/XRAY1\.pdf|Paper|RAG/i);
    await expect(page.locator('[data-citation-ref="3"]')).toContainText(/Ops/i);
    await expect(page.locator('[data-citation-ref="4"]')).toContainText(/Materials Project/i);

    await page.locator('[data-citation-ref="1"]').click();
    await expect(page.locator('[data-cite-focus-id="matkg:p3ht"]')).toBeVisible();
  });
});
