import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobLogEntry, JobLogsResponse } from '../extractionApi';
import { ERROR_MAX_MS, IDLE_MAX_MS, nextDelay, POLL_MS, useJobLogPolling } from './useJobLogPolling';

const entry = (seq: number): JobLogEntry => ({ seq, at: '2026-11-02T10:00:00Z', level: 'info', stage: 's', message: `m${seq}` });

type Call = { jobId: string; after: number; options: { limit?: number; tail?: boolean } };
let calls: Call[];
let answers: (JobLogsResponse | Error)[];

const fetchLogs = vi.fn(async (jobId: string, after: number, _f: typeof fetch, options: { limit?: number; tail?: boolean }) => {
  calls.push({ jobId, after, options });
  const next = answers.shift() ?? { logs: [] };
  if (next instanceof Error) throw next;
  return next;
});

beforeEach(() => {
  vi.useFakeTimers();
  calls = [];
  answers = [];
  fetchLogs.mockClear();
});
afterEach(() => vi.useRealTimers());

const flush = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

function mount(live = true, terminal = false, jobId: string | null = 'j1') {
  return renderHook(({ live, terminal, jobId }) => useJobLogPolling(jobId, { live, terminal, fetchLogs }),
    { initialProps: { live, terminal, jobId } });
}

describe('nextDelay', () => {
  it('stays at the base period while lines arrive, then backs off to 10 s, and to 30 s after errors', () => {
    expect(nextDelay(2000, 0, 0)).toBe(2000);
    expect(nextDelay(2000, 4, 0)).toBe(2000);
    expect(nextDelay(2000, 5, 0)).toBe(4000);
    expect(nextDelay(2000, 6, 0)).toBe(8000);
    expect(nextDelay(2000, 20, 0)).toBe(IDLE_MAX_MS);
    expect(nextDelay(2000, 0, 1)).toBe(4000);
    expect(nextDelay(2000, 0, 10)).toBe(ERROR_MAX_MS);
  });
});

describe('useJobLogPolling', () => {
  it('first fetches the tail (how a reloaded page finds its cursor), then polls after the last seq about every 2 s', async () => {
    answers = [{ logs: [entry(1500), entry(1501)], total: 1502 }, { logs: [entry(1502)], total: 1503 }, { logs: [] }];
    const { result } = mount();
    await flush();
    expect(calls[0]).toEqual({ jobId: 'j1', after: -1, options: { limit: 1000, tail: true } });
    expect(result.current.lines.map(l => l.seq)).toEqual([1500, 1501]);
    expect(result.current.total).toBe(1502);
    await flush(POLL_MS);
    expect(calls[1]).toEqual({ jobId: 'j1', after: 1501, options: { limit: 500, tail: false } });
    expect(result.current.lines.map(l => l.seq)).toEqual([1500, 1501, 1502]);
    await flush(POLL_MS);
    expect(calls[2].after).toBe(1502);
    expect(result.current.polling).toBe(true);
  });

  it('keeps lines unique by seq and bounded', async () => {
    answers = [{ logs: [entry(1), entry(2)] }, { logs: [entry(2), entry(3)] }];
    const { result } = mount();
    await flush();
    await flush(POLL_MS);
    expect(result.current.lines.map(l => l.seq)).toEqual([1, 2, 3]);
  });

  it('stops polling while hidden, keeps the cursor and fetches at once when shown again', async () => {
    answers = [{ logs: [entry(0), entry(1)] }];
    const { result, rerender } = mount();
    await flush();
    expect(calls).toHaveLength(1);
    rerender({ live: false, terminal: false, jobId: 'j1' });
    await flush(60_000);
    expect(calls).toHaveLength(1); // no timer ran while hidden
    expect(result.current.polling).toBe(false);
    expect(result.current.lines.map(l => l.seq)).toEqual([0, 1]); // history kept
    answers = [{ logs: [entry(2)] }];
    rerender({ live: true, terminal: false, jobId: 'j1' });
    await flush();
    expect(calls[1]).toEqual({ jobId: 'j1', after: 1, options: { limit: 500, tail: false } }); // cursor not lost
    expect(result.current.lines.map(l => l.seq)).toEqual([0, 1, 2]);
  });

  it('does one last fetch when the job turns terminal, then no more', async () => {
    answers = [{ logs: [entry(0)] }, { logs: [entry(1)] }];
    const { result, rerender } = mount();
    await flush();
    rerender({ live: true, terminal: true, jobId: 'j1' });
    await flush();
    expect(calls).toHaveLength(2);
    expect(calls[1].after).toBe(0);
    expect(result.current.lines.map(l => l.seq)).toEqual([0, 1]);
    await flush(120_000);
    expect(calls).toHaveLength(2);
    expect(result.current.polling).toBe(false);
  });

  it('loads a finished job once (its history) and stops', async () => {
    answers = [{ logs: [entry(0), entry(1)], total: 2 }];
    const { result } = mount(true, true);
    await flush();
    await flush(120_000);
    expect(calls).toHaveLength(1);
    expect(result.current.lines).toHaveLength(2);
  });

  it('backs off after an error, reports it, and recovers', async () => {
    answers = [new Error('Network error'), { logs: [entry(0)] }];
    const { result } = mount();
    await flush();
    expect(result.current.error).toBe('Network error');
    await flush(POLL_MS); // base 2 s is not enough after one failure (4 s)
    expect(calls).toHaveLength(1);
    await flush(POLL_MS);
    expect(calls).toHaveLength(2);
    expect(result.current.error).toBeNull();
    expect(result.current.lines.map(l => l.seq)).toEqual([0]);
  });

  it('slows to 10 s when nothing new arrives', async () => {
    const { rerender } = mount();
    await flush();
    for (let i = 0; i < 6; i += 1) await flush(IDLE_MAX_MS); // generous: one poll per step
    const count = calls.length;
    await flush(IDLE_MAX_MS);
    expect(calls.length - count).toBeLessThanOrEqual(1);
    rerender({ live: false, terminal: false, jobId: 'j1' });
  });

  it('clears and restarts for another job', async () => {
    answers = [{ logs: [entry(5)] }, { logs: [entry(0)] }];
    const { result, rerender } = mount();
    await flush();
    rerender({ live: true, terminal: false, jobId: 'j2' });
    await flush();
    expect(calls[1]).toEqual({ jobId: 'j2', after: -1, options: { limit: 1000, tail: true } });
    expect(result.current.lines.map(l => l.seq)).toEqual([0]);
  });

  it('does nothing without a job', async () => {
    mount(true, false, null);
    await flush(10_000);
    expect(calls).toHaveLength(0);
  });
});
