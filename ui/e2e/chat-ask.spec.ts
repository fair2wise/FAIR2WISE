import { expect, test } from '@playwright/test';
import { askQuestion, mockAgent, waitForTypedAnswer } from './fixtures/agent';

test.describe('chat ask', () => {
  test('sends with Enter, shows progress, then a completed answer', async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await askQuestion(page, 'What is P3HT?');
    await waitForTypedAnswer(page);
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'What is P3HT?' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Copy answer' })).toBeVisible();
  });

  test('Stop while busy returns the composer to Send', async ({ page }) => {
    await mockAgent(page, { hangChatMs: 20_000 });
    await page.goto('/');
    await askQuestion(page, 'A slow question');
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible();
    await page.getByRole('button', { name: 'Stop' }).click();
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeEnabled();
  });

  test('a second turn keeps both user bubbles', async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await askQuestion(page, 'First question about P3HT');
    await waitForTypedAnswer(page);
    await askQuestion(page, 'Follow-up about morphology');
    await waitForTypedAnswer(page);
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'First question about P3HT' })).toBeVisible();
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'Follow-up about morphology' })).toBeVisible();
  });
});
