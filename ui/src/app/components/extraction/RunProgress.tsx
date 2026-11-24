import { useEffect, useMemo, useState } from 'react';
import { Progress } from '../ui/progress';
import { cn } from '../ui/utils';
import type { JobInfo } from '../extractionApi';
import { elapsedSeconds, formatDuration, papersPercent, PHASES } from './runFormat';

/** Props of {@link RunProgress}. */
export interface RunProgressProps {
  job: JobInfo;
  /** Tick the elapsed time once a second (false while the tab is hidden). */
  live: boolean;
}

const nf = new Intl.NumberFormat('en-US');
const n = (value: number | undefined) => nf.format(value ?? 0);

/**
 * Progress of one run: the phase of the paper being processed, papers done of total with the paper's display
 * name, elapsed time, and the KG growth counters (entities and relations, new versus merged into existing nodes).
 * Everything comes from the server's summary, so a reloaded page shows the same figures.
 */
export function RunProgress({ job, live }: RunProgressProps) {
  const summary = job.summary ?? null;
  const moving = job.state === 'running' || job.state === 'queued';
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live || !moving) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live, moving]);

  const percent = papersPercent(summary, job.progress);
  const papers = summary?.papers;
  const phase = summary?.stage.phase ?? null;
  const receivedAt = useMemo(() => Date.now(), [summary]);
  const elapsed = elapsedSeconds(summary, job.state, now, receivedAt);
  const growth = summary?.growth ?? {};
  const pagesDone = summary?.counters.paper_pages_done;
  const pagesTotal = summary?.counters.paper_pages_total;

  return (
    <div className="space-y-3" data-testid="run-progress">
      <div className="space-y-1">
        <Progress
          value={percent ?? (job.state === 'succeeded' ? 100 : 0)}
          aria-label="Extraction progress"
          aria-valuetext={percent === null ? 'Progress unknown' : `${percent}%`}
        />
        <p className="text-xs text-muted-foreground" data-testid="run-progress-text">
          {percent === null
            ? 'Waiting for progress…'
            : papers
              ? `${papers.done} of ${papers.total} papers done${papers.failed ? `, ${papers.failed} failed` : ''}${papers.skipped ? `, ${papers.skipped} already in the KG` : ''} (${percent}%)`
              : `${job.progress.done} of ${job.progress.total} papers (${percent}%)`}
          {elapsed !== null ? <span data-testid="run-elapsed"> · elapsed {formatDuration(elapsed)}</span> : null}
        </p>
      </div>

      {moving && (
        <div className="space-y-1" data-testid="run-now">
          <ol className="flex flex-wrap gap-1 text-xs" aria-label="Stage of the current paper">
            {PHASES.map(name => (
              <li
                key={name}
                aria-current={phase === name ? 'step' : undefined}
                className={cn(
                  'rounded-md border px-2 py-0.5',
                  phase === name ? 'border-primary bg-primary/10 font-medium' : 'text-muted-foreground',
                )}
              >
                {name}
              </li>
            ))}
          </ol>
          {summary?.current_paper && (
            <p className="text-xs" data-testid="run-current-paper">
              Now: <span className="font-medium">{summary.current_paper}</span>
              {typeof pagesTotal === 'number' && pagesTotal > 0 && typeof pagesDone === 'number'
                ? ` (page ${Math.min(pagesDone, pagesTotal)} of ${pagesTotal})`
                : ''}
            </p>
          )}
        </div>
      )}

      {summary && Object.keys(growth).length > 0 && (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4" data-testid="run-growth" aria-label="Knowledge graph growth">
          <div>
            <dt className="text-muted-foreground">Entities</dt>
            <dd className="font-medium">{n(growth.entities_total ?? (growth.entities_new ?? 0))} in the KG</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Entities this run</dt>
            <dd className="font-medium">{n(growth.entities_new)} new · {n(growth.entities_merged)} merged</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Relations</dt>
            <dd className="font-medium">{n(growth.relations_total ?? (growth.relations_new ?? 0))} in the KG</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Relations this run</dt>
            <dd className="font-medium">{n(growth.relations_new)} new · {n(growth.relations_merged)} merged</dd>
          </div>
        </dl>
      )}
    </div>
  );
}
