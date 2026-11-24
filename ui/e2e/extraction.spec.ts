import { expect, test } from '@playwright/test';
import { mockAgent } from './fixtures/agent';
import { mockLibrary, mockPaper } from './fixtures/library';

const PAPERS = [
  mockPaper('rs-a', 'rsoxs'),
  mockPaper('rs-b', 'rsoxs'),
  mockPaper('tomo-a', 'tomo'),
  mockPaper('tomo-b', 'tomo'),
  mockPaper('tomo-c', 'tomo'),
];

// A8 (O-U4): the Extract tab starts a KG from one folder of the library and follows the run.
test.describe('Extraction tab', () => {
  test('starts a new KG from one parent folder and shows the run with its log', async ({ page }) => {
    const jobPosts: Array<Record<string, unknown>> = [];
    const estimatePosts: Array<Record<string, unknown>> = [];
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS, jobPosts, estimatePosts });
    await page.goto('/');
    await page.getByRole('link', { name: 'Extract' }).click();

    await page.getByLabel('KG name', { exact: true }).fill('tomo_kg');
    await page.getByLabel('Topic').fill('tomography');

    const folders = page.getByRole('radiogroup', { name: 'Paper folder' });
    await folders.getByRole('radio', { name: /^tomo \(3\)/ }).click();
    await expect(page.getByRole('list', { name: 'Papers' }).getByRole('listitem')).toHaveCount(3);
    await page.getByRole('checkbox', { name: /Select all shown/ }).check();
    await expect(page.getByTestId('paper-count')).toHaveText('3 of 5 selected');

    await page.getByRole('button', { name: 'Estimate cost' }).click();
    await expect(page.getByTestId('estimate-summary')).toContainText('3 papers, 30 pages');
    expect(estimatePosts).toHaveLength(1);
    expect([...(estimatePosts[0].paper_ids as string[])].sort()).toEqual(['tomo-a', 'tomo-b', 'tomo-c']);

    await page.getByRole('checkbox', { name: 'I confirm this cost estimate' }).check();
    await page.getByRole('button', { name: 'Start extraction' }).click();

    await expect(page.getByTestId('extraction-run')).toBeVisible();
    await expect(page.getByTestId('run-state')).toHaveText(/running/);
    await expect(page.getByRole('log', { name: 'Extraction log' })).toContainText('extraction started');

    expect(jobPosts).toHaveLength(1);
    const submitted = jobPosts[0] as { kind: string; inputs: { params: Record<string, unknown> } };
    expect(submitted.kind).toBe('library_extract');
    expect(submitted.inputs.params).toMatchObject({
      bundle: 'tomo_kg', topic: 'tomography', schema: 'matkg_schema@0.3.1', model: 'cheap-model',
      confirmation: 'e2e-confirmation',
    });
    expect([...(submitted.inputs.params.papers as string[])].sort()).toEqual(['tomo-a', 'tomo-b', 'tomo-c']);
  });

  test('re-attaches to a running extraction after a reload and cancels it', async ({ page }) => {
    const cancels: string[] = [];
    await mockAgent(page);
    await mockLibrary(page, {
      papers: PAPERS, cancels,
      jobs: [{ id: 'job9', state: 'running', progress: { done: 1, total: 4 }, logs: ['paper 1 of 4 done'] }],
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Extract' }).click();
    await expect(page.getByTestId('extraction-run')).toBeVisible();
    await page.reload();

    await expect(page.getByTestId('extraction-run')).toBeVisible();
    await expect(page.getByTestId('run-progress-text')).toContainText('1 of 4 papers (25%)');
    await expect(page.getByRole('log', { name: 'Extraction log' })).toContainText('paper 1 of 4 done');

    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect.poll(() => cancels).toEqual(['job9']);
  });

  test('offers the model list from the server and blocks Start until the cost is confirmed', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, { papers: PAPERS, models: ['cheap-model', 'big-model'] });
    await page.goto('/');
    await page.getByRole('link', { name: 'Extract' }).click();

    await expect(page.locator('#kg-model option')).toHaveText(['cheap-model', 'big-model']);
    await expect(page.getByRole('button', { name: 'Start extraction' })).toBeDisabled();
    await expect(page.getByRole('checkbox', { name: 'I confirm this cost estimate' })).toBeDisabled();
  });

  test('shows live progress, KG growth and filterable warnings for a running extraction', async ({ page }) => {
    await mockAgent(page);
    await mockLibrary(page, {
      papers: PAPERS,
      jobs: [{
        id: 'job7', state: 'running', progress: { done: 2, total: 5 },
        summary: {
          outcome: 'running', state: 'running', started_at: '2026-11-03T12:00:00Z', finished_at: null, elapsed_seconds: 95,
          stage: { name: 'run', status: 'running', phase: 'extracting' }, current_paper: 'tomo-b.pdf',
          papers: { total: 5, done: 2, failed: 1, skipped: 0, failed_list: [{ name: 'tomo-a.pdf', reason: 'the PDF could not be read' }] },
          growth: { entities_new: 30, entities_merged: 8, relations_new: 11, relations_merged: 3, entities_total: 38, relations_total: 14 },
          counters: { paper_pages_done: 3, paper_pages_total: 9 },
          params: { bundle: 'tomo_kg', model: 'cheap-model', backend: 'cborg' }, error: null,
          logs: { kept: 3, total: 3, cap: 2000 },
        },
        logs: [
          'paper 1 of 5: extracting tomo-a.pdf',
          { message: 'tomo-a.pdf: the PDF could not be read', level: 'warning' },
          { message: "CBORG refused this machine's address; check network egress", level: 'error' },
        ],
      }],
    });
    await page.goto('/');
    await page.getByRole('link', { name: 'Extract' }).click();

    await expect(page.getByTestId('run-progress-text')).toContainText('2 of 5 papers done, 1 failed');
    await expect(page.getByTestId('run-current-paper')).toContainText('Now: tomo-b.pdf (page 3 of 9)');
    await expect(page.getByTestId('run-growth')).toContainText('30 new · 8 merged');
    await expect(page.getByRole('list', { name: 'Stage of the current paper' }).getByText('extracting', { exact: true }))
      .toHaveAttribute('aria-current', 'step');

    const log = page.getByRole('log', { name: 'Extraction log' });
    await expect(log).toContainText('paper 1 of 5: extracting tomo-a.pdf');
    await page.getByRole('button', { name: /Warnings and errors/ }).click();
    await expect(log).not.toContainText('paper 1 of 5');
    await expect(log).toContainText('the PDF could not be read');
    await expect(log).toContainText('check network egress');
    await expect(page.getByTestId('log-count')).toContainText('Showing all 3 lines');
  });
});
