/**
 * T18B: streaming error semantics of the frozen client against the real API.
 *  - a terminal `error` event (retrieval failure, or stream_error after progress)
 *    is shown and does NOT fall back to /chat;
 *  - a fragmented stream (1-7 byte pieces) parses to the same answer;
 *  - a failure before the first event falls back to the real /chat route;
 *  - Stop (AbortSignal) cancels the request and does not fall back.
 * Faults are injected server-side by the launcher (marker in the message);
 * the browser's requests are never intercepted.
 */
import { expect, test } from '@playwright/test';
import {
  askQuestion, expectNothingPublished, expectTurnStream, observations, openApp, parseSse,
  publicationSnapshot, recordApi, resetBackend, shot, waitForAnswer,
} from './live';

test.describe('live stream faults', () => {
  test.beforeEach(async () => { await resetBackend(); });

  test('retrieval failure arrives as an error event, is shown, and does not fall back', async ({ page }) => {
    const before = publicationSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Please explode the retrieval');
    const events = parseSse(await (await streamed).text());
    const request = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
    expectTurnStream(events, request.client_turn_id, 'error');
    expect(events[events.length - 1].data.status).toBe('retrieval_error');
    await expect(page.getByText('Agent run failed')).toBeVisible();
    await expect(page.getByText(/Retrieval failed safely/)).toBeVisible();
    // Disclosure projection: no raw exception text in the browser.
    await expect(page.getByText(/synthetic retrieval failure/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();
    expect(seen.filter(s => s.path === '/chat')).toHaveLength(0);
    await shot(page, 'error-retrieval');
    await expectNothingPublished(before, seen);
  });

  test('stream_error after progress events does not fall back to /chat', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'Please crash the verdict');
    const events = parseSse(await (await streamed).text());
    const request = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
    expectTurnStream(events, request.client_turn_id, 'error');
    expect(events.map(e => e.data.phase)).toContain('retrieval_started');
    const terminal = events[events.length - 1].data;
    expect(terminal.status).toBe('stream_error');
    expect(terminal.message).toBe(`Internal error (error: internal; ref: ${request.client_turn_id})`);
    await expect(page.getByText('Agent run failed')).toBeVisible();
    await expect(page.getByText(`ref: ${request.client_turn_id}`)).toBeVisible();
    await expect(page.getByText(/has no attribute/)).toHaveCount(0);
    expect(seen.filter(s => s.path === '/chat')).toHaveLength(0);
  });

  test('a fragmented stream parses to the same answer and citations', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    const faultsBefore = observations().faults.length;
    const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
    await askQuestion(page, 'What is Term A resonant scattering [fragment]?');
    const events = parseSse(await (await streamed).text());
    const request = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
    expectTurnStream(events, request.client_turn_id, 'complete');
    expect(observations().faults.slice(faultsBefore)).toEqual([{ fault: 'fragment', path: '/chat/stream' }]);
    const sends = observations().requests.filter(r => r.method === 'POST' && r.path === '/chat/stream').pop();
    expect(sends?.status).toBe(200);
    await waitForAnswer(page);
    await expect(page.getByText(/Synthetic grounded answer \(μ-scale, UTF-8\) citing/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Citation 1: Term A' })).toBeVisible();
    await expect(page.getByText('2 nodes')).toBeVisible();
    expect(seen.filter(s => s.path === '/chat')).toHaveLength(0);
  });

  test('a failure before the first event falls back to the real non-stream route', async ({ page }) => {
    const before = publicationSnapshot();
    const seen = recordApi(page);
    await openApp(page);
    const fallback = page.waitForResponse(r => r.url().endsWith('/chat') && r.request().method() === 'POST');
    await askQuestion(page, 'What is Term A resonant scattering [fallback-503]?');
    const response = await fallback;
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.status).toBe('answered');
    const stream = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
    const chat = seen.find(s => s.path === '/chat')!.body as Record<string, any>;
    // Same turn id and payload on the fallback (liveAgent.ts:525).
    expect(chat.client_turn_id).toBe(stream.client_turn_id);
    expect(chat.message).toBe(stream.message);
    expect(body.turn_id).toBe(stream.client_turn_id);
    await waitForAnswer(page);
    await expect(page.getByRole('button', { name: 'Citation 1: Term A' })).toBeVisible();
    await shot(page, 'fallback-nonstream');
    await expectNothingPublished(before, seen);
  });

  test('Stop aborts the stream, does not fall back, and the backend stays usable', async ({ page }) => {
    const seen = recordApi(page);
    await openApp(page);
    const callsBefore = observations().retrieval_calls.length;
    await askQuestion(page, '[slow] What is Term A resonant scattering?');
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible();
    await expect.poll(() => observations().retrieval_calls.slice(callsBefore)
      .some(c => c.question.includes('[slow]')), { timeout: 15_000 }).toBeTruthy();
    await page.getByRole('button', { name: 'Stop' }).click();
    await expect(page.getByRole('button', { name: 'Send question' })).toBeVisible();
    await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeEnabled();
    // The server saw the client go away well before the 30 s fake delay.
    await expect.poll(() => observations().requests.filter(r => r.method === 'POST' && r.path === '/chat/stream').pop()?.disconnected,
      { timeout: 15_000 }).toBe(true);
    await page.waitForTimeout(1_000);
    expect(seen.filter(s => s.path === '/chat')).toHaveLength(0);
    await expect(page.getByText('Agent run failed')).toHaveCount(0);
    await shot(page, 'cancelled');

    await askQuestion(page, 'What is Term A resonant scattering?');
    await waitForAnswer(page);
    await expect(page.getByRole('button', { name: 'Citation 1: Term A' })).toBeVisible();
  });
});
