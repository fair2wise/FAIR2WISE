/**
 * T18B: insufficient KG evidence reaches the download-candidate card through
 * the real orchestrator (candidate search + debate), and nothing is downloaded,
 * extracted or published without an explicit approval.
 */
import { expect, test } from '@playwright/test';
import {
  askQuestion, expectNothingPublished, expectTurnStream, observations, openApp, parseSse,
  publicationSnapshot, recordApi, resetBackend, shot,
} from './live';

test.beforeEach(async () => { await resetBackend(); });

test('insufficient evidence shows the candidate card and downloads nothing', async ({ page }) => {
  const before = publicationSnapshot();
  const searchesBefore = observations().fake_calls['download.search_candidates'] ?? 0;
  const seen = recordApi(page);
  await openApp(page);
  const streamed = page.waitForResponse(r => r.url().endsWith('/chat/stream'));
  await askQuestion(page, 'Explain the insufficient synthetic topic');
  const events = parseSse(await (await streamed).text());
  const request = seen.find(s => s.path === '/chat/stream')!.body as Record<string, any>;
  expectTurnStream(events, request.client_turn_id, 'complete');
  const complete = events[events.length - 1].data;
  expect(complete.status).toBe('awaiting_download_decision');
  expect(complete.pending.kind).toBe('download');
  expect(complete.pending.papers.map((p: any) => p.title)).toEqual(['Candidate paper one', 'Candidate paper two']);
  expect(JSON.stringify(complete.pending)).not.toContain('approval_token');
  expect(events.map(e => e.data.phase)).toEqual(expect.arrayContaining(['candidate_search_started', 'candidate_search_result', 'debate_started', 'debate_result', 'awaiting_download_decision']));

  await expect(page.getByText('Candidate Papers:')).toBeVisible();
  await expect(page.getByText('Candidate paper one')).toBeVisible();
  await expect(page.getByText('Candidate paper two')).toBeVisible();
  await expect(page.getByText('RECOMMENDED', { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/Ask in chat which paper to download/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run extraction' })).toHaveCount(0);
  await shot(page, 'insufficient-candidates');

  expect(observations().fake_calls['download.search_candidates']).toBe(searchesBefore + 1);
  await expectNothingPublished(before, seen);
});
