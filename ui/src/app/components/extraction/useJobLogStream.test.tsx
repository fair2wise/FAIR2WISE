import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useJobLogStream } from './useJobLogStream';

const entry = (seq: number) => ({ seq, at: '2026-11-02T10:00:00Z', level: 'info', stage: 's', message: `m${seq}` });
const sse = (chunks: string[]) => {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        for (const x of chunks) c.enqueue(enc.encode(x));
        c.close();
      },
    }),
    { status: 200 },
  );
};
const frame = (seq: number) => `id: ${seq}\nevent: job_log\ndata: ${JSON.stringify(entry(seq))}\n\n`;

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe('useJobLogStream', () => {
  it('loads the backlog then streams job_log frames, de-duplicated by seq', async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.includes('/logs') ? json({ logs: [entry(1)] }) : sse([frame(1), frame(2)]),
    );
    const { result } = renderHook(() => useJobLogStream('j1', { active: true, retryMs: 60_000 }));
    await waitFor(() => expect(result.current.lines.map(l => l.seq)).toEqual([1, 2]));
  });

  it('reconnects with Last-Event-ID set to the last seq', async () => {
    let streams = 0;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/logs')) return json({ logs: [] });
      streams += 1;
      return sse(streams === 1 ? [frame(1), frame(2)] : [frame(3)]);
    });
    const { result } = renderHook(() => useJobLogStream('j1', { active: true, retryMs: 5 }));
    await waitFor(() => expect(result.current.lines.map(l => l.seq)).toEqual([1, 2, 3]));
    const streamCalls = fetchMock.mock.calls.filter(c => String(c[0]).includes('/stream'));
    expect((streamCalls[1][1].headers as Record<string, string>)['Last-Event-ID']).toBe('2');
  });

  it('falls back to polling /logs?after= when the stream fails', async () => {
    let polls = 0;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/stream')) return new Response('nope', { status: 500 });
      polls += 1;
      return json({ logs: polls >= 2 ? [entry(1), entry(2)] : [] });
    });
    const { result } = renderHook(() => useJobLogStream('j1', { active: true, retryMs: 5 }));
    await waitFor(() => expect(result.current.lines.map(l => l.seq)).toEqual([1, 2]));
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/logs?after='))).toBe(true);
  });

  it('keeps only the newest maxLines lines', async () => {
    const all = Array.from({ length: 600 }, (_, i) => entry(i + 1));
    fetchMock.mockImplementation(async (url: string) =>
      url.includes('/logs') ? json({ logs: all }) : sse([]),
    );
    const { result } = renderHook(() => useJobLogStream('j1', { active: false }));
    await waitFor(() => expect(result.current.lines).toHaveLength(500));
    expect(result.current.lines[0].seq).toBe(101);
    expect(result.current.lines[499].seq).toBe(600);
  });

  it('does not open a stream when inactive (one catch-up fetch only)', async () => {
    fetchMock.mockImplementation(async () => json({ logs: [entry(1)] }));
    const { result } = renderHook(() => useJobLogStream('j1', { active: false }));
    await waitFor(() => expect(result.current.lines).toHaveLength(1));
    expect(fetchMock.mock.calls.every(c => !String(c[0]).includes('/stream'))).toBe(true);
  });

  it('does nothing without a job id', () => {
    const { result } = renderHook(() => useJobLogStream(null, { active: true }));
    expect(result.current.lines).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
