/**
 * T18C UI11/UI12/UI09/UI26: the pending download/extraction flow in the frozen UI
 * against the migrated API, accepted and declined, plus replay of an already
 * applied decision (no duplicate effect). Download/extraction/rebuild are the
 * T18A fakes; every byte the browser parses comes from the real routes.
 */
import { expect, test, type Page } from '@playwright/test';
import {
  api, askQuestion, effectSnapshot, fullObservations, fullShot, noSecretLeak, openApp, parseSse, recordApi,
  resetFull,
} from './full';

const QUESTION = 'Explain the insufficient synthetic topic';

function calls() {
  const c = fullObservations().fake_calls;
  return {
    search: c['download.search_candidates'] ?? 0,
    download: c['download.download_selected'] ?? 0,
    extract: (c['extractor.extract_targeted'] ?? 0) + (c['extractor.extract'] ?? 0),
    rebuild: c['kg_update.rebuild_kg'] ?? 0,
    reimport: c['kg_update.splash_reimport'] ?? 0,
  };
}

async function toCandidates(page: Page) {
  const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
  await askQuestion(page, QUESTION);
  const events = parseSse(await (await streamed).text());
  expect(events[events.length - 1].data.status).toBe('awaiting_download_decision');
  await expect(page.getByText('Candidate Papers:')).toBeVisible();
  await expect(page.getByText('Candidate paper one')).toBeVisible();
  await expect(page.getByText('Candidate paper two')).toBeVisible();
  await expect(page.getByText(/Ask in chat which paper to download/)).toBeVisible();
  return events[events.length - 1].data;
}

test.describe('pending download and extraction', () => {
  test.beforeEach(async () => { await resetFull(); });

  test('UI11/UI12 accept: chosen candidate downloads once, Run extraction rebuilds once, replay is a no-op', async ({ page }) => {
    const seen = recordApi(page);
    const start = calls();
    await openApp(page);
    const pending = await toCandidates(page);
    expect(pending.pending.kind).toBe('download');
    expect(pending.pending.papers.map((p: any) => p.title)).toEqual(['Candidate paper one', 'Candidate paper two']);
    expect(JSON.stringify(pending)).not.toContain('approval_token');

    // Choose by number in chat (the frozen UI has no download buttons).
    const chosen = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Download paper 1 please');
    const ev = parseSse(await (await chosen).text());
    const afterDownload = ev[ev.length - 1].data;
    expect(afterDownload.status).toBe('awaiting_extraction_decision');
    expect(afterDownload.pending.kind).toBe('extraction');
    expect(afterDownload.pending.candidate.title).toBe('Candidate paper one');
    await expect.poll(() => calls().download).toBe(start.download + 1);
    await expect(page.getByRole('button', { name: 'Run extraction' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Skip' })).toBeVisible();
    await fullShot(page, 'pending-extraction');

    const action = page.waitForResponse(r => r.url().endsWith('/chat/action/stream'));
    await page.getByRole('button', { name: 'Run extraction' }).click();
    const actionEvents = parseSse(await (await action).text());
    const done = actionEvents[actionEvents.length - 1];
    expect(done.event).toBe('complete');
    const phases = actionEvents.map(e => e.data.phase).filter(Boolean);
    expect(phases[0]).toBe('turn_started');
    expect(phases).toEqual(expect.arrayContaining(['splash_reimport_started', 'splash_reimport_result']));
    const req = seen.find(s => s.path === '/chat/action/stream')!.body as Record<string, any>;
    expect(req).toMatchObject({ decision: 'yes', kind: 'extraction' });
    expect(req.parent_turn_id).toBeTruthy();
    await expect.poll(() => calls()).toEqual({ ...start, search: calls().search, download: start.download + 1,
      extract: start.extract + 1, rebuild: start.rebuild + 1, reimport: start.reimport + 1 });
    await expect(page.getByRole('button', { name: 'Run extraction' })).toHaveCount(0);
    await fullShot(page, 'pending-extracted');

    // Replay of both decisions on the same session: explicit no_pending_action, no second effect.
    const sessionId = req.session_id as string;
    const before = calls();
    for (const body of [
      { decision: 'yes', kind: 'extraction', session_id: sessionId, client_turn_id: 'replay-ex-1' },
      { decision: 'yes', kind: 'download', candidate_index: 0, session_id: sessionId, client_turn_id: 'replay-dl-1' },
    ]) {
      const replay = await api('/chat/action', { method: 'POST', body: JSON.stringify(body) });
      expect(replay.status).toBe(200);
      expect(replay.body.status).toBe('no_pending_action');
    }
    // Same client_turn_id as the applied action, over the stream route.
    const again = await api('/chat/action/stream', { method: 'POST', body: JSON.stringify({ ...req }) });
    expect(again.status).toBe(200);
    const againEvents = parseSse(again.body as string);
    expect(againEvents[againEvents.length - 1].data.status).toBe('no_pending_action');
    expect(calls()).toEqual(before);
    noSecretLeak();
  });

  test('UI11 decline in chat: stopped_by_user, nothing downloaded or rebuilt', async ({ page }) => {
    const before = effectSnapshot();
    const start = calls();
    await openApp(page);
    await toCandidates(page);
    const declined = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Do not download any paper');
    const ev = parseSse(await (await declined).text());
    expect(ev[ev.length - 1].data.status).toBe('stopped_by_user');
    await expect(page.getByText('Candidate Papers:')).toHaveCount(1);
    expect({ ...calls(), search: 0 }).toEqual({ ...start, search: 0 });
    expect(effectSnapshot()).toEqual(before);
  });

  test('UI12 Skip extraction: explicit stop, extraction and rebuild never run', async ({ page }) => {
    const seen = recordApi(page);
    const start = calls();
    await openApp(page);
    await toCandidates(page);
    const chosen = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Download the first paper');
    await chosen;
    await expect(page.getByRole('button', { name: 'Skip' })).toBeVisible();
    const action = page.waitForResponse(r => r.url().endsWith('/chat/action/stream'));
    await page.getByRole('button', { name: 'Skip' }).click();
    const events = parseSse(await (await action).text());
    expect(events[events.length - 1].data.status).toBe('stopped_by_user');
    expect((seen.find(s => s.path === '/chat/action/stream')!.body as any).decision).toBe('no');
    await expect.poll(() => calls().download).toBe(start.download + 1);
    const now = calls();
    expect(now.download).toBe(start.download + 1);
    expect([now.extract, now.rebuild, now.reimport]).toEqual([start.extract, start.rebuild, start.reimport]);
  });
});
