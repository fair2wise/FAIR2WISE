/**
 * T18C UI04/UI15/UI16/UI17/UI18: the frozen UI's graph editing.
 *
 * Target condition (T18C acceptance): editable ("splash") mode works on Tiled with no
 * Splash service. The migrated backend still routes PATCH /graph/node through the
 * Splash client (app/modules/f2w_agent/api.py update_graph_node -> kg_update.splash_*);
 * the Tiled edit projection is blocked on owner decisions STOP-1..STOP-6
 * (docs/migration/reviews/H1/decision.json). These specs record, in the browser:
 *   - with no Splash service, Save fails closed (502 shown, nothing written anywhere);
 *   - the Tiled-backed Save is an expected failure (test.fail) until STOP-1..6 are decided;
 *   - with the T18A in-memory Splash fake, the old UI's edit semantics are unchanged.
 */
import { expect, test } from '@playwright/test';
import {
  api, askQuestion, effectSnapshot, fullShot, noSecretLeak, openApp, openKgViewer, openNode, parseSse,
  recordApi, resetFull, setSplashMode, waitForAnswer,
} from './full';

const NODE = 'matkg:TermA';

async function startEdit(page: import('@playwright/test').Page) {
  await openApp(page);
  await openKgViewer(page);
  await openNode(page, NODE);
  await page.getByRole('button', { name: 'Edit properties' }).click();
  await expect(page.getByLabel('Node label')).toBeVisible();
}

