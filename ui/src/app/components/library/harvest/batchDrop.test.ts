import { describe, expect, it, vi } from 'vitest';
import filedFixture from '../../__fixtures__/harvest_filed.json';
import needsFixture from '../../__fixtures__/harvest_needs_confirmation.json';
import unmatchedFixture from '../../__fixtures__/harvest_unmatched.json';
import { BATCH_CONCURRENCY, confirmOne, dropOne, matchedTitle, outcomeOf, planDrop, runPool } from './batchDrop';

const pdf = (name: string) => new File(['%PDF'], name, { type: 'application/pdf' });
const respond = (status: number, body: unknown) =>
  vi.fn(async (_input?: unknown, _init?: unknown) => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe('planDrop', () => {
  it('accepts up to 20 files and separates non-PDFs', () => {
    const files = [...Array.from({ length: 19 }, (_, i) => pdf(`${i}.pdf`)), new File(['x'], 'notes.txt', { type: 'text/plain' })];
    const plan = planDrop(files);
    expect(plan.ok).toBe(true);
    if (plan.ok) {
      expect(plan.send).toHaveLength(19);
      expect(plan.notPdf.map(f => f.name)).toEqual(['notes.txt']);
    }
  });

  it('refuses 21 files whole with a plain message', () => {
    const plan = planDrop(Array.from({ length: 21 }, (_, i) => pdf(`${i}.pdf`)));
    expect(plan.ok).toBe(false);
    if (!plan.ok) expect(plan.message).toBe('You dropped 21 files; at most 20 can be dropped at once. Nothing was sent. Drop fewer files.');
  });
});

describe('runPool', () => {
  it('never runs more than the limit at once and runs everything', async () => {
    let active = 0;
    let peak = 0;
    const done: number[] = [];
    await runPool([1, 2, 3, 4, 5, 6], BATCH_CONCURRENCY, async n => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise(resolve => setTimeout(resolve, 5));
      active -= 1;
      done.push(n);
    });
    expect(BATCH_CONCURRENCY).toBe(2);
    expect(peak).toBe(2);
    expect(done.sort()).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('a worker that throws does not stop the others', async () => {
    const done: number[] = [];
    await runPool([1, 2, 3, 4], 2, async n => {
      if (n === 2) throw new Error('boom');
      done.push(n);
    });
    expect(done.sort()).toEqual([1, 3, 4]);
  });

  it('stops starting new items when told to', async () => {
    const done: number[] = [];
    let stop = false;
    await runPool([1, 2, 3, 4, 5], 1, async n => { done.push(n); stop = n >= 2; }, () => stop);
    expect(done).toEqual([1, 2]);
  });
});

describe('batch outcomes (the real answers)', () => {
  it('a strong match is filed', async () => {
    const { outcome, result } = await dropOne('run1', pdf('a.pdf'), respond(200, filedFixture));
    expect(outcome).toMatchObject({ state: 'filed', kind: 'strong', confirmed: false, duplicate: false, title: filedFixture.candidate.title });
    expect(result?.filed).toBe(true);
  });

  it('a weak match asks, and names the paper', async () => {
    const { outcome, result } = await dropOne('run1', pdf('a.pdf'), respond(200, needsFixture));
    expect(outcome.state).toBe('confirm');
    expect(matchedTitle(outcome)).toBe(needsFixture.candidate.title);
    expect(result).toBeNull();
  });

  it('an unmatched file is none and nothing is filed', async () => {
    const { outcome, result } = await dropOne('run1', pdf('a.pdf'), respond(200, unmatchedFixture));
    expect(outcome).toEqual({ state: 'none' });
    expect(result).toBeNull();
    expect(matchedTitle(outcome)).toBeNull();
  });

  it('a failed request becomes an error row with the server message (413 and 415 read plainly)', async () => {
    expect((await dropOne('run1', pdf('a.pdf'), respond(413, { detail: 'upload exceeds the limit' }))).outcome)
      .toEqual({ state: 'error', message: 'File too large: upload exceeds the limit' });
    expect((await dropOne('run1', pdf('a.pdf'), respond(415, { detail: 'not a PDF' }))).outcome)
      .toEqual({ state: 'error', message: 'Not a PDF: not a PDF' });
  });

  it('outcomeOf reads a duplicate', () => {
    expect(outcomeOf({ ...filedFixture, duplicate: true } as never)).toMatchObject({ state: 'filed', duplicate: true });
  });

  it('confirm repeats on the candidate route with confirm=true', async () => {
    const fetcher = respond(200, { ...filedFixture, verdict: { ...filedFixture.verdict, kind: 'weak' } });
    const { outcome, result } = await confirmOne('cand 1', pdf('a.pdf'), fetcher);
    expect((fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatch(/candidates\/cand%201\/pdf\?confirm=true$/);
    expect(outcome).toMatchObject({ state: 'filed', kind: 'weak', confirmed: true });
    expect(result?.filed).toBe(true);
  });
});
