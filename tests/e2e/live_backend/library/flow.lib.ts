/**
 * Real-backend browser smoke (Tier 2 E): what the owner tests by hand on the pilot, in the order it is done there, in
 * the production UI build against the real API, the real jobs and a real worker. One serial file, so each step builds
 * on the state the earlier ones left on the server (a saved schema, a bundle, an exported file, a harvest run).
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { api, askQuestion, exportedGraph, libRun, noSecretLeak, openApp, parseSse, shot, textPdf, waitForJob } from './lib';

test.describe.configure({ mode: 'serial' });

const KG = 'smoke_kg';
const USER_KG = 'smoke_user_kg';

async function openExtract(page: Page) {
  await openApp(page);
  await page.getByRole('link', { name: 'Extract' }).click();
  // The page re-attaches to the latest run of this server; the form is one click away from it.
  await expect(page.getByTestId('extraction-idle').or(page.getByTestId('extraction-run'))).toBeVisible();
  if (await page.getByTestId('extraction-run').isVisible()) await page.getByRole('button', { name: 'New extraction' }).click();
  await expect(page.getByTestId('extraction-idle')).toBeVisible();
}

async function pickAllPapers(page: Page) {
  // One folder only: the picker lists the papers without a folder filter.
  await expect(page.getByText('blend_scattering.pdf').first()).toBeVisible();
  await page.getByRole('checkbox', { name: /^Select all/ }).check();
  await expect(page.getByTestId('paper-count')).toHaveText('2 of 2 selected');
}

async function estimateAndStart(page: Page) {
  await page.getByRole('button', { name: 'Estimate cost' }).click();
  await expect(page.getByTestId('estimate-summary')).toContainText('2 papers');
  await page.getByRole('checkbox', { name: 'I confirm this cost estimate' }).check();
  const posted = page.waitForResponse(r => r.url().endsWith('/v1/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Start extraction' }).click();
  const handle = await (await posted).json();   // the real handle names the job `job_id`
  expect(handle.job_id).toMatch(/^job-[0-9a-f]{24}$/);
  await expect(page.getByTestId('extraction-run')).toBeVisible();
  return handle.job_id as string;
}

test.describe('library smoke on the real backend', () => {
  test('schema panel: generate a draft, save it as a user schema, preview it on one paper', async ({ page }) => {
    const { schemaName, freeModel } = libRun();
    await openExtract(page);
    await page.getByRole('button', { name: 'Create or edit a schema' }).click();
    const dialog = page.getByTestId('schema-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('#schema-model')).toHaveValue(new RegExp(freeModel.replace('/', '\\/')));

    const generated = page.waitForResponse(r => r.url().endsWith('/v1/library/schemas/generate') && r.request().method() === 'POST');
    await dialog.getByLabel('What should the schema cover?').fill('Loricariid catfish: species, measurements, localities.');
    await dialog.getByRole('button', { name: 'Send' }).click();
    const reply = await (await generated).json();
    expect(reply.stopped ?? null).not.toBe('cancelled');
    await expect(dialog.getByTestId('generate-result')).toBeVisible();
    await expect(dialog.getByTestId('schema-textarea')).toHaveValue(/name: plecos/);
    await expect(dialog.getByTestId('validation-status')).toContainText('No problems found.');

    await dialog.getByLabel('Schema name').fill(schemaName);
    const saved = page.waitForResponse(r => r.url().endsWith('/v1/library/schemas') && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Accept and save' }).click();
    const body = await (await saved).json();
    expect(body.schema?.id ?? body.id).toBe(`user/${schemaName}@v1`);
    await expect(dialog.getByTestId('schema-notice')).toContainText(`user/${schemaName}@v1`);

    await dialog.locator('#preview-paper').selectOption({ index: 1 });
    await dialog.getByRole('button', { name: 'Preview (estimate first)' }).click();
    await expect(dialog.getByTestId('preview-estimate')).toBeVisible();
    await dialog.getByRole('button', { name: 'Confirm and run the preview' }).click();
    await expect(dialog.getByTestId('preview-summary')).toBeVisible({ timeout: 60_000 });
    await shot(page, 'schema-preview');
    const list = await api('/v1/library/schemas/user');
    expect(list.body.schemas.map((s: any) => s.name)).toContain(schemaName);
    noSecretLeak();
  });

  test('extraction on the shipped schema: run, coverage block, export, download, node card', async ({ page }) => {
    await openExtract(page);
    await page.getByLabel('KG name', { exact: true }).fill(KG);
    await page.getByLabel('Topic').fill('polymer blend scattering');
    await pickAllPapers(page);
    const jobId = await estimateAndStart(page);
    const job = await waitForJob(jobId);
    expect(job.state).toBe('succeeded');
    await expect(page.getByTestId('run-state')).toHaveText(/succeeded/i);
    await expect(page.getByRole('log', { name: 'Extraction log' })).toContainText(/extracting 2 papers/);

    // The coverage block: every term of the generated papers has a passage except the one the model invented.
    const coverage = page.getByTestId('run-passages');
    await expect(coverage).toBeVisible();
    const progress = await api(`/v1/jobs/${jobId}/progress`);
    const total = progress.body.summary.passages.total;
    expect(total.terms).toBeGreaterThanOrEqual(12);
    expect(total.uncovered).toBe(1);
    expect(total.with_passage + total.uncovered).toBe(total.terms);
    expect(total.relations_with_evidence).toBeGreaterThan(0);
    await expect(page.getByTestId('run-term-coverage')).toContainText(String(total.with_passage));
    await shot(page, 'extraction-coverage');

    // Export writes the bundle to the release folder; Download streams that file.
    const exported = page.waitForResponse(r => r.url().endsWith(`/v1/library/kgs/${KG}/export`));
    await page.getByRole('button', { name: 'Export' }).click();
    const result = await (await exported).json();
    expect(result.file_name).toBe(`matkg_${KG}.json`);
    const notice = page.getByTestId('export-notice');
    await expect(notice).toBeVisible();
    const link = notice.getByRole('link', { name: `Download matkg_${KG}.json` });
    const href = await link.getAttribute('href');
    expect(href).toContain(`/v1/library/kgs/${KG}/download`);
    const download = await fetch(href!);
    expect(download.status).toBe(200);
    const graph = JSON.parse(await download.text());
    expect(graph.things.length).toBeGreaterThanOrEqual(12);
    const withPassages = graph.things.filter((n: any) => (n.context_snippets || []).length > 0);
    expect(withPassages.length).toBeGreaterThanOrEqual(10);
    expect(exportedGraph(KG).things.length).toBe(graph.things.length);
    await shot(page, 'extraction-exported');

    // The exported file is a selectable graph source; a node card shows the passage recorded for the term.
    const sources = await api('/v1/graph/sources');
    const source = sources.body.sources.find((s: any) => String(s.label || s.id).includes(KG));
    expect(source, JSON.stringify(sources.body).slice(0, 400)).toBeTruthy();
    await openApp(page);   // the KG Viewer is on the chat page; it has its own source selector
    await page.getByRole('button', { name: 'KG Viewer' }).click();
    await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
    await page.getByRole('combobox', { name: 'Graph source' }).selectOption({ value: source.id });
    await expect(page.getByText(`${graph.things.length} nodes`)).toBeVisible();
    await page.getByRole('button', { name: 'Fixed Layout' }).click();   // the SVG layout carries the node ids
    await expect(page.locator('[data-kg-node-id]')).toHaveCount(graph.things.length);
    const rendered = await page.locator('[data-kg-node-id]').evaluateAll(els => els.map(e => e.getAttribute('data-kg-node-id')));
    const node = withPassages.find((n: any) => rendered.includes(n.id));
    expect(node, `no rendered node with passages among: ${rendered.join(', ')}`).toBeTruthy();
    await page.locator(`[data-kg-node-id="${node.id}"]`).first().click({ force: true });
    await expect(page.getByRole('button', { name: 'Close node details' })).toBeVisible();
    const snippets = page.getByTestId('paper-snippets').first();
    await expect(snippets).toBeVisible();
    await snippets.getByRole('button', { name: /Show supporting text/ }).click();
    await expect(snippets.getByText(node.context_snippets[0].text.slice(0, 40), { exact: false }).first()).toBeVisible();
    await expect(snippets.getByTestId('passage-kind').first()).toHaveText(/\[(quoted|located|table row)\]/);
    await shot(page, 'node-card-passages');
    noSecretLeak();
  });

  test('Find supporting text on the exported KG runs to the end and reports per paper', async ({ page }) => {
    await openExtract(page);
    await page.getByRole('radiogroup', { name: 'Continue or new' }).getByRole('radio', { name: 'Continue existing KG' }).click();
    await page.getByRole('combobox', { name: 'Existing KG' }).selectOption({ label: KG });
    const posted = page.waitForResponse(r => r.url().endsWith(`/v1/library/kgs/${KG}/passages`) && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Find supporting text' }).click();
    const handle = await (await posted).json();
    expect(handle.job_id).toMatch(/^job-/);
    expect(handle.kind).toBe('library_passages');
    const job = await waitForJob(handle.job_id);
    expect(job.state).toBe('succeeded');
    await expect(page.getByTestId('recovery-summary')).toBeVisible();
    await expect(page.getByTestId('recovery-papers').getByRole('row')).toHaveCount(3);   // header + two papers
    await shot(page, 'find-supporting-text');
    noSecretLeak();
  });

  test('extraction on the user schema, then Repair relations on it', async ({ page }) => {
    const { schemaName } = libRun();
    await openExtract(page);
    await page.getByLabel('KG name', { exact: true }).fill(USER_KG);
    await page.getByLabel('Topic').fill('catfish measurements');
    await page.locator('#kg-schema').selectOption({ value: `user/${schemaName}@v1` });
    await pickAllPapers(page);
    const jobId = await estimateAndStart(page);
    const job = await waitForJob(jobId);
    expect(job.state).toBe('succeeded');
    await expect(page.getByTestId('run-state')).toHaveText(/succeeded/i);

    await page.getByRole('button', { name: 'New extraction' }).click();
    await page.getByRole('radiogroup', { name: 'Continue or new' }).getByRole('radio', { name: 'Continue existing KG' }).click();
    await page.getByRole('combobox', { name: 'Existing KG' }).selectOption({ label: USER_KG });
    const posted = page.waitForResponse(r => r.url().endsWith(`/v1/library/kgs/${USER_KG}/relations`) && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Repair relations' }).click();
    const handle = await (await posted).json();
    expect(handle.kind).toBe('library_relations');
    const repair = await waitForJob(handle.job_id);
    expect(repair.state).toBe('succeeded');
    await expect(page.getByTestId('relations-result')).toBeVisible();
    await shot(page, 'repair-relations');

    // A shipped-schema KG refuses the repair with the typed reason the UI words.
    const refused = await api(`/v1/library/kgs/${KG}/relations`, { method: 'POST', body: '{}' });
    expect(refused.status).toBe(422);
    expect(refused.body.reason).toBe('not_user_schema');
    noSecretLeak();
  });

  test('harvest: a run files the open-access paper, queues the rest, and a dropped PDF settles one of them', async ({ page }) => {
    const { category } = libRun();
    await openApp(page);
    await page.getByRole('link', { name: 'Library' }).click();
    await page.getByRole('tab', { name: 'Harvester' }).click();
    await page.getByLabel('Topic').fill('domain purity');
    await page.getByLabel('Category').selectOption(category);
    await page.getByLabel('Schema to record with the run').selectOption({ index: 1 });
    const posted = page.waitForResponse(r => r.url().endsWith('/v1/jobs') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start harvest' }).click();
    const handle = await (await posted).json();
    const runId = handle.job_id as string;
    expect(runId).toMatch(/^job-[0-9a-f]{24}$/);
    await expect(page.getByTestId('harvest-run')).toBeVisible();
    const job = await waitForJob(runId);
    expect(job.state).toBe('succeeded');

    const runs = await api('/v1/library/harvest/runs');
    expect(runs.body.runs.map((r: any) => r.id)).toContain(runId);
    const candidates = (await api(`/v1/library/harvest/runs/${runId}/candidates`)).body.candidates as any[];
    const byStatus = Object.fromEntries(candidates.map(c => [c.title.split(' ')[1], c.status]));
    expect(byStatus).toEqual({ open: 'downloaded', paywalled: 'paywalled', study: 'failed' });

    const table = page.getByRole('table', { name: 'Harvest results' });
    await expect(table.getByText('An open access study of domain purity')).toBeVisible();
    const queue = page.getByRole('list', { name: 'Papers needing a PDF' });
    await expect(queue.getByRole('listitem')).toHaveCount(2);
    await expect(page.getByRole('heading', { name: 'Needs a PDF (2 left)' })).toBeVisible();
    const paywalled = candidates.find(c => c.status === 'paywalled');
    expect(paywalled.open_url).toBe('https://doi.org/10.1000/paywalled-study');
    await expect(queue.getByRole('link', { name: `Open at publisher: ${paywalled.title}` })).toHaveAttribute('href', paywalled.open_url);
    await shot(page, 'harvest-queue');

    // The person saves the PDF in their own browser and drops it on the paper.
    await page.getByTestId(`harvest-file-${paywalled.id}`).setInputFiles({
      name: 'A_paywalled_study_of_domain_purity.pdf', mimeType: 'application/pdf',
      buffer: textPdf('A paywalled study of domain purity. doi:10.1000/paywalled-study'),
    });
    // The PDF names the paper's DOI, so the real match check files it without asking; a weaker match asks first.
    const filed = page.getByRole('status').filter({ hasText: /Filed A_paywalled_study_of_domain_purity\.pdf against/ });
    const confirm = page.getByTestId('harvest-confirm-dialog');
    await expect(filed.or(confirm)).toBeVisible();
    if (await confirm.isVisible()) {
      const tick = confirm.getByRole('checkbox', { name: /I have checked/ });
      if (await tick.count()) await tick.click();
      await confirm.getByRole('button', { name: 'File anyway' }).click();
    }
    await expect(filed).toBeVisible();
    await expect(filed).toContainText('strong match');
    await expect(page.getByRole('heading', { name: 'Needs a PDF (1 left)' })).toBeVisible();
    const after = (await api(`/v1/library/harvest/runs/${runId}/candidates`)).body.candidates as any[];
    expect(after.find(c => c.id === paywalled.id).status).toBe('filed');
    await shot(page, 'harvest-filed');

    await page.getByRole('tab', { name: 'Papers' }).click();
    const papers = page.getByRole('table', { name: 'Library papers' });
    await expect(papers.getByText('A_paywalled_study_of_domain_purity.pdf')).toBeVisible();
    await expect(papers.getByTestId('supplied-badge')).toHaveText('Supplied by hand');
    noSecretLeak();
  });

  test('chat: a decline ends an offer; a question asked while an offer is pending is answered, not taken as a decision', async ({ page }) => {
    await openApp(page);
    const offer = async () => {
      const offered = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
      await askQuestion(page, 'Explain the insufficient synthetic topic');
      const events = parseSse(await (await offered).text());
      expect(events[events.length - 1].data.status).toBe('awaiting_download_decision');
      await expect(page.getByText('Candidate Papers:').last()).toBeVisible();
    };

    // A question asked while an offer is pending is a question: answered, and the offer is dropped, not taken as a decision
    // (tests/test_chat_pending_offer.py pins the same on the service).
    await offer();
    const answered = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'What is Term A resonant scattering?');
    const events = parseSse(await (await answered).text());
    const last = events[events.length - 1].data;
    expect(last.status).toBe('answered');
    expect(last.answer).toContain('Synthetic grounded answer');
    expect(last.pending ?? null).toBeNull();
    await expect(page.getByText('Synthetic grounded answer').last()).toBeVisible();
    await page.getByRole('button', { name: 'View Knowledge Graph' }).last().waitFor();

    // A plain decline ends a new offer, then the original question is asked of the KG again with the topic marked declined;
    // on these fakes that re-ask has no evidence and no new offer, so the turn ends `stop_insufficient` with nothing pending.
    // (The declined topic is remembered for the session, so this is the last turn.)
    await offer();
    const declined = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Do not download any paper');
    const ev = parseSse(await (await declined).text());
    expect(ev[ev.length - 1].data.status).toBe('stop_insufficient');
    expect(ev[ev.length - 1].data.pending ?? null).toBeNull();
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();   // the turn is over (Stop is gone)
    await shot(page, 'chat-pending-then-question');
    noSecretLeak();
  });
});

test.afterAll(() => {
  const { artifacts } = libRun();
  const shots = fs.existsSync(path.join(artifacts, 'screenshots')) ? fs.readdirSync(path.join(artifacts, 'screenshots')) : [];
  fs.writeFileSync(path.join(artifacts, 'screenshots.json'), JSON.stringify(shots.sort(), null, 2));
});
