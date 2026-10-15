/**
 * T18C UI13/UI22/UI25: sessions (switch, reload, delete) with server-side pending
 * state isolated per session; turn annotations, traces and UI telemetry.
 */
import { expect, test } from '@playwright/test';
import {
  api, askQuestion, fullShot, openApp, parseSse, recordApi, resetFull, waitForAnswer,
} from './full';

test.describe('sessions, traces and annotations', () => {
  test.beforeEach(async () => { await resetFull(); });

  test('UI13/UI25 two chats keep separate server sessions; delete clears only its own', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    const first = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Explain the insufficient synthetic topic');
    await first;
    await expect(page.getByText('Candidate Papers:')).toBeVisible();

    await page.getByRole('button', { name: 'New chat' }).click();
    await expect(page.getByText('Candidate Papers:')).toHaveCount(0);
    await askQuestion(page, 'What is Term A resonant scattering?');
    await waitForAnswer(page);
    const turns = seen.filter(s => s.path === '/chat/stream').map(s => s.body as Record<string, any>);
    const [sessionA, sessionB] = [turns[0].session_id, turns[1].session_id];
    expect(sessionA).not.toBe(sessionB);

    // Session B has no pending decision; A still has its download pending.
    const bAction = await api('/chat/action', { method: 'POST', body: JSON.stringify(
      { decision: 'no', kind: 'download', session_id: sessionB, client_turn_id: 'iso-b-1' }) });
    expect(bAction.body.status).toBe('no_pending_action');

    // Switch back through Search chats, then reload: bubbles come from browser storage.
    await page.getByRole('button', { name: 'Search chats' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText('Explain the insufficient synthetic topic').click();
    await expect(page.getByText('Candidate Papers:')).toBeVisible();
    await page.reload();
    await expect(page.getByText('Candidate Papers:')).toBeVisible();
    const stored = await page.evaluate(() => Object.keys(window.localStorage));
    expect(stored).toEqual(expect.arrayContaining(['fair2wise.chat.sessions.v2', 'fair2wise-agent-settings-v1']));
    await fullShot(page, 'sessions-switched');

    // Delete chat A from the UI: DELETE /session/<A>; B keeps answering in the same session.
    await page.getByRole('button', { name: 'Search chats' }).click();
    const deleted = page.waitForResponse(r => r.request().method() === 'DELETE');
    await page.getByRole('button', { name: 'Delete Explain the insufficient synthetic topic' }).click();
    const del = await deleted;
    expect(del.status()).toBe(200);
    expect(new URL(del.url()).pathname).toBe(`/session/${sessionA}`);
    const aAction = await api('/chat/action', { method: 'POST', body: JSON.stringify(
      { decision: 'yes', kind: 'download', candidate_index: 0, session_id: sessionA, client_turn_id: 'iso-a-1' }) });
    expect(aAction.body.status).toBe('no_pending_action');
    const bAgain = await api('/chat', { method: 'POST', body: JSON.stringify(
      { message: 'Tell me more about Term A', session_id: sessionB, client_turn_id: 'iso-b-2' }) });
    expect(bAgain.body.status).toBe('answered');
  });

  test('UI22 Pass label posts an annotation; the trace and telemetry are linked to the turn', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'What is Term A resonant scattering?');
    const events = parseSse(await (await streamed).text());
    await waitForAnswer(page);
    const turnId = events[0].data.turn_id as string;
    const annotated = page.waitForResponse(r => r.url().endsWith('/annotations'));
    await page.getByRole('button', { name: 'Pass', exact: true }).click();
    const ann = await annotated;
    expect(ann.status()).toBe(200);
    const body = seen.find(s => s.path === '/annotations')!.body as Record<string, any>;
    expect(body).toMatchObject({ turn_id: turnId, verdict: 'pass' });
    await page.getByRole('button', { name: 'Citation 1: Term A' }).click();
    const trace = await api(`/traces/${turnId}`);
    expect(trace.status).toBe(200);
    expect(JSON.stringify(trace.body)).toContain(turnId);
    expect(JSON.stringify(trace.body)).not.toMatch(/CBORG_API_KEY|Apikey [A-Za-z0-9]/);
    await expect.poll(() => seen.some(s => s.path === '/telemetry/ui'), { timeout: 20_000 }).toBeTruthy();
    const telemetry = seen.filter(s => s.path === '/telemetry/ui').map(s => JSON.stringify(s.body)).join('\n');
    expect(telemetry).not.toContain('What is Term A resonant'); // no draft/question keystrokes
    await fullShot(page, 'annotation');
  });
});
