import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';
import { mockCandidate, mockLibrary, mockPaper } from './fixtures/library';

const PAPERS = [mockPaper('rs-a', 'rsoxs'), mockPaper('tomo-a', 'tomo')];

const FINDS = [
  mockCandidate('oa1', { title: 'Open access RSoXS paper', status: 'downloaded', relpath: 'rsoxs/2021/oa1.pdf' }),
  mockCandidate('dup', { title: 'Already there', status: 'in_library' }),
  mockCandidate('bad', { title: 'Broken download', status: 'failed', detail: 'HTTP 403' }),
  mockCandidate('wall1', { title: 'Behind a paywall', status: 'paywalled' }),
  mockCandidate('wall2', { title: 'Also paywalled', status: 'paywalled', landing_url: 'https://arxiv.org/abs/2101.00001' }),
];

// A8 (O-U4, T46): the Harvester panel of the Library tab searches a topic and files the papers it finds.
test.describe('Harvester panel', () => {
  test('starts a harvest, shows the run and its results, and finishes the paywalled queue by hand', async ({ page }) => {
    const jobPosts: Array<Record<string, unknown>> = [];
    const harvestFiles: Array<{ id: string; name: string }> = [];
    const harvestDismisses: string[] = [];
    const cancels: string[] = [];
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS, jobPosts, harvestFinds: FINDS, harvestFiles, harvestDismisses, cancels });
    await page.goto('/');
    await page.getByRole('link', { name: 'Library' }).click();
    await page.getByRole('tab', { name: 'Harvester' }).click();
    await expect(page.getByRole('tab', { name: 'Harvester' })).toHaveAttribute('aria-selected', 'true');

    const start = page.getByRole('button', { name: 'Start harvest' });
    await expect(start).toBeDisabled();
    await page.getByLabel('Topic').fill('resonant soft x-ray scattering');
    await page.getByLabel('Category').selectOption('rsoxs');
    await page.getByLabel('Schema (label only)').selectOption({ index: 1 });
    await page.getByRole('checkbox', { name: 'OpenAlex' }).click();
    await page.getByLabel('Max downloads').fill('12');
    await expect(start).toBeEnabled();
    await start.click();

    await expect(page.getByTestId('harvest-run')).toBeVisible();
    await expect(page.getByTestId('harvest-state')).toHaveText(/running/);
    await expect(page.getByRole('log', { name: 'Harvest log' })).toContainText('harvest started');

    expect(jobPosts).toHaveLength(1);
    const posted = jobPosts[0] as { kind: string; inputs: { params: Record<string, unknown> } };
    expect(posted.kind).toBe('library_harvest');
    expect(posted.inputs.params).toMatchObject({
      topic: 'resonant soft x-ray scattering', category: 'rsoxs', schema: 'matkg_schema@0.3.1',
      sources: ['arxiv'], max_downloads: 12,
    });

    // Results: the table has the settled outcomes, the queue has the paywalled papers.
    const table = page.getByRole('table', { name: 'Harvest results' });
    await expect(table.getByText('Open access RSoXS paper')).toBeVisible();
    await expect(table.getByText('Downloaded')).toBeVisible();
    await expect(table.getByText('In library')).toBeVisible();
    await expect(table.getByText('Failed')).toHaveAttribute('title', 'HTTP 403');
    const queue = page.getByRole('list', { name: 'Paywalled papers' });
    await expect(queue.getByRole('listitem')).toHaveCount(2);

    const publisher = queue.getByRole('link', { name: /Open publisher page for Behind a paywall/ });
    await expect(publisher).toHaveAttribute('href', 'https://doi.org/10.1/wall1');
    await expect(publisher).toHaveAttribute('target', '_blank');
    await expect(publisher).toHaveAttribute('rel', 'noopener noreferrer');

    // The person saves the PDF in their own browser and drops it on the paper.
    await page.getByTestId('harvest-file-wall1').setInputFiles({
      name: 'behind-a-paywall.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n'),
    });
    await expect.poll(() => harvestFiles).toEqual([{ id: 'wall1', name: 'behind-a-paywall.pdf' }]);
    await expect(queue.getByRole('listitem')).toHaveCount(1);
    await expect(table.getByText('Behind a paywall')).toBeVisible();

    await page.getByRole('button', { name: 'Dismiss Also paywalled' }).click();
    await expect.poll(() => harvestDismisses).toEqual(['wall2']);
    await expect(page.getByText('Nothing is waiting for a PDF.')).toBeVisible();

    // The filed paper shows up in the Papers panel.
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect.poll(() => cancels).toHaveLength(1);
    await page.getByRole('button', { name: 'Back to the form' }).click();
    await expect(page.getByRole('form', { name: 'Start a harvest' })).toBeVisible();
    await page.getByRole('tab', { name: 'Papers' }).click();
    await expect(page.getByRole('table', { name: 'Library papers' }).getByText('behind-a-paywall.pdf')).toBeVisible();
  });

  test('a new category needs a valid name', async ({ page }) => {
    const jobPosts: Array<Record<string, unknown>> = [];
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS, jobPosts });
    await page.goto('/');
    await page.getByRole('link', { name: 'Library' }).click();
    await page.getByRole('tab', { name: 'Harvester' }).click();

    await page.getByLabel('Topic').fill('x-ray ptychography');
    await page.getByLabel('Category').selectOption({ label: 'New category…' });
    await expect(page.getByRole('button', { name: 'Start harvest' })).toBeDisabled();
    await page.getByLabel('New category name').fill('bad name');
    await expect(page.getByLabel('New category name')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('button', { name: 'Start harvest' })).toBeDisabled();
    await page.getByLabel('New category name').fill('ptycho');
    await page.getByRole('button', { name: 'Start harvest' }).click();
    await expect(page.getByTestId('harvest-run')).toBeVisible();
    expect((jobPosts[0] as { inputs: { params: Record<string, unknown> } }).inputs.params.category).toBe('ptycho');
  });

  test('re-attaches to a running harvest after a reload and reopens a past run', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, {
      papers: PAPERS,
      jobs: [{ id: 'h7', kind: 'library_harvest', state: 'running', progress: { done: 2, total: 6 }, logs: ['searching arxiv'] }],
      harvestRuns: [
        { id: 'h7', topic: 'polymer blends', category: 'rsoxs', candidates: [mockCandidate('r1', { title: 'Run seven paper' })] },
        { id: 'h3', topic: 'tomography', category: 'tomo', candidates: [mockCandidate('t1', { title: 'Old tomography paper', status: 'paywalled' })] },
      ],
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Library' }).click();
    await page.getByRole('tab', { name: 'Harvester' }).click();
    await expect(page.getByTestId('harvest-run')).toBeVisible();
    await page.reload();
    await page.getByRole('tab', { name: 'Harvester' }).click();

    await expect(page.getByTestId('harvest-run')).toBeVisible();
    await expect(page.getByTestId('harvest-progress-text')).toContainText('2 of 6 (33%)');
    await expect(page.getByRole('log', { name: 'Harvest log' })).toContainText('searching arxiv');
    await expect(page.getByRole('table', { name: 'Harvest results' }).getByText('Run seven paper')).toBeVisible();

    const runs = page.getByRole('list', { name: 'Harvest runs' });
    await runs.getByRole('button', { name: /tomography/ }).click();
    await expect(runs.getByRole('button', { name: /tomography/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('list', { name: 'Paywalled papers' }).getByText('Old tomography paper')).toBeVisible();
  });

  test('the Extraction tab does not pick up a running harvest', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, {
      papers: PAPERS,
      jobs: [{ id: 'h7', kind: 'library_harvest', state: 'running', logs: [] }],
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Extract' }).click();
    await expect(page.getByLabel('KG name', { exact: true })).toBeVisible();
    await expect(page.getByTestId('extraction-run')).toHaveCount(0);
  });
});
