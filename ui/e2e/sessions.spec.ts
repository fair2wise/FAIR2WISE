import { expect, test } from '@playwright/test';
import { askQuestion, mockAgent, waitForTypedAnswer } from './fixtures/agent';

test.describe('sessions', () => {
  test.beforeEach(async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
  });

  test('new chat titles from the first prompt, switch restores bubbles, reload keeps store', async ({ page }) => {
    await askQuestion(page, 'What is P3HT used for in RSoXS?');
    await waitForTypedAnswer(page);
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'What is P3HT used for in RSoXS?' })).toBeVisible();

    await page.getByRole('button', { name: 'New chat' }).click();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect(page.getByText('What is P3HT used for in RSoXS?')).toHaveCount(0);

    await askQuestion(page, 'How do I set the beamline energy?');
    await waitForTypedAnswer(page);

    await page.getByRole('button', { name: 'Search chats' }).click();
    await expect(page.getByRole('heading', { name: 'Search chats' })).toBeVisible();
    await page.getByLabel('Search chat titles').fill('P3HT');
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('What is P3HT used for in RSoXS?')).toBeVisible();
    await expect(dialog.getByText('How do I set the beamline energy?')).toHaveCount(0);

    await dialog.getByText('What is P3HT used for in RSoXS?').click();
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'What is P3HT used for in RSoXS?' })).toBeVisible();

    await page.reload();
    await expect(page.locator('.bg-sky-500').filter({ hasText: 'What is P3HT used for in RSoXS?' })).toBeVisible();
    const stored = await page.evaluate(() => window.localStorage.getItem('fair2wise.chat.sessions.v2'));
    expect(stored).toContain('What is P3HT used for in RSoXS?');
  });

  test('deleting the last chat yields a fresh empty session', async ({ page }) => {
    await askQuestion(page, 'Only session prompt');
    await waitForTypedAnswer(page);

    await page.getByRole('button', { name: 'Search chats' }).click();
    await page.getByRole('button', { name: 'Delete Only session prompt' }).click();
    await expect(page.getByText('Only session prompt')).toHaveCount(0);
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
  });
});
