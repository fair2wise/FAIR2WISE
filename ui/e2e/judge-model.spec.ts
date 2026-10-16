import { expect, test } from '@playwright/test';
import { mockAgent, openSettings } from './fixtures/agent';

// O-U2: the validation judge is picked from a dropdown of the models the server lists now.
test.describe('validation judge model', () => {
  test('dropdown lists the live models and saves the judge without touching PUT /settings', async ({ page }) => {
    const judgePuts: Array<Record<string, unknown>> = [];
    const settingsPuts: string[] = [];
    page.on('request', request => {
      if (request.method() === 'PUT' && new URL(request.url()).pathname.endsWith('/settings')) {
        settingsPuts.push(request.url());
      }
    });
    await mockAgent(page, { judgeModels: ['live-judge-a', 'live-judge-b'], judgePuts });
    await page.goto('/');
    await openSettings(page);

    const dialog = page.getByRole('dialog');
    const select = dialog.locator('#judge-model-select');
    await expect(select).toBeVisible();
    await expect(select.locator('option')).toHaveText(['No judge selected', 'live-judge-a', 'live-judge-b']);
    await expect(dialog.getByTestId('judge-model-source')).toContainText('2 models currently offered by CBORG');

    await select.selectOption('live-judge-b');
    await dialog.getByRole('button', { name: 'Save judge model' }).click();
    await expect(dialog.getByRole('status')).toContainText('Judge model saved: live-judge-b');
    expect(judgePuts).toEqual([{ judge_model: 'live-judge-b' }]);
    expect(settingsPuts).toEqual([]);
  });

  test('hidden for a caller who may not change it (ALS non-admin)', async ({ page }) => {
    await mockAgent(page, { judgeModels: 'forbidden' });
    await page.goto('/');
    await openSettings(page);
    await expect(page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ })).toBeVisible();
    await expect(page.getByTestId('judge-model-setting')).toHaveCount(0);
  });

  test('hidden when the server has no judge route (legacy app)', async ({ page }) => {
    await mockAgent(page);
    await page.goto('/');
    await openSettings(page);
    await expect(page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ })).toBeVisible();
    await expect(page.getByTestId('judge-model-setting')).toHaveCount(0);
  });
});
