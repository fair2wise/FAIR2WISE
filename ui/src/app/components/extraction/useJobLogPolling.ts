import { useEffect, useRef, useState } from 'react';
import { fetchJobLogs, type JobLogEntry, type JobLogsResponse } from '../extractionApi';

/** Most lines kept in memory and rendered; older lines are dropped from the page (not from the server). */
export const MAX_LOG_LINES = 1000;
/** Poll period while the run is moving. */
export const POLL_MS = 2000;
/** Longest wait between polls when nothing new arrives. */
export const IDLE_MAX_MS = 10_000;
/** Longest wait between polls after failures. */
export const ERROR_MAX_MS = 30_000;
/** Polls without a new line before the period starts to grow. */
const IDLE_GRACE = 5;

/** Options of {@link useJobLogPolling}. */
export interface JobLogPollingOptions {
  /** Poll while true: the tab is on screen and the page is visible (`useLive`). Nothing is scheduled otherwise. */
  live: boolean;
  /** The job reached a final state: one last fetch, then no more polls. */
  terminal: boolean;
  /** Cap on retained lines. */
  maxLines?: number;
  /** Base period (tests shorten it). */
  pollMs?: number;
  /** Fetcher (tests inject one). */
  fetchLogs?: (jobId: string, after: number, fetcher: typeof fetch, options: { limit?: number; tail?: boolean }) => Promise<JobLogsResponse>;
}

/** Result of {@link useJobLogPolling}. */
export interface JobLogPolling {
  /** The newest lines, oldest first, at most `maxLines`. */
  lines: JobLogEntry[];
  /** Lines the server wrote for the job in all (`0` until the first answer; from an older server, the last seq + 1). */
  total: number;
  /** Message of the last failed poll, cleared by the next good one. */
  error: string | null;
  /** True while polls are scheduled (live and not final). */
  polling: boolean;
}

/** Wait before the next poll: the base period, longer when idle (up to 10 s) and after failures (up to 30 s). */
export function nextDelay(base: number, idlePolls: number, failures: number): number {
  if (failures > 0) return Math.min(base * 2 ** failures, ERROR_MAX_MS);
  if (idlePolls < IDLE_GRACE) return base;
  return Math.min(base * 2 ** (idlePolls - IDLE_GRACE + 1), Math.max(IDLE_MAX_MS, base));
}

/**
 * Follow a job's log by short polling with a cursor: `GET /v1/jobs/{id}/logs?after=<last seq>&limit=500`. (The SSE
 * route stays on the server, but a proxy in front of it may cut a stream after 30 s, so the page polls.)
 *
 * The first fetch asks for the tail (`tail=true`), which is also how a reloaded page finds its cursor, so a page
 * that opens in the middle of a run shows the history so far. Then it polls from the last seq about every 2 s,
 * slowing to 10 s when no line arrives and to at most 30 s after errors. When `live` is false (hidden tab or page)
 * no timer runs and the cursor is kept; when it turns true a poll runs at once. When `terminal` turns true it
 * does one last fetch and stops. Lines are de-duplicated by `seq`.
 *
 * @param jobId - Job to follow, or null for none (state is cleared).
 * @param options - See {@link JobLogPollingOptions}.
 */
export function useJobLogPolling(jobId: string | null, options: JobLogPollingOptions): JobLogPolling {
  const { live, terminal, maxLines = MAX_LOG_LINES, pollMs = POLL_MS, fetchLogs = fetchJobLogs } = options;
  const [lines, setLines] = useState<JobLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const cursor = useRef<number | null>(null);
  const finalFetched = useRef(false);
  const fetchRef = useRef(fetchLogs);
  fetchRef.current = fetchLogs;

  useEffect(() => {
    setLines([]);
    setTotal(0);
    setError(null);
    cursor.current = null;
    finalFetched.current = false;
  }, [jobId]);

  useEffect(() => {
    if (jobId === null || !live) return undefined;
    if (terminal && finalFetched.current) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let idle = 0;
    let failures = 0;

    const once = async (): Promise<number> => {
      const first = cursor.current === null;
      const answer = await fetchRef.current(jobId, cursor.current ?? -1, fetch, {
        limit: first ? maxLines : 500,
        tail: first,
      });
      if (cancelled) return 0;
      const fresh = answer.logs.filter(entry => cursor.current === null || entry.seq > cursor.current)
        .sort((a, b) => a.seq - b.seq);
      if (fresh.length > 0) {
        cursor.current = fresh[fresh.length - 1].seq;
        setLines(previous => [...previous, ...fresh.filter(e => !previous.some(p => p.seq === e.seq))].slice(-maxLines));
      } else if (cursor.current === null) {
        cursor.current = -1; // an empty log: poll from the start
      }
      const last = cursor.current ?? -1;
      setTotal(typeof answer.total === 'number' ? answer.total : last + 1);
      setError(null);
      return fresh.length;
    };

    const tick = async () => {
      try {
        const got = await once();
        failures = 0;
        idle = got > 0 ? 0 : idle + 1;
      } catch (caught) {
        failures += 1;
        if (!cancelled) setError(caught instanceof Error ? caught.message : 'Could not load the log.');
      }
      if (cancelled) return;
      if (terminal) {
        if (failures === 0) finalFetched.current = true; // the one last fetch happened
        else timer = setTimeout(() => void tick(), nextDelay(pollMs, idle, failures));
        return;
      }
      timer = setTimeout(() => void tick(), nextDelay(pollMs, idle, failures));
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [jobId, live, terminal, maxLines, pollMs]);

  return { lines, total, error, polling: jobId !== null && live && !terminal };
}
