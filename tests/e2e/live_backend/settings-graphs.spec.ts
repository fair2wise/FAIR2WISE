/**
 * T18B: settings round trip through the real PUT/GET /settings (save, reload),
 * the Source RAG toggle reaching retrieval, and multi-graph JSON citations.
 */
import { expect, test } from '@playwright/test';
import {
  askQuestion, expectNothingPublished, liveRun, observations, openApp, openSettings, publicationSnapshot,
  recordApi, resetBackend, shot, waitForAnswer,
} from './live';

async function backendSettings() {
  return (await fetch(`${liveRun().apiBase}/settings`)).json();
}

test.describe('live settings', () => {
  test.beforeEach(async () => { await resetBackend(); });
  test.afterAll(async () => { await resetBackend(); });

  test('Source RAG toggle saves, survives reload, reaches retrieval and toggles back', async ({ page }) => {
    const before = publicationSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    let dialog = await openSettings(page);
    const rag = () => page.getByRole('dialog').getByRole('checkbox', { name: /Source RAG/ });
    await expect(rag()).not.toBeChecked();
    await rag().check();
    const saved = page.waitForResponse(r => r.url().endsWith('/settings') && r.request().method() === 'PUT');
    await dialog.getByRole('button', { name: 'Save Preferences' }).click();
    const put = await saved;
    expect(put.status()).toBe(200);
    expect(put.request().postDataJSON().source_rag).toBe(true);
    expect((await put.json()).source_rag).toBe(true);
    expect((await backendSettings()).source_rag).toBe(true);

    await page.reload();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    dialog = await openSettings(page);
    await expect(rag()).toBeChecked();
    await dialog.getByRole('button', { name: 'Close' }).click();

    const callsBefore = observations().retrieval_calls.length;
    await askQuestion(page, 'What is Term A resonant scattering?');
    await waitForAnswer(page);
    const turn = seen.filter(s => s.path === '/chat/stream').pop()!.body as Record<string, any>;
    expect(turn.source_rag).toBe(true);
    expect(turn.ui_settings.sourceRag).toBe(true);
    const calls = observations().retrieval_calls.slice(callsBefore);
    expect(calls.map(c => c.source_rag)).toContain(true);

    dialog = await openSettings(page);
    await rag().uncheck();
    const savedOff = page.waitForResponse(r => r.url().endsWith('/settings') && r.request().method() === 'PUT');
    await dialog.getByRole('button', { name: 'Save Preferences' }).click();
    expect((await (await savedOff).json()).source_rag).toBe(false);
    expect((await backendSettings()).source_rag).toBe(false);
    await expectNothingPublished(before, seen);
  });

  test('JSON multi-graph selection saves and cites nodes from both graphs', async ({ page }) => {
    const before = publicationSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    const dialog = await openSettings(page);
    await dialog.getByRole('radio', { name: /JSON/ }).click();
    const v1 = dialog.getByRole('checkbox', { name: /matkg_contract_v1\.json/ });
    const v2 = dialog.getByRole('checkbox', { name: /matkg_contract_v2\.json/ });
    await expect(v1).toBeVisible();
    if (!(await v1.isChecked())) await v1.check();
    if (!(await v2.isChecked())) await v2.check();
    const saved = page.waitForResponse(r => r.url().endsWith('/settings') && r.request().method() === 'PUT');
    await dialog.getByRole('button', { name: 'Save Preferences' }).click();
    const put = await saved;
    expect(put.status()).toBe(200);
    const sent = put.request().postDataJSON();
    expect(sent.graph_source).toBe('json');
    expect(sent.json_graph_paths).toEqual(expect.arrayContaining(['storage/kg/matkg_contract_v1.json', 'storage/kg/matkg_contract_v2.json']));
    // Finding (baseline behaviour, not changed here): the frozen UI also keeps its own
    // default paths (matkg_rsoxs_v1 / matkg_bl1101_v1) that this server does not have;
    // the server stores them and skips the missing files at query time.
    const server = await backendSettings();
    expect(server.graph_source).toBe('json');
    expect(server.json_graph_paths).toEqual(sent.json_graph_paths);

    // Reload: the server's selection drives the dialog.
    await page.reload();
    const again = await openSettings(page);
    await expect(again.getByRole('radio', { name: /JSON/ })).toBeChecked();
    await expect(again.getByRole('checkbox', { name: /matkg_contract_v1\.json/ })).toBeChecked();
    await expect(again.getByRole('checkbox', { name: /matkg_contract_v2\.json/ })).toBeChecked();
    await again.getByRole('button', { name: 'Close' }).click();

    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'multi-graph question about Term A and Beta film');
    await streamed;
    await waitForAnswer(page);
    await expect(page.locator('[data-citation-chip]')).toHaveCount(2);
    await expect(page.getByRole('button', { name: 'Citation 1: Term A' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Citation 2: Beta film' })).toBeVisible();
    await expect(page.locator('[data-citation-ref="1"]')).toContainText('Term A');
    await expect(page.locator('[data-citation-ref="2"]')).toContainText('Beta film');
    await page.getByRole('button', { name: 'Citation 2: Beta film' }).hover();
    await expect(page.getByRole('tooltip')).toContainText('Material');
    await shot(page, 'multi-graph-citations');
    await page.getByRole('button', { name: 'Citation 2: Beta film' }).click();
    await expect(page.locator('[data-cite-focus-id="matkg:Beta"]')).toBeVisible();
    await expectNothingPublished(before, seen);
  });
});
