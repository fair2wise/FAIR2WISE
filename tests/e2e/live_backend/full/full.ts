/**
 * T18C helpers on top of the T18B ones (../live.ts). Observation only: nothing
 * here intercepts a request. The backend's own records (observations.json,
 * capture.jsonl) and direct API calls are used to check effects.
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { liveRun, observations as baseObservations } from '../live';
import type { FullRun } from './global-setup';

export * from '../live';

export function fullRun(): FullRun {
  return liveRun() as FullRun;
}

export interface FullObservations extends ReturnType<typeof baseObservations> {
  tiled: { entities: number; links: number; fingerprint: string } | { error: string };
  tiled_lookups: Array<{ status: string; hits: string[] }>;
  splash_mode: 'absent' | 'fake';
  secret_hits: Array<{ path: string; part: string }>;
}

export function fullObservations(): FullObservations {
  return JSON.parse(fs.readFileSync(path.join(fullRun().stateDir, 'observations.json'), 'utf8'));
}

/** Choose the editable-mode condition: no Splash service (target) or the T18A fake. */
export function setSplashMode(mode: 'absent' | 'fake') {
  fs.writeFileSync(path.join(fullRun().stateDir, 'splash_mode'), mode);
}

export async function api(pathname: string, init?: RequestInit) {
  const response = await fetch(`${fullRun().apiBase}${pathname}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  const text = await response.text();
  let body: any = text;
  try { body = JSON.parse(text); } catch { /* keep text */ }
  return { status: response.status, body };
}

/** Startup settings: editable (splash) graph, Source RAG off, live Tiled off. */
export async function resetFull() {
  setSplashMode('absent');
  const res = await api('/settings', {
    method: 'PUT',
    body: JSON.stringify({ graph_source: 'splash', source_rag: false, use_live_tiled: false }),
  });
  expect(res.status).toBe(200);
}

/** Every write-capable counter, Tiled fingerprint and graph-file hash. */
export function effectSnapshot() {
  const obs = fullObservations();
  const counters = Object.fromEntries(Object.entries(obs.fake_calls)
    // Writes and paid/ingest effects only; Splash reads (load_graph, find_*) are not effects.
    .filter(([k]) => /update_entity|create_entity|create_link|delete_link|rebuild_kg|splash_reimport|download_selected|extractor\./.test(k)));
  const graphs = Object.fromEntries(Object.entries(obs.graph_sha256).filter(([k]) => k.startsWith('storage/kg/')));
  return { counters, graphs, tiled: obs.tiled, splashEntities: obs.splash_entities, splashLinks: obs.splash_links };
}

export function capture(): Array<Record<string, any>> {
  const file = path.join(fullRun().stateDir, 'capture.jsonl');
  return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
}

export function fullShot(page: Page, name: string) {
  return page.screenshot({ path: path.join(fullRun().artifacts, 'screenshots', `${fullRun().runtime}-${name}.png`), fullPage: true });
}

export async function openKgViewer(page: Page) {
  await page.getByRole('button', { name: 'KG Viewer' }).click();
  await expect(page.locator('[data-kg-empty="false"]')).toBeVisible();
}

export async function openNode(page: Page, nodeId: string) {
  await page.locator(`[data-kg-node-id="${nodeId}"]`).first().click({ force: true });
  await expect(page.getByRole('button', { name: 'Close node details' })).toBeVisible();
}

export function noSecretLeak() {
  expect(fullObservations().secret_hits).toEqual([]);
}
