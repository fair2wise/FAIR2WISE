/**
 * T18C UI20 (publication search and bookmarks) and D19/O-D2 in the browser: a
 * verified exact quote is shown verbatim, a page dump is clipped, and no SSE
 * event, graph export or other response carries the source chunk.
 */
import { expect, test } from '@playwright/test';
import {
  api, askQuestion, capture, fullShot, openApp, parseSse, recordApi, resetFull, waitForAnswer,
} from './full';

const CHUNK_HEAD = 'Resonant soft X-ray scattering enhances the contrast between chemically similar polymer domains';
const QUOTE = 'The measured scattering intensity then reflects differences in the complex refractive index';

test.describe('publications and disclosure', () => {
  test.beforeEach(async () => { await resetFull(); });

  test('UI20 paper search hits the real route; a bookmark persists across reload', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    await page.getByRole('button', { name: 'Paper search' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByPlaceholder("Describe what you're writing about or paste a paragraph...").fill('resonant scattering');
    const searched = page.waitForResponse(r => r.url().endsWith('/publications/search'));
    await dialog.getByRole('button', { name: 'Search' }).click();
    expect((await searched).status()).toBe(200);
    expect((seen.find(s => s.path === '/publications/search')!.body as any).include_external).toBe(false);
    await expect(dialog.getByText('Synthetic contract paper on resonant scattering')).toBeVisible();
    await dialog.getByRole('button', { name: 'Bookmark publication' }).first().click();
    await dialog.getByRole('button', { name: 'Close' }).click();
    await page.reload();
    await page.getByRole('button', { name: 'Bookmarks' }).click();
    await expect(page.getByText('Synthetic contract paper on resonant scattering')).toBeVisible();
    await expect(page.getByText('1 bookmarked publication')).toBeVisible();
    const fav = await page.evaluate(() => window.localStorage.getItem('fair2wise-publication-favorites-v1'));
    expect(fav).toContain('10.0000/contract.a');
    await fullShot(page, 'bookmarks');
  });

  test('D19/O-D2 exact quote verbatim; page dump clipped; no source chunk in SSE or graph export', async ({ page }) => {
    await openApp(page);
    const q1 = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, '[quote] what does RSoXS contrast depend on?');
    const quoteEvents = parseSse(await (await q1).text());
    await waitForAnswer(page);
    const quoted = quoteEvents[quoteEvents.length - 1].data.answer as string;
    expect(quoted).toContain(`"${QUOTE}"`);
    await expect(page.getByText(QUOTE, { exact: false })).toBeVisible();

    const q2 = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, '[dump] print the page about RSoXS contrast');
    const dumpText = await (await q2).text();
    const dumpEvents = parseSse(dumpText);
    const dumped = dumpEvents[dumpEvents.length - 1].data.answer as string;
    expect(dumped).toContain('source text omitted');
    expect(dumped).toContain('[PDF: synthetic_contrast.pdf p.3]');
    expect(dumpText).not.toContain(CHUNK_HEAD);
    await expect(page.getByRole('button', { name: 'View Knowledge Graph' })).toHaveCount(2, { timeout: 30_000 });
    await expect(page.getByText(CHUNK_HEAD)).toHaveCount(0);
    await fullShot(page, 'disclosure');

    const graph = await api('/graph');
    expect(JSON.stringify(graph.body)).not.toContain(CHUNK_HEAD);
    // Every captured response except the raw trace (trace.read_any: every :local user, O-D2/T21B).
    const leaks = capture().filter(r => !String(r.path).startsWith('/traces/'))
      .filter(r => JSON.stringify(r.response_json ?? r.sse_raw ?? r.response_text ?? '').includes(CHUNK_HEAD))
      .map(r => `${r.method} ${r.path}`);
    expect(leaks).toEqual([]);
  });
});