test.describe('graph editing from the frozen UI', () => {
  test.beforeEach(async () => { await resetFull(); });
  test.afterEach(async () => { setSplashMode('absent'); });

  test('UI04/UI15 no Splash service: Save fails closed with the legacy 502 and writes nothing', async ({ page }) => {
    setSplashMode('absent');
    const before = effectSnapshot();
    const nodeBefore = (await api(`/graph/node/${encodeURIComponent(NODE)}`)).body;
    await startEdit(page);
    await page.getByLabel('Node label').fill('Term A edited without Splash');
    const patched = page.waitForResponse(r => r.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save' }).click();
    const response = await patched;
    expect(response.status()).toBe(502);
    const detail = (await response.json()).detail as string;
    expect(detail).toMatch(/^Splash update failed: /);
    expect(detail).not.toMatch(/Traceback|ConnectionRefused|127\.0\.0\.1/);
    await expect(page.locator('p.text-rose-600')).toContainText('Splash update failed');
    await expect(page.getByLabel('Node label')).toBeVisible(); // still editing, no fake success
    await fullShot(page, 'edit-no-splash');
    expect(effectSnapshot()).toEqual(before);
    expect((await api(`/graph/node/${encodeURIComponent(NODE)}`)).body).toEqual(nodeBefore);
    noSecretLeak();
  });

  test('UI04/UI15 Tiled-backed editable mode saves and reads back without a Splash service', async ({ page }) => {
    test.fail(true, 'BLOCKED: no Tiled edit projection behind PATCH /graph/node; owner STOP-1..6 '
      + '(docs/migration/ui_contracts/legacy_splash_to_tiled_adr.md section 3; H1 decision.json)');
    setSplashMode('absent');
    await startEdit(page);
    await page.getByLabel('Node label').fill('Term A edited on Tiled');
    const patched = page.waitForResponse(r => r.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save' }).click();
    expect((await patched).status()).toBe(200);
  });

  test('UI15/UI16/UI17 baseline semantics (T18A Splash fake): fields, relationship and snippet edits read back; old citation resolves', async ({ page }) => {
    setSplashMode('fake');
    const seen = recordApi(page);
    await startEdit(page);
    await page.getByLabel('Node label').fill('Term A edited');
    await page.locator('textarea').first().fill('Edited in the frozen UI against the migrated API.');

    // UI16: add an outgoing relationship through the real node search.
    await page.getByText('Relationships', { exact: true }).locator('..').getByRole('button', { name: 'Add' }).click();
    await page.getByLabel('Relationship predicate').fill('rel:affects');
    await page.getByPlaceholder('Search for a target node…').fill('polymer film');
    await page.getByPlaceholder('Search for a target node…').press('Enter');
    await page.getByRole('button', { name: /Other node/ }).click();
    await page.getByRole('button', { name: 'Stage relationship' }).click();

    // UI17: add a linked snippet.
    await page.getByText('Linked code snippets', { exact: true }).locator('..').getByRole('button', { name: 'Add' }).click();
    await page.getByPlaceholder('Label', { exact: true }).last().fill('delta snippet');
    await page.getByPlaceholder('Function name', { exact: true }).last().fill('delta');
    await page.getByPlaceholder('Language', { exact: true }).last().fill('python');
    await page.getByPlaceholder('Code', { exact: true }).last().fill('def delta():\n    return 4');

    const patched = page.waitForResponse(r => r.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save' }).click();
    const response = await patched;
    expect(response.status()).toBe(200);
    const body = seen.find(s => s.method === 'PATCH')!.body as Record<string, any>;
    expect(body.label).toBe('Term A edited');
    expect(body.relationship_updates).toEqual([
      { action: 'add', source: NODE, predicate: 'rel:affects', target: 'matkg:Other' }]);
    expect(body.linked_code_snippets.map((s: any) => [s.id ?? null, s._action])).toEqual([
      ['matkg:Code1', 'upsert'], [null, 'upsert']]);
    const saved = await response.json();
    expect(saved.label).toBe('Term A edited');
    expect(saved.linked_code_snippets.map((s: any) => s.function_name).sort()).toEqual(['delta', 'gamma']);
    await expect(page.getByLabel('Node label')).toHaveCount(0);   // Save completed (panel closes)
    await expect(page.getByText('4 nodes')).toBeVisible();        // the refreshed graph has the new snippet
    await expect(page.locator('[data-kg-node-id]')).toHaveCount(4);
    await fullShot(page, 'edit-fake-saved');

    // Same public id reads back through the old API; the graph shows the new edge.
    const node = (await api(`/graph/node/${encodeURIComponent(NODE)}`)).body;
    expect(node.label).toBe('Term A edited');
    const graph = (await api('/graph')).body;
    expect(graph.edges).toEqual(expect.arrayContaining([
      { source: NODE, target: 'matkg:Other', predicate: 'rel:affects' }]));

    // UI19/UI26: an old-style citation of the same id resolves to the edited record.
    await page.getByRole('button', { name: 'Agent KG Viewer' }).click();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'What is Term A resonant scattering?');
    const events = parseSse(await (await streamed).text());
    await waitForAnswer(page);
    expect(events[events.length - 1].data.node_ids).toContain(NODE);
    await expect(page.getByRole('button', { name: 'Citation 1: Term A edited' })).toBeVisible();

    // UI16/UI17 inverse in the browser: remove the relationship and unlink the new snippet.
    await openKgViewer(page);
    await openNode(page, NODE);
    await page.getByRole('button', { name: 'Edit properties' }).click();
    await page.getByRole('button', { name: 'Remove relationship rel:affects' }).click();
    await page.getByRole('button', { name: 'Remove linked snippet 2' }).click();
    await page.getByLabel('Node label').fill('Term A');
    await page.locator('textarea').first().fill('Synthetic contract node A (resonant scattering).');
    const second = page.waitForResponse(r => r.request().method() === 'PATCH');
    await page.getByRole('button', { name: 'Save' }).click();
    expect((await second).status()).toBe(200);
    const body2 = seen.filter(s => s.method === 'PATCH')[1].body as Record<string, any>;
    expect(body2.relationship_updates).toEqual([
      { action: 'remove', source: NODE, predicate: 'rel:affects', target: 'matkg:Other' }]);
    expect(body2.linked_code_snippets.map((s: any) => s.id)).toEqual(['matkg:Code1']);
    const after = (await api(`/graph/node/${encodeURIComponent(NODE)}`)).body;
    expect(after.label).toBe('Term A');
    expect(after.linked_code_snippets.map((s: any) => s.function_name)).toEqual(['gamma']);
    const graph2 = (await api('/graph')).body;
    expect(graph2.edges.filter((e: any) => e.predicate === 'rel:affects')).toEqual([]);
    noSecretLeak();
  });

  test('UI18 JSON mode stays read-only in the browser: no Edit control, PATCH refused with the legacy 400', async ({ page }) => {
    const set = await api('/settings', { method: 'PUT', body: JSON.stringify({
      graph_source: 'json', json_graph_path: 'storage/kg/matkg_contract_v1.json',
      json_graph_paths: ['storage/kg/matkg_contract_v1.json'] }) });
    expect(set.status).toBe(200);
    const before = effectSnapshot();
    await openApp(page);
    await openKgViewer(page);
    await openNode(page, NODE);
    await expect(page.getByRole('button', { name: 'Edit properties' })).toHaveCount(0);
    const refused = await api(`/graph/node/${encodeURIComponent(NODE)}`, { method: 'PATCH',
      body: JSON.stringify({ label: 'should fail' }) });
    expect(refused.status).toBe(400);
    expect(refused.body.detail).toBe('Editing node properties requires splash graph mode');
    expect(effectSnapshot()).toEqual(before);
  });
});
