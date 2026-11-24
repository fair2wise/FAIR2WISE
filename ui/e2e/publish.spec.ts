import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';
import { mockLibrary, mockPaper } from './fixtures/library';

const PAPERS = [mockPaper('rs-a', 'rsoxs')];

async function chooseBundle(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('link', { name: 'Extract' }).click();
  await page.getByRole('radio', { name: 'Continue existing KG' }).click();
  await page.getByLabel('Existing KG', { exact: true }).selectOption('bundle:demo');
}

// Publish to Tiled (owner decision 2026-11-10): export, adopt and stage a plan from one button; the owner approves
// on Pending Approvals with the existing route, never here.
test.describe('Publish to Tiled', () => {
  test('confirms, follows the job and links to Pending Approvals without approving anything', async ({ page }) => {
    const posts: Array<Record<string, unknown>> = [];
    await mockAgent(page, { canPublish: true, canApprove: true, approvals: { pending: { items: [], counts: {}, total: 0, includes: {}, generated_at: null } } });
    await mockLibrary(page, { papers: PAPERS, bundles: ['demo'], publish: { posts } });
    await chooseBundle(page);

    await page.getByRole('button', { name: 'Publish to Tiled' }).click();
    const dialog = page.getByTestId('publish-dialog');
    await expect(dialog).toContainText('matkg_demo.json');
    // The Finch stylesheet's `hsl(var(--token))` utilities beat Tailwind's for the shadcn tokens, which left this
    // dialog transparent and off-centre (pilot 2026-11-24): it has to be opaque, centred and its confirm button filled.
    await expect(dialog).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    const box = await dialog.boundingBox();
    const viewport = page.viewportSize();
    expect(box && viewport && Math.abs(box.x + box.width / 2 - viewport.width / 2) < 2).toBe(true);
    await expect(dialog.getByRole('button', { name: 'Publish to Tiled' })).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(page.getByTestId('publish-ref')).toHaveText('demo/v2');
    await expect(dialog).toContainText('The plan still needs your approval');

    await dialog.getByRole('button', { name: 'Publish to Tiled' }).click();
    await expect(page.getByTestId('publish-success')).toContainText('abcdef012345');
    await expect(page.getByTestId('publish-success')).toContainText('12 creates, 7 links');
    expect(posts).toEqual([{ confirm: false, again: false }, { confirm: true, again: false }]);

    await page.getByRole('link', { name: 'Open Pending Approvals' }).click();
    await expect(page).toHaveURL(/\/approvals$/);
  });

  test('shows the refused disclosure rule', async ({ page }) => {
    const posts: Array<Record<string, unknown>> = [];
    await mockAgent(page, { canPublish: true });
    await mockLibrary(page, {
      papers: PAPERS, bundles: ['demo'],
      publish: { posts, failure: { reason: 'disclosure_refused', message: 'disclosure_refused: refused this KG (R7 x2)' } },
    });
    await chooseBundle(page);
    await page.getByRole('button', { name: 'Publish to Tiled' }).click();
    await page.getByTestId('publish-dialog').getByRole('button', { name: 'Publish to Tiled' }).click();
    await expect(page.getByRole('alert')).toContainText('R7 x2');
    await expect(page.getByTestId('publish-success')).toHaveCount(0);
  });

  test('blocks Confirm when the image has no commit label', async ({ page }) => {
    await mockAgent(page, { canPublish: true });
    await mockLibrary(page, {
      papers: PAPERS, bundles: ['demo'],
      publish: { posts: [], problems: [{ reason: 'no_commit_label', code: 'unsupported_capability',
        message: 'no_commit_label: this image has no commit label (F2W_CODE_COMMIT)' }] },
    });
    await chooseBundle(page);
    await page.getByRole('button', { name: 'Publish to Tiled' }).click();
    await expect(page.getByRole('alert')).toContainText('no commit label');
    await expect(page.getByTestId('publish-dialog').getByRole('button', { name: 'Publish to Tiled' })).toBeDisabled();
  });

  test('is hidden unless the server says can_publish', async ({ page }) => {
    await mockAgent(page, { canPublish: false });
    await mockLibrary(page, { papers: PAPERS, bundles: ['demo'], publish: { posts: [] } });
    await chooseBundle(page);
    await expect(page.getByRole('button', { name: 'Export' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Publish to Tiled' })).toHaveCount(0);
  });
});
