import { useEffect, useRef, useState } from 'react';
import { fetchJobLogs, streamJobEvents, type JobLogEntry } from '../extractionApi';

/** Maximum log lines kept in memory and rendered; older lines are dropped. */
export const MAX_LOG_LINES = 500;

/** Options of {@link useJobLogStream}. */
export interface JobLogStreamOptions {
  /** Keep following while true (job not terminal). A final catch-up fetch runs when it turns false. */
  active: boolean;
  /** Delay before reconnecting a closed stream or polling the fallback endpoint. */
  retryMs?: number;
  /** Cap on retained lines. */
  maxLines?: number;
}

/** Result of {@link useJobLogStream}. */
export interface JobLogStream {
  /** The newest lines, oldest first, at most `maxLines`. */
  lines: JobLogEntry[];
  /** True while the SSE stream is connected (false while polling `GET /logs?after=`). */
  streaming: boolean;
}

const isLogEntry = (value: unknown): value is JobLogEntry =>
  typeof value === 'object' && value !== null && typeof (value as JobLogEntry).seq === 'number'
  && typeof (value as JobLogEntry).message === 'string';

/**
 * Follow a job's log lines. Loads the backlog with `GET /v1/jobs/{id}/logs?after=0`, then reads the
 * `job_log` frames of `GET /v1/jobs/{id}/stream` (via `readSseStream`), reconnecting with
 * `Last-Event-ID` set to the last seen `seq`. If the stream fails, it falls back to polling
 * `GET /logs?after=<seq>`. Lines are de-duplicated by `seq` and bounded to `maxLines`.
 *
 * @param jobId - Job to follow, or null for none.
 * @param options - See {@link JobLogStreamOptions}.
 */
export function useJobLogStream(jobId: string | null, options: JobLogStreamOptions): JobLogStream {
  const { active, retryMs = 2000, maxLines = MAX_LOG_LINES } = options;
  const [lines, setLines] = useState<JobLogEntry[]>([]);
  const [streaming, setStreaming] = useState(false);
  const lastSeq = useRef(0);

  useEffect(() => {
    setLines([]);
    lastSeq.current = 0;
  }, [jobId]);

  useEffect(() => {
    if (jobId === null) return undefined;
    const controller = new AbortController();
    const { signal } = controller;

    const append = (entries: JobLogEntry[]) => {
      const fresh = entries.filter(entry => entry.seq > lastSeq.current).sort((a, b) => a.seq - b.seq);
      if (fresh.length === 0) return;
      lastSeq.current = fresh[fresh.length - 1].seq;
      setLines(prev => [...prev, ...fresh].slice(-maxLines));
    };
    const catchUp = async () => {
      const { logs } = await fetchJobLogs(jobId, lastSeq.current);
      if (!signal.aborted) append(logs);
    };
    const wait = () =>
      new Promise<void>(resolve => {
        const timer = setTimeout(resolve, retryMs);
        signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
      });

    const run = async () => {
      try {
        await catchUp();
      } catch {
        // The stream or the next poll retries.
      }
      if (!active) return;
      while (!signal.aborted) {
        try {
          setStreaming(true);
          await streamJobEvents(jobId, {
            signal,
            lastEventId: lastSeq.current > 0 ? String(lastSeq.current) : undefined,
            onEvent: block => {
              if (block.event === 'job_log' && isLogEntry(block.data)) append([block.data]);
            },
          });
        } catch {
          if (signal.aborted) return;
          setStreaming(false);
          try {
            await catchUp();
          } catch {
            // Keep trying.
          }
        }
        setStreaming(false);
        if (signal.aborted) return;
        await wait();
      }
    };
    void run();
    return () => {
      controller.abort();
      setStreaming(false);
    };
  }, [jobId, active, retryMs, maxLines]);

  return { lines, streaming };
}
