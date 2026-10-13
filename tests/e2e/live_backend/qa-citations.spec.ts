/**
 * T18B: ordinary QA against the real API. KG citations render from the live
 * answer and graph payload; the stream keeps turn_started/graph_update/complete.
 */
import { expect, test } from '@playwright/test';
import {
  askQuestion, expectNothingPublished, expectTurnStream, openApp, parseSse, publicationSnapshot,
  recordApi, resetBackend, shot, waitForAnswer,
} from './live';

test.describe('live QA and citations', () => {
  test.beforeEach(async () => { await resetBackend(); });

  test('streamed answer renders KG citations, references and the merged graph', async ({ page }) => {
    const before = publicationSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'What is Term A resonant scattering?');
    const response = await streamed;
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('text/event-stream');
    await waitForAnswer(page);

    // Wire: the request the frozen UI sent and the stream the real backend returned.
    const request = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
    expect(request.message).toBe('What is Term A resonant scattering?');
    expect(typeof request.client_turn_id).toBe('string');
    expect(typeof request.session_id).toBe('string');
    expect(request.source_rag).toBe(false);
    const events = parseSse(await response.text());
    expectTurnStream(events, request.client_turn_id, 'complete');
    const phases = events.map(e => e.data.phase).filter(Boolean);
    expect(phases).toEqual(expect.arrayContaining(['turn_started', 'retrieval_started', 'retrieval_result', 'graph_update']));
    const graphUpdate = events.find(e => e.data.phase === 'graph_update')!;
    expect(graphUpdate.data.node_ids).toEqual(['matkg:TermA', 'matkg:Other']);
    const complete = events[events.length - 1].data;
    expect(complete.status).toBe('answered');
    expect(complete.turn_id).toBe(request.client_turn_id);
    expect(seen.filter(s => s.path === '/chat')).toHaveLength(0); // no fallback

    // UI: answer text, one KG chip with tooltip, bibliography and the merged graph.
    await expect(page.getByText(/Synthetic grounded answer \(μ-scale, UTF-8\) citing/)).toBeVisible();
    await expect(page.locator('[data-citation-chip]')).toHaveCount(1);
    const chip = page.getByRole('button', { name: 'Citation 1: Term A' });
    await chip.hover();
    await expect(page.getByRole('tooltip')).toContainText('Term A');
    await expect(page.getByRole('tooltip')).toContainText('Technique');
    await expect(page.locator('[data-citation-ref="1"]')).toContainText(/KG/);
    await expect(page.locator('[data-citation-ref="1"]')).toContainText('Term A');
    await expect(page.getByText('Synthetic contract paper on resonant scattering')).toBeVisible();
    await expect(page.getByText('2 nodes')).toBeVisible();
    await chip.click();
    await expect(page.locator('[data-cite-focus-id="matkg:TermA"]')).toBeVisible();
    await expect(page.getByText('Pinned')).toBeVisible();
    await expect(page.getByText('Unknown')).toHaveCount(0);
    await shot(page, 'qa-citations');

    // Local profile: no login prompt, no credentials sent.
    await expect(page.getByText(/sign in|log in/i)).toHaveCount(0);
    await expectNothingPublished(before, seen);
  });

  test('a second turn in the same session keeps both bubbles and the session id', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    await askQuestion(page, 'What is Term A resonant scattering?');
    await waitForAnswer(page);
    await askQuestion(page, 'Tell me more about Term A');
    await expect(page.getByRole('button', { name: 'View Knowledge Graph' })).toHaveCount(2, { timeout: 30_000 });
    const turns = seen.filter(s => s.path === '/chat/stream').map(s => s.body as Record<string, any>);
    expect(turns).toHaveLength(2);
    expect(turns[1].session_id).toBe(turns[0].session_id);
    expect(turns[1].client_turn_id).not.toBe(turns[0].client_turn_id);
    expect(turns[1].messages.map((m: any) => m.role)).toEqual(['user', 'assistant']);
  });

  test('a mundane turn is answered directly without retrieval', async ({ page }) => {
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'hello there');
    const events = parseSse(await (await streamed).text());
    expect(events[events.length - 1].data.status).toBe('direct_response');
    expect(events.map(e => e.data.phase)).not.toContain('retrieval_started');
  });
});
