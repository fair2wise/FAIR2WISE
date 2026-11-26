/**
 * Helpers of the library live-backend specs. Observation only: nothing here intercepts a request; the page talks to
 * the real server and the specs check effects through the server's own routes and files.
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, type Page } from '@playwright/test';
import { runFile, type LibRun } from './global-setup';

export function libRun(): LibRun {
  const artifacts = path.resolve(process.env.F2W_LIVE_ARTIFACTS || '');
  return JSON.parse(fs.readFileSync(runFile(artifacts), 'utf8')) as LibRun;
}

export interface Observations {
  fake_calls: Record<string, number>;
  requests: Array<{ seq: number; method: string; path: string; status: number | null }>;
  secret_hits: Array<{ path: string; part: string }>;
}

export function observations(): Observations {
  return JSON.parse(fs.readFileSync(path.join(libRun().stateDir, 'observations.json'), 'utf8'));
}

export function noSecretLeak() {
  expect(observations().secret_hits).toEqual([]);
}

export async function api(pathname: string, init?: RequestInit) {
  const response = await fetch(`${libRun().apiBase}${pathname}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  });
  const text = await response.text();
  let body: any = text;
  try { body = JSON.parse(text); } catch { /* keep text */ }
  return { status: response.status, body };
}

/** Poll one job until it leaves queued/running; returns the final job record. */
export async function waitForJob(jobId: string, timeoutMs = 90_000) {
  const start = Date.now();
  for (;;) {
    const { status, body } = await api(`/v1/jobs/${encodeURIComponent(jobId)}`);
    expect(status).toBe(200);
    if (!['queued', 'running'].includes(body.state)) return body;
    if (Date.now() - start > timeoutMs) throw new Error(`job ${jobId} still ${body.state} after ${timeoutMs} ms`);
    await new Promise(resolve => setTimeout(resolve, 500));
  }
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

export function shot(page: Page, name: string) {
  return page.screenshot({ path: path.join(libRun().artifacts, 'screenshots', `${name}.png`), fullPage: true });
}

export interface SseEvent { event: string; data: Record<string, any> }

export function parseSse(raw: string): SseEvent[] {
  const frames = raw.split('\n\n');
  expect(frames.pop()).toBe('');
  return frames.map(frame => {
    const match = /^event: ([a-z_]+)\ndata: ([^\n]*)$/.exec(frame);
    expect(match, `bad SSE frame ${frame.slice(0, 80)}`).not.toBeNull();
    return { event: match![1], data: JSON.parse(match![2]) };
  });
}

/** The exported bundle file in the release folder (the workspace's storage/kg). */
export function exportedGraph(name: string): { things: any[]; associations: any[] } {
  return JSON.parse(fs.readFileSync(path.join(libRun().workspace, 'storage', 'kg', `matkg_${name}.json`), 'utf8'));
}

/** A small real PDF (one page of text) built in the browser process, for drops onto the Needs-a-PDF list. */
export function textPdf(text: string): Buffer {
  const escaped = text.replace(/[\\()]/g, m => `\\${m}`);
  const stream = `BT /F1 12 Tf 50 740 Td (${escaped}) Tj ET`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let out = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((body, index) => {
    offsets.push(out.length);
    out += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) out += `${String(offset).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}
