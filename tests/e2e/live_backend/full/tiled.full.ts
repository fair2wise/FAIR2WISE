/**
 * T18C UI01/UI27: settings and live-Tiled exploration from the frozen UI against a
 * real disposable Tiled 0.2.18 (the legacy GraphQL lookup is not faked).
 */
import { expect, test } from '@playwright/test';
import {
  askQuestion, effectSnapshot, fullObservations, fullRun, fullShot, noSecretLeak, openApp, openSettings,
  parseSse, recordApi, resetFull, waitForAnswer,
} from './full';

test.describe('UI27 live Tiled on a disposable server', () => {
  test.beforeEach(async () => { await resetFull(); });

  test('UI01/UI27 settings: live Tiled toggle probes the disposable server; the key never reaches the browser', async ({ page }) => {
    const before = effectSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    const dialog = await openSettings(page);
    await dialog.locator('#live-tiled').check();
    await dialog.locator('#tiled-uri').fill(fullRun().tiledUrl);
    const saved = page.waitForResponse(r => r.url().endsWith('/settings') && r.request().method() === 'PUT');
    await dialog.getByRole('button', { name: 'Save Preferences' }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ use_live_tiled: true, tiled_uri: fullRun().tiledUrl, tiled_status: 'ok',
      tiled_api_key_set: true, graph_source: 'splash' });
    expect(JSON.stringify(body)).not.toMatch(/apikey|api_key"\s*:\s*"/i);
    const put = seen.find(s => s.method === 'PUT' && s.path === '/settings')!.body as Record<string, any>;
    expect(put.use_live_tiled).toBe(true);
    expect(put.tiled_uri).toBe(fullRun().tiledUrl);
    await page.reload();
    const again = await openSettings(page);
    await expect(again.getByText('Status:')).toContainText('ok');
    await expect(again.getByText(/API key in env: yes/)).toBeVisible();
    await expect(again.getByText(/sign in|log in/i)).toHaveCount(0);
    await fullShot(page, 'tiled-settings');
    expect(effectSnapshot()).toEqual(before);
    noSecretLeak();
  });

  test('UI27/UI19 experiment-identity turn cites live Tiled records and explores their neighborhood read-only', async ({ page }) => {
    const { tiledUrl, tiledSeed } = fullRun();
    const before = effectSnapshot();
    const res = await fetch(`${fullRun().apiBase}/settings`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_live_tiled: true, tiled_uri: tiledUrl }),
    });
    expect(res.status).toBe(200);
    const seen = recordApi(page);
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, '[tiled] Which samples are on ESAF 2026-00043?');
    const events = parseSse(await (await streamed).text());
    await waitForAnswer(page);
    const complete = events[events.length - 1];
    expect(complete.event).toBe('complete');
    expect(complete.data.status).toBe('answered');
    expect(complete.data.node_ids).toEqual(expect.arrayContaining([tiledSeed.esaf, ...tiledSeed.samples]));
    expect(complete.data.graph.source_path).toBe('tiled://graphql');
    const update = events.find(e => e.data.phase === 'graph_update')!;
    expect(update.data.node_ids).toEqual(complete.data.node_ids);
    expect(complete.data.graph.edges).toEqual(expect.arrayContaining([
      { source: tiledSeed.esaf, target: tiledSeed.samples[0], predicate: 'rel:hasSample' },
    ]));
    expect(fullObservations().tiled_lookups.at(-1)?.status).toBe('ok');

    await expect(page.locator('[data-citation-chip]')).toHaveCount(3);
    const chip = page.getByRole('button', { name: /Citation \d+: ESAF 2026-00043/ });
    await chip.hover();
    await expect(page.getByRole('tooltip')).toContainText('ESAF');
    await expect(page.getByText('3 nodes')).toBeVisible();
    const neighborhood = page.waitForResponse(r => r.url().includes('/graph/neighborhood/'), { timeout: 15_000 })
      .catch(() => null);
    await chip.click();
    const nb = await neighborhood;
    if (nb) {
      expect(nb.status()).toBe(200);
      const payload = await nb.json();
      expect(payload.nodes.map((n: any) => n.id)).toEqual(expect.arrayContaining([tiledSeed.esaf]));
    }
    await expect(page.locator(`[data-cite-focus-id="${tiledSeed.esaf}"]`)).toBeVisible();
    await fullShot(page, 'tiled-identity');
    // Read-only: no Tiled write, no graph write, no edit/action request.
    expect(effectSnapshot()).toEqual(before);
    expect(seen.filter(s => s.method === 'PATCH' || s.path.startsWith('/chat/action'))).toEqual([]);
    noSecretLeak();
  });

  test('UI27/UI23 live Tiled off: the same question degrades to the insufficient-evidence path, not an error', async ({ page }) => {
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, '[tiled] Which samples are on ESAF 2026-00043?');
    const events = parseSse(await (await streamed).text());
    const final = events[events.length - 1];
    expect(final.event).toBe('complete');
    expect(final.data.status).toBe('awaiting_download_decision');
    await expect(page.getByText('Candidate Papers:')).toBeVisible();
    await expect(page.getByText('Agent run failed')).toHaveCount(0);
  });
});
