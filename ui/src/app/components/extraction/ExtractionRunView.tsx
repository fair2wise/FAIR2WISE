import { Ban, CheckCircle2, Download, Loader2, TriangleAlert, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import { TERMINAL_JOB_STATES, type ExportResult, type JobInfo, type JobLogEntry } from '../extractionApi';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { ExportedNotice } from './ExportedNotice';
import { ExtractionCoverage } from './PassageCoverage';
import { RunLog } from './RunLog';
import { RunProgress } from './RunProgress';
import { outcomeLabel } from './runFormat';

/** Props of {@link ExtractionRunView}. */
export interface ExtractionRunViewProps {
  /** The run: its state, and the live `summary` from the server when it has sent one. */
  job: JobInfo;
  /** Bundle name of the run when known; the run's own `summary.params.bundle` wins (so Export works after a reload). */
  kgName: string | null;
  /** The newest log lines the page holds, oldest first. */
  lines: JobLogEntry[];
  /** Lines the server wrote for the job in all (0 when unknown). */
  logTotal?: number;
  /** Whether the log is being polled right now. */
  polling?: boolean;
  /** Message of the last failed log poll. */
  logError?: string | null;
  /** Tab on screen and page visible (the elapsed clock ticks only then). */
  live?: boolean;
  cancelling: boolean;
  exporting: boolean;
  /** The last successful Export of this run's KG (shown with a Download link); `null` before any. */
  exported?: ExportResult | null;
  onCancel: () => void;
  onExport: () => void;
  /** Optional extra action shown beside Export once the run succeeded (the Publish to Tiled button). */
  publishSlot?: ReactNode;
  /** Leave the finished run and return to the idle form. */
  onNew: () => void;
}

/** Progress percentage from `{done, total}`; `null` when the total is unknown. */
export function progressPercent(progress: Record<string, number>): number | null {
  const { done, total } = progress;
  if (typeof done !== 'number' || typeof total !== 'number' || total <= 0) return null;
  return Math.min(100, Math.round((done / total) * 100));
}

/**
 * One extraction run: the outcome badge (succeeded, failed, partially failed with the failed papers listed,
 * cancelled), phase, papers done of total, the paper in progress, elapsed time and KG growth ({@link RunProgress}),
 * the live log ({@link RunLog}), Cancel while it moves, and Export or "New extraction" once it has ended.
 */
export function ExtractionRunView({
  job, kgName, lines, logTotal = 0, polling = false, logError = null, live = true, cancelling, exporting,
  onCancel, onExport, publishSlot, onNew, exported = null,
}: ExtractionRunViewProps) {
  const terminal = TERMINAL_JOB_STATES.has(job.state);
  const summary = job.summary ?? null;
  const outcome = outcomeLabel(summary?.outcome ?? job.state, summary);
  const bundle = summary?.params.bundle ?? kgName;
  const exportable = job.state === 'succeeded' || summary?.outcome === 'partially_failed';
  const failedList = summary?.papers.failed_list ?? [];
  const note = job.state === 'failed' ? (job.error ?? summary?.error) : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4" data-testid="extraction-run">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="min-w-0 truncate text-base font-semibold" title={bundle ?? undefined}>
          {bundle ? `${terminal ? 'Extraction' : 'Extracting'} ${bundle}` : 'Extraction'}
        </h3>
        <Badge variant={outcome.tone === 'bad' ? 'destructive' : 'secondary'} data-testid="run-state">
          {outcome.tone === 'ok' ? <CheckCircle2 aria-hidden="true" /> : null}
          {outcome.tone === 'bad' ? <XCircle aria-hidden="true" /> : null}
          {outcome.tone === 'warn' && terminal ? <TriangleAlert aria-hidden="true" /> : null}
          {job.state === 'cancelled' ? <Ban aria-hidden="true" /> : null}
          {!terminal ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
          {summary ? outcome.text : job.state}
        </Badge>
        {summary?.params.model && (
          <span className="text-xs text-muted-foreground" data-testid="run-model">
            {summary.params.backend ?? 'cborg'} · {summary.params.model}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          {!terminal && (
            <Button type="button" variant="outline" size="sm" disabled={cancelling} onClick={onCancel}>
              {cancelling ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Ban aria-hidden="true" />}
              Cancel
            </Button>
          )}
          {exportable && (
            <Button type="button" size="sm" disabled={exporting || !bundle} onClick={onExport}>
              {exporting ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Download aria-hidden="true" />}
              Export
            </Button>
          )}
          {job.state === 'succeeded' && publishSlot}
          {terminal && (
            <Button type="button" variant="outline" size="sm" onClick={onNew}>
              New extraction
            </Button>
          )}
        </div>
      </div>

      <RunProgress job={job} live={live} />

      {summary?.passages && <ExtractionCoverage passages={summary.passages} />}

      {summary?.schema_prompt_condensed && (
        <p role="status" className="rounded-md border border-border bg-muted p-3 text-sm" data-testid="schema-prompt-condensed">
          The schema prompt was condensed: {summary.schema_prompt_condensed.full_chars.toLocaleString('en-US')} characters in full,{' '}
          {summary.schema_prompt_condensed.used_chars.toLocaleString('en-US')} sent (budget{' '}
          {summary.schema_prompt_condensed.budget.toLocaleString('en-US')}). The model saw class names with their first sentence
          and relation domains only, so a long schema may be matched less precisely.
        </p>
      )}

      <ExportedNotice result={exported} />

      {note && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {note}
        </p>
      )}
      {failedList.length > 0 && (
        <details open={summary?.outcome === 'partially_failed'} className="text-sm" data-testid="failed-papers">
          <summary className="cursor-pointer text-amber-700 dark:text-amber-300">
            {failedList.length} failed paper{failedList.length === 1 ? '' : 's'}
          </summary>
          <ul className="mt-1 list-disc pl-5 text-xs">
            {failedList.map(item => (
              <li key={item.name}>
                <span className="font-medium">{item.name}</span>: {item.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      <RunLog lines={lines} total={logTotal} polling={polling} error={logError} />
    </div>
  );
}
