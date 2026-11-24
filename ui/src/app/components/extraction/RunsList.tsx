import { Loader2 } from 'lucide-react';
import { Badge } from '../ui/badge';
import { cn } from '../ui/utils';
import type { JobInfo } from '../extractionApi';
import { outcomeLabel } from './runFormat';

/** Props of {@link RunsList}. */
export interface RunsListProps {
  runs: JobInfo[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const stamp = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace('T', ' ') : '');

/** Recent extraction runs, newest first: bundle, outcome, papers, start time. Selecting one shows it. */
export function RunsList({ runs, selectedId, onSelect }: RunsListProps) {
  if (runs.length === 0) return null;
  return (
    <nav aria-label="Extraction runs" data-testid="runs-list">
      <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-md border border-border p-1">
        {runs.map(run => {
          const summary = run.summary ?? null;
          const outcome = outcomeLabel(summary?.outcome ?? run.state, summary);
          const moving = run.state === 'running' || run.state === 'queued';
          return (
            <li key={run.id}>
              <button
                type="button"
                aria-current={run.id === selectedId ? 'true' : undefined}
                onClick={() => onSelect(run.id)}
                className={cn(
                  'flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-muted',
                  run.id === selectedId && 'bg-muted font-medium',
                )}
              >
                {moving ? <Loader2 aria-hidden="true" className="size-3 animate-spin" /> : null}
                <span className="min-w-0 flex-1 truncate">{summary?.params.bundle ?? run.id}</span>
                <Badge variant={outcome.tone === 'bad' ? 'destructive' : 'secondary'}>{outcome.text.replace(/ \(.*\)$/, '')}</Badge>
                {summary ? (
                  <span className="text-muted-foreground">{summary.papers.done}/{summary.papers.total} papers</span>
                ) : null}
                <span className="text-muted-foreground">{stamp(summary?.started_at ?? run.created_at)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
