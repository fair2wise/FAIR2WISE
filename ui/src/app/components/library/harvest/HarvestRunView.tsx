import { Ban, CheckCircle2, Loader2, XCircle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { TERMINAL_JOB_STATES, type JobInfo, type JobLogEntry } from '../../extractionApi';
import { progressPercent } from '../../extraction/ExtractionRunView';
import { Badge } from '../../ui/badge';
import { Button } from '../../ui/button';
import { Progress } from '../../ui/progress';
import { cn } from '../../ui/utils';

/** Props of {@link HarvestRunView}. */
export interface HarvestRunViewProps {
  job: JobInfo;
  /** Topic of the run when known (unknown right after a reload until the run list loads). */
  topic: string | null;
  lines: JobLogEntry[];
  /** Lines the server wrote in all (0 until the first answer). */
  logTotal: number;
  /** Whether the log is being polled now (false while the tab is hidden or the run has ended). */
  polling: boolean;
  /** Message of the last failed log poll. */
  logError: string | null;
  cancelling: boolean;
  onCancel: () => void;
  /** Leave the finished run and return to the form. */
  onClose: () => void;
}

const LEVEL_CLASS: Record<JobLogEntry['level'], string> = {
  debug: 'text-muted-foreground',
  info: 'text-foreground',
  warning: 'text-amber-600',
  error: 'text-destructive',
};

/**
 * Running, finished, failed or cancelled view of a harvest job: state badge, progress bar, the bounded
 * live log pane (newest line in view), Cancel while running and "Back to the form" once it has ended.
 */
export function HarvestRunView({ job, topic, lines, logTotal, polling, logError, cancelling, onCancel, onClose }: HarvestRunViewProps) {
  const terminal = TERMINAL_JOB_STATES.has(job.state);
  const percent = progressPercent(job.progress);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [lines]);

  return (
    <div className="flex min-h-0 flex-col gap-4" data-testid="harvest-run">
      <div className="flex items-center gap-3">
        <h3 className="min-w-0 truncate text-base font-semibold" title={topic ?? undefined}>
          {topic ? `Harvesting “${topic}”` : 'Harvest'}
        </h3>
        <Badge variant={job.state === 'failed' ? 'destructive' : 'secondary'} data-testid="harvest-state">
          {job.state === 'succeeded' ? <CheckCircle2 aria-hidden="true" /> : null}
          {job.state === 'failed' ? <XCircle aria-hidden="true" /> : null}
          {job.state === 'cancelled' ? <Ban aria-hidden="true" /> : null}
          {!terminal ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
          {job.state}
        </Badge>
        <div className="ml-auto flex gap-2">
          {!terminal && (
            <Button type="button" variant="outline" size="sm" disabled={cancelling} onClick={onCancel}
              className="disabled:cursor-not-allowed disabled:opacity-50">
              {cancelling ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Ban aria-hidden="true" />}
              Cancel
            </Button>
          )}
          {terminal && (
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              Back to the form
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-1">
        <Progress
          value={percent ?? (terminal ? 100 : 0)}
          aria-label="Harvest progress"
          aria-valuetext={percent === null ? 'Progress unknown' : `${percent}%`}
        />
        <p className="text-xs text-muted-foreground" data-testid="harvest-progress-text">
          {percent === null ? 'Searching…' : `${job.progress.done} of ${job.progress.total} (${percent}%)`}
        </p>
      </div>

      {job.state === 'failed' && job.error && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {job.error}
        </p>
      )}

      <div className="flex min-h-0 flex-col gap-1">
        <p className="text-xs text-muted-foreground">
          Log ({logTotal > lines.length ? `last ${lines.length} of ${logTotal}` : `${lines.length}`} lines){polling ? ' · polling' : ''}
        </p>
        {logError ? (
          <p role="status" className="text-xs text-destructive" data-testid="harvest-log-error">
            Log update failed ({logError}); retrying.
          </p>
        ) : null}
        <div
          role="log"
          aria-label="Harvest log"
          aria-live="off"
          className="max-h-64 min-h-32 overflow-y-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs"
          data-testid="harvest-log"
        >
          {lines.length === 0 ? (
            <p className="text-muted-foreground">No log lines yet.</p>
          ) : (
            lines.map(line => (
              <div key={line.seq} className={cn('whitespace-pre-wrap break-words', LEVEL_CLASS[line.level])}>
                <span className="text-muted-foreground">{line.at.slice(11, 19)}</span>{' '}
                <span className="text-muted-foreground">[{line.stage}]</span> {line.message}
              </div>
            ))
          )}
          <div ref={endRef} />
        </div>
      </div>
    </div>
  );
}
