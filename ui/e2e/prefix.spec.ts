import { expect, test } from '@playwright/test';
import { mockAgent, PREFIXED_API_BASE } from './fixtures/agent';

// T24C: only meaningful for the prefixed build (playwright.prefix.config.ts).
test.describe('served under /fair2wise', () => {
  test.skip(!PREFIXED_API_BASE, 'prefixed build only');

  test('bare host redirects into the UI and the API is same-origin /fair2wise/api', async ({ page, baseURL }) => {
    const apiCalls: string[] = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname.includes('/settings') || url.pathname.includes('/graph')) apiCalls.push(request.url());
    });
    await mockAgent(page);
    await page.goto('/');
    await expect(page).toHaveURL(`${baseURL}/fair2wise/`);
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect.poll(() => apiCalls.length).toBeGreaterThan(0);
    for (const call of apiCalls) {
      expect(call.startsWith(`${baseURL}/fair2wise/api/`)).toBe(true);
    }
  });

  test('deep links get the SPA shell and the router works under the prefix', async ({ page, baseURL }) => {
    await mockAgent(page);
    // A query deep link to the UI root renders the app.
    await page.goto('/fair2wise/?session=abc');
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    // Any other path in the UI namespace gets index.html (not a 404) and the router's
    // basename: the shell renders and the sidebar's home link points back to /fair2wise.
    // (The UI defines one route, "/", so the main pane is empty there, as unprefixed.)
    await page.goto('/fair2wise/some/deep/link');
    await expect(page.getByRole('heading', { name: 'FAIR2WISE' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'FAIR2WISE' })).toHaveAttribute('href', '/fair2wise');
    await page.getByRole('link', { name: 'FAIR2WISE' }).click();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    await expect(page).toHaveURL(`${baseURL}/fair2wise`);
    const logo = page.getByRole('img', { name: 'FAIR2WISE' }).first();
    await expect(logo).toHaveAttribute('src', '/fair2wise/wise_owl.svg');
    await expect.poll(() => logo.evaluate(img => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    const icon = await page.locator('link[rel="icon"]').getAttribute('href');
    expect(icon).toBe('/fair2wise/wise_owl.svg');
    const scripts = await page.locator('script[type="module"]').evaluateAll(els =>
      els.map(el => (el as HTMLScriptElement).src));
    expect(scripts.every(src => src.startsWith(`${baseURL}/fair2wise/assets/`))).toBe(true);
  });

  test('docs open from /fair2wise/docs/ and a missing asset is 404', async ({ page, request }) => {
    await mockAgent(page);
    await page.route('**/fair2wise/docs/**', route => route.fulfill({
      status: 200, contentType: 'text/html', body: '<!doctype html><h1>FAIR2WISE Documentation</h1>',
    }));
    await page.goto('/fair2wise/');
    await page.getByRole('button', { name: 'Documentation' }).click();
    await expect(page.locator('iframe[title="FAIR2WISE Documentation"]')).toHaveAttribute('src', '/fair2wise/docs/');
    const missing = await request.get('/fair2wise/assets/missing-chunk.js');
    expect(missing.status()).toBe(404);
    const build = await (await request.get('/fair2wise/f2w-build.json')).json();
    expect(build).toEqual({
      public_prefix: '/fair2wise', ui_base: '/fair2wise/', api_base: '/fair2wise/api', docs_url: '/fair2wise/docs/',
    });
  });
});
