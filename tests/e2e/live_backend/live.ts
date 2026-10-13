/**
 * Shared helpers for the T18B live-backend specs. Nothing here intercepts or
 * fulfils requests: the page talks to the real backend; we only observe
 * traffic (page 'request'/'response' events) and the backend's own records.
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, type Page, type Request } from '@playwright/test';
import { runFile, type LiveRun } from './global-setup';

export function liveRun(): LiveRun {
  const artifacts = path.resolve(process.env.F2W_LIVE_ARTIFACTS || '');
  return JSON.parse(fs.readFileSync(runFile(artifacts), 'utf8')) as LiveRun;
}

export interface Observations {
  fake_calls: Record<string, number>;
  retrieval_calls: Array<{ question: string; source_rag: boolean | null; use_live_tiled: boolean | null }>;
  faults: Array<{ fault: string; path: string }>;
  requests: Array<{ seq: number; method: string; path: string; status: number | null; disconnected: boolean }>;
  graph_sha256: Record<string, string>;
  splash_entities: number;
  splash_links: number;
}

export function observations(): Observations {
  return JSON.parse(fs.readFileSync(path.join(liveRun().stateDir, 'observations.json'), 'utf8'));
}

/** Graph files and Splash write counters that must never change on a read path. */
export const WRITE_CALLS = [
  'splash.update_entity', 'splash.create_entity', 'splash.create_link', 'splash.delete_link',
  'kg_update.rebuild_kg', 'kg_update.splash_reimport', 'download.download_selected',
  'extractor.extract', 'extractor.extract_targeted',
];

export function publicationSnapshot() {
  const obs = observations();
  const writes: Record<string, number> = {};
  for (const name of WRITE_CALLS) writes[name] = obs.fake_calls[name] ?? 0;
  const graphs = Object.fromEntries(Object.entries(obs.graph_sha256).filter(([k]) => k.startsWith('storage/kg/')));
  return { writes, graphs, splashEntities: obs.splash_entities, splashLinks: obs.splash_links };
}

export interface Seen { method: string; url: string; path: string; body: unknown }

/** Record every request the page sends to the backend (observation only). */
export function recordApi(page: Page): Seen[] {
  const { apiBase } = liveRun();
  const seen: Seen[] = [];
  page.on('request', (req: Request) => {
    if (!req.url().startsWith(apiBase)) return;
    let body: unknown = null;
    try { body = req.postDataJSON(); } catch { body = req.postData(); }
    seen.push({ method: req.method(), url: req.url(), path: new URL(req.url()).pathname, body });
  });
  return seen;
}

/** Restore the backend to its startup settings (splash graph, Source RAG off). */
export async function resetBackend() {
  const { apiBase } = liveRun();
  const response = await fetch(`${apiBase}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ graph_source: 'splash', source_rag: false, use_live_tiled: false }),
  });
  expect(response.ok).toBeTruthy();
}

export async function openApp(page: Page) {
  await page.goto('/');
  await expect(page.getByPlaceholder('Ask a domain-specific question...')).toBeVisible();
}

export async function askQuestion(page: Page, text: string) {
  const input = page.getByPlaceholder('Ask a domain-specific question...');
  await input.fill(text);
  await input.press('Enter');
}

export async function waitForAnswer(page: Page) {
  await page.getByRole('button', { name: 'View Knowledge Graph' }).last().waitFor({ timeout: 30_000 });
}

export async function openSettings(page: Page) {
  await page.getByRole('button', { name: 'Settings' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('heading', { name: 'Settings' }).waitFor();
  return dialog;
}

export function shot(page: Page, name: string) {
  return page.screenshot({ path: path.join(liveRun().artifacts, 'screenshots', `${liveRun().runtime}-${name}.png`), fullPage: true });
}

export interface SseEvent { event: string; data: Record<string, any> }

/** Strict parse of the baseline framing (event: x\ndata: <json>\n\n, LF only). */
export function parseSse(raw: string): SseEvent[] {
  expect(raw.includes('\r')).toBeFalsy();
  const frames = raw.split('\n\n');
  expect(frames.pop()).toBe('');
  return frames.map(frame => {
    const match = /^event: ([a-z_]+)\ndata: ([^\n]*)$/.exec(frame);
    expect(match, `bad SSE frame ${frame.slice(0, 80)}`).not.toBeNull();
    return { event: match![1], data: JSON.parse(match![2]) };
  });
}

/** Turn-level stream invariants kept from the T18A baseline. */
export function expectTurnStream(events: SseEvent[], clientTurnId: string, terminal: 'complete' | 'error') {
  expect(events[0].event).toBe('progress');
  expect(events[0].data.phase).toBe('turn_started');
  expect(events[0].data.turn_id).toBe(clientTurnId);
  const terminals = events.filter(e => e.event === 'complete' || e.event === 'error');
  expect(terminals).toHaveLength(1);
  expect(events[events.length - 1].event).toBe(terminal);
  for (const e of events.slice(0, -1)) {
    expect(e.event).toBe('progress');
    expect(e.data.turn_id).toBe(clientTurnId);
  }
}

/** Snapshot before a test; assert after it that nothing was written or switched. */
export async function expectNothingPublished(before: ReturnType<typeof publicationSnapshot>, seen: Seen[]) {
  expect(publicationSnapshot()).toEqual(before);
  const writes = seen.filter(s => s.method === 'PATCH' || s.path === '/graph/upload' || s.path.startsWith('/chat/action'));
  expect(writes).toEqual([]);
  const { apiBase } = liveRun();
  const settings = await (await fetch(`${apiBase}/settings`)).json();
  expect({ backend: settings.backend, model: settings.model, use_live_tiled: settings.use_live_tiled })
    .toEqual({ backend: 'cborg', model: 'lbl/cborg-chat', use_live_tiled: false });
}
