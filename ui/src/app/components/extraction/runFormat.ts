import type { JobRunSummary, JobState, RunOutcome } from '../extractionApi';

/** The phases of one paper, in order (`stage.phase` of the summary). */
export const PHASES = ['parsing', 'chunking', 'extracting', 'merging into the KG'] as const;

/** `1 h 02 min`, `3 min 05 s` or `42 s` for a number of seconds. */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`;
  if (minutes > 0) return `${minutes} min ${String(rest).padStart(2, '0')} s`;
  return `${rest} s`;
}

/**
 * Elapsed seconds of a run: the server's figure, plus the time since this browser received it while the run
 * moves (so a clock that is off between browser and server does not show); the server's figure once it ended.
 */
export function elapsedSeconds(summary: JobRunSummary | null, state: JobState, now: number, receivedAt: number): number | null {
  if (!summary) return null;
  const moving = state === 'running' || state === 'queued';
  return moving ? summary.elapsed_seconds + Math.max(0, now - receivedAt) / 1000 : summary.elapsed_seconds;
}

/** Display label and tone of a run's final (or current) state. */
export function outcomeLabel(outcome: RunOutcome, summary: JobRunSummary | null): { text: string; tone: 'ok' | 'bad' | 'warn' | 'idle' } {
  switch (outcome) {
    case 'succeeded':
      return { text: 'Succeeded', tone: 'ok' };
    case 'partially_failed': {
      const papers = summary?.papers;
      const detail = papers ? ` (${papers.failed} of ${papers.done + papers.failed} papers failed)` : '';
      return { text: `Partially failed${detail}`, tone: 'warn' };
    }
    case 'failed':
      return { text: 'Failed', tone: 'bad' };
    case 'cancelled':
      return { text: 'Cancelled', tone: 'idle' };
    case 'queued':
      return { text: 'Queued', tone: 'idle' };
    case 'paused_budget':
    case 'paused_provider':
      return { text: 'Paused', tone: 'warn' };
    case 'awaiting_review':
      return { text: 'Awaiting review', tone: 'warn' };
    default:
      return { text: 'Running', tone: 'idle' };
  }
}

/** Percentage of papers finished (`done + failed` of `total`), or null when the total is unknown. */
export function papersPercent(summary: JobRunSummary | null, fallback: Record<string, number>): number | null {
  const done = summary ? summary.papers.done + summary.papers.failed : fallback.done;
  const total = summary ? summary.papers.total : fallback.total;
  if (typeof done !== 'number' || typeof total !== 'number' || total <= 0) return null;
  return Math.min(100, Math.round((done / total) * 100));
}
