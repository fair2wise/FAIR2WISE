import { AlertTriangle, ArrowDownToLine, XCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../ui/button';
import { cn } from '../ui/utils';
import type { JobLogEntry } from '../extractionApi';

/** Which lines the log shows. */
export type LogFilter = 'all' | 'problems';

/** Props of {@link RunLog}. */
export interface RunLogProps {
  /** The newest lines the page holds, oldest first. */
  lines: JobLogEntry[];
  /** Lines the server wrote for the job in all (0 when unknown). */
  total: number;
  /** True while the page polls for new lines. */
  polling: boolean;
  /** Message of the last failed poll. */
  error: string | null;
}

const NEAR_BOTTOM_PX = 24;

const ROW: Record<JobLogEntry['level'], string> = {
  debug: 'text-muted-foreground',
  info: 'text-foreground',
  warning: 'border-l-2 border-amber-500 bg-amber-500/10 pl-1 text-amber-800 dark:text-amber-300',
  error: 'border-l-2 border-destructive bg-destructive/10 pl-1 text-destructive',
};

/** Whether a log line is a warning or an error. */
export const isProblem = (line: JobLogEntry) => line.level === 'warning' || line.level === 'error';

/**
 * Live log of a run, newest at the bottom. Follows the newest line until the person scrolls up (it then pauses
 * and offers "Jump to newest"); warnings and errors are marked and can be filtered ("All" or "Warnings and
 * errors"). Says "Showing last N of M lines" so a capped log is not mistaken for the whole run.
 */
export function RunLog({ lines, total, polling, error }: RunLogProps) {
  const [filter, setFilter] = useState<LogFilter>('all');
  const [following, setFollowing] = useState(true);
  const followRef = useRef(true);
  const paneRef = useRef<HTMLDivElement>(null);

  const problems = useMemo(() => lines.filter(isProblem), [lines]);
  const shown = filter === 'problems' ? problems : lines;

  const scrollToEnd = useCallback(() => {
    const pane = paneRef.current;
    if (pane) pane.scrollTop = pane.scrollHeight;
  }, []);

  useEffect(() => {
    if (followRef.current) scrollToEnd();
  }, [shown, scrollToEnd]);

  const onScroll = () => {
    const pane = paneRef.current;
    if (!pane) return;
    const near = pane.scrollHeight - pane.scrollTop - pane.clientHeight <= NEAR_BOTTOM_PX;
    followRef.current = near;
    setFollowing(near);
  };

  const jump = () => {
    followRef.current = true;
    setFollowing(true);
    scrollToEnd();
  };

  const count = total > lines.length
    ? `Showing last ${lines.length} of ${total} lines`
    : `Showing all ${lines.length} lines`;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs text-muted-foreground" data-testid="log-count">
          {count}
          {filter === 'problems' ? ` · ${problems.length} warnings and errors` : ''}
          {polling ? ' · updating' : ''}
        </p>
        <div className="ml-auto flex gap-1" role="group" aria-label="Log filter">
          <Button type="button" size="sm" variant={filter === 'all' ? 'secondary' : 'outline'}
            aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
            All
          </Button>
          <Button type="button" size="sm" variant={filter === 'problems' ? 'secondary' : 'outline'}
            aria-pressed={filter === 'problems'} onClick={() => setFilter('problems')}>
            Warnings and errors ({problems.length})
          </Button>
        </div>
      </div>
      {error && (
        <p role="status" className="text-xs text-amber-700 dark:text-amber-300" data-testid="log-error">
          Could not refresh the log ({error}); retrying.
        </p>
      )}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={paneRef}
          role="log"
          aria-label="Extraction log"
          aria-live="off"
          tabIndex={0}
          onScroll={onScroll}
          className="min-h-40 flex-1 overflow-y-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs"
          data-testid="log-pane"
        >
          {shown.length === 0 ? (
            <p className="text-muted-foreground">
              {filter === 'problems' && lines.length > 0 ? 'No warnings or errors.' : 'No log lines yet.'}
            </p>
          ) : (
            shown.map(line => (
              <div key={line.seq} className={cn('whitespace-pre-wrap break-words', ROW[line.level])} data-level={line.level}>
                <span className="text-muted-foreground">{line.at.slice(11, 19)}</span>{' '}
                {line.level === 'error' && <XCircle aria-label="error" className="inline size-3" />}
                {line.level === 'warning' && <AlertTriangle aria-label="warning" className="inline size-3" />}{' '}
                {line.message}
              </div>
            ))
          )}
        </div>
        {!following && (
          <Button type="button" size="sm" variant="secondary" className="absolute bottom-2 right-4 shadow" onClick={jump}>
            <ArrowDownToLine aria-hidden="true" /> Following paused · Jump to newest
          </Button>
        )}
      </div>
    </div>
  );
}
