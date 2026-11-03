import { Ban, CheckCircle2, Download, Loader2, XCircle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { TERMINAL_JOB_STATES, type JobInfo, type JobLogEntry } from '../extractionApi';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Progress } from '../ui/progress';
import { cn } from '../ui/utils';

/** Props of {@link ExtractionRunView}. */
export interface ExtractionRunViewProps {
  job: JobInfo;
  /** Bundle name of the run when known (shown, and enables Export); unknown after a reload. */
  kgName: string | null;
  lines: JobLogEntry[];
  /** Whether the log is streamed live (otherwise polled). */
  streaming: boolean;
  cancelling: boolean;
  exporting: boolean;
  onCancel: () => void;
  onExport: () => void;
  /** Leave the finished run and return to the idle form. */
  onNew: () => void;
}

const LEVEL_CLASS: Record<JobLogEntry['level'], string> = {
  debug: 'text-muted-foreground',
  info: 'text-foreground',
  warning: 'text-amber-600',
  error: 'text-destructive',
};

/** Progress percentage from `{done, total}`; `null` when the total is unknown. */
export function progressPercent(progress: Record<string, number>): number | null {
  const { done, total } = progress;
  if (typeof done !== 'number' || typeof total !== 'number' || total <= 0) return null;
  return Math.min(100, Math.round((done / total) * 100));
}

/**
 * Running, finished, failed or cancelled view of an extraction job: state badge, progress bar, the
 * bounded live log pane (auto-scrolls to the newest line), Cancel while running, and Export or
 * "New extraction" once it has ended.
 */
export function ExtractionRunView({
  job, kgName, lines, streaming, cancelling, exporting, onCancel, onExport, onNew,
}: ExtractionRunViewProps) {
  const terminal = TERMINAL_JOB_STATES.has(job.state);
  const percent = progressPercent(job.progress);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [lines]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4" data-testid="extraction-run">
      <div className="flex items-center gap-3">
        <h3 className="min-w-0 truncate text-base font-semibold" title={kgName ?? undefined}>
          {kgName ? `Extracting ${kgName}` : 'Extraction'}
        </h3>
        <Badge variant={job.state === 'failed' ? 'destructive' : 'secondary'} data-testid="run-state">
          {job.state === 'succeeded' ? <CheckCircle2 aria-hidden="true" /> : null}
          {job.state === 'failed' ? <XCircle aria-hidden="true" /> : null}
          {job.state === 'cancelled' ? <Ban aria-hidden="true" /> : null}
          {!terminal ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
          {job.state}
        </Badge>
        <div className="ml-auto flex gap-2">
          {!terminal && (
            <Button type="button" variant="outline" size="sm" disabled={cancelling} onClick={onCancel}>
              {cancelling ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Ban aria-hidden="true" />}
              Cancel
            </Button>
          )}
          {job.state === 'succeeded' && (
            <Button type="button" size="sm" disabled={exporting || !kgName} onClick={onExport}>
              {exporting ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Download aria-hidden="true" />}
              Export
            </Button>
          )}
          {terminal && (
            <Button type="button" variant="outline" size="sm" onClick={onNew}>
              New extraction
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-1">
        <Progress
          value={percent ?? (terminal ? 100 : 0)}
          aria-label="Extraction progress"
          aria-valuetext={percent === null ? 'Progress unknown' : `${percent}%`}
        />
        <p className="text-xs text-muted-foreground" data-testid="run-progress-text">
          {percent === null
            ? 'Waiting for progress…'
            : `${job.progress.done} of ${job.progress.total} papers (${percent}%)`}
        </p>
      </div>

      {job.state === 'failed' && job.error && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {job.error}
        </p>
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-1">
        <p className="text-xs text-muted-foreground">
          Log (last {lines.length} lines){!terminal && !streaming ? ' · polling' : ''}
        </p>
        <div
          role="log"
          aria-label="Extraction log"
          aria-live="off"
          className="min-h-40 flex-1 overflow-y-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs"
          data-testid="log-pane"
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
