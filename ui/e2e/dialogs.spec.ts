import { expect, test, type Page } from '@playwright/test';
import { mockAgent, openSettings } from './fixtures/agent';
import { mockCandidate, mockLibrary, mockPaper } from './fixtures/library';

// Every dialog, sheet and panel of the app is opened at two desktop widths and checked in the real layout: a centred
// dialog lies wholly inside the viewport and is opaque, and no element is translated twice. The double translation
// came from Finch's Tailwind v3 stylesheet, which moves `-translate-x-1/2` with `transform` while Tailwind v4 moves it
// with `translate`: the schema, Publish and Approvals dialogs landed above the top-left corner on the pilot (2026-11-25).
// Pages that use a translate utility must write it as `[translate:...]`, which Finch does not know.

const SHA = 'd'.repeat(64);
const approvals = {
  items: [{
    kind: 'publication', id: 'job-1', job_id: 'job-1', job_kind: 'publish', corpus_scope: ['rsoxs'], target: 'http://127.0.0.1:8765/api#f2w',
    snapshot_ref: 'rsoxs/v1', reason: null,
    plan: { plan_id: 'p1', plan_sha256: SHA, snapshot_id: 'v1', base_revision: 'r0', creates: 3, links: 2, supersessions: 0, affected: 3 },
    submitted_at: '2026-11-09T10:00:00+00:00', requested_by: 'local-operator',
    approve: { method: 'POST', path: 'v1/publications/job-1/approval', body: { plan_sha256: SHA } }, reject: { method: 'POST', path: 'v1/jobs/job-1/cancel', body: null },
  }],
  total: 1, counts: { publication: 1 }, includes: { plans: true, review_cases: true, cost_control: true, merge_apply: false }, generated_at: '2026-11-09T10:05:00+00:00',
};
const PAPERS = [mockPaper('rs-a', 'rsoxs'), mockPaper('tomo-a', 'tomo')];
const SCHEMA_TEXT = 'id: https://example.org/user/catfish\nname: catfish\nversion: 0.1.0\nclasses:\n  Thing:\n    description: Anything.\n';

async function boot(page: Page) {
  await mockAgent(page, { canPublish: true, canApprove: true, approvals: { pending: approvals } });
  await mockLibrary(page, {
    papers: PAPERS, bundles: ['demo'], publish: { posts: [] },
    harvestRuns: [{ id: 'h1', topic: 'queue', category: 'rsoxs', candidates: [mockCandidate('one', { title: 'Paper one', status: 'paywalled' })] }],
    harvestMatches: { 'scan.pdf': { kind: 'weak' } },
  });
  await page.route('**/v1/library/schemas/user', route => route.fulfill({ json: { schemas: [] } }));
  await page.route('**/v1/library/schemas/validate', route => route.fulfill({
    json: { ok: true, problems: [], stats: { bytes: SCHEMA_TEXT.length, classes: 1, slots: 0 }, note: 'n', checked: ['size and encoding'], fixable: false },
  }));
  await page.goto('/');
}

/** Elements translated by both the `translate` and the `transform` property, the double-translation signature. */
async function doubleTranslated(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const style = getComputedStyle(el);
      const identity = style.transform === 'none' || style.transform === 'matrix(1, 0, 0, 1, 0, 0)';
      // An animation in flight also sets `transform`; only settled elements count.
      if (style.translate !== 'none' && !identity && el.getAnimations().length === 0) out.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 80)}`);
    }
    return out;
  });
}

async function checkCentred(page: Page, dialog: ReturnType<Page['getByTestId']>) {
  await expect(dialog).toBeVisible();
  await page.waitForTimeout(400); // the open animation
  const box = await dialog.boundingBox();
  const view = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(view.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(view.height);
  const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
  expect(Math.abs(box!.x + box!.width / 2 - clientWidth / 2)).toBeLessThan(12);
  await expect(dialog).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  expect(await doubleTranslated(page)).toEqual([]);
}

for (const width of [1280, 1600]) {
  test.describe(`dialogs at ${width} wide`, () => {
    test.use({ viewport: { width, height: Math.round(width * 0.5625) } });

    test('schema panel', async ({ page }) => {
      await boot(page);
      await page.getByRole('link', { name: 'Extract' }).click();
      await page.getByTestId('open-schema-panel').click();
      await page.getByTestId('schema-textarea').fill(SCHEMA_TEXT);
      const dialog = page.getByTestId('schema-dialog');
      await checkCentred(page, dialog);
      // The prompt box and its Send button are inside the viewport, not cut off at the top.
      const first = await dialog.locator('textarea').first().boundingBox();
      expect(first!.y).toBeGreaterThanOrEqual(0);
    });

    test('Publish to Tiled', async ({ page }) => {
      await boot(page);
      await page.getByRole('link', { name: 'Extract' }).click();
      await page.getByRole('radio', { name: 'Continue existing KG' }).click();
      await page.getByLabel('Existing KG', { exact: true }).selectOption('bundle:demo');
      await page.getByRole('button', { name: 'Publish to Tiled' }).click();
      await checkCentred(page, page.getByTestId('publish-dialog'));
    });

    test('Approvals confirm', async ({ page }) => {
      await boot(page);
      await page.getByRole('link', { name: 'Pending Approvals' }).click();
      await page.getByRole('button', { name: 'Approve', exact: true }).click();
      await checkCentred(page, page.getByRole('alertdialog'));
    });

    test('Settings and the model chooser', async ({ page }) => {
      await boot(page);
      await openSettings(page);
      await page.waitForTimeout(700);
      expect(await doubleTranslated(page)).toEqual([]);
      const picker = page.locator('#cborg-model-select');
      if (await picker.isVisible()) {
        await picker.click();
        await checkCentred(page, page.getByRole('dialog').filter({ hasText: 'Choose CBORG model' }));
      }
    });

    test('harvest PDF confirmation', async ({ page }) => {
      await boot(page);
      await page.getByRole('link', { name: 'Library' }).click();
      await page.getByRole('tab', { name: 'Harvester' }).click();
      await page.getByRole('button', { name: /queue/ }).click();
      await page.getByTestId('harvest-file-one').setInputFiles({ name: 'scan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n') });
      await checkCentred(page, page.getByTestId('harvest-confirm-dialog'));
    });

    for (const [button, name] of [['Paper search', 'paper search'], ['Search chats', 'search chats'], ['Bookmarks', 'bookmarks'], ['Documentation', 'docs']] as const) {
      test(`${name} sheet`, async ({ page }) => {
        await boot(page);
        await page.getByRole('button', { name: button }).click();
        const sheet = page.getByRole('dialog');
        await expect(sheet).toBeVisible();
        await page.waitForTimeout(700);
        const box = await sheet.boundingBox();
        const view = page.viewportSize()!;
        expect(box!.y).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(view.width + 1);
        expect(await doubleTranslated(page)).toEqual([]);
      });
    }
  });
}
