import { HARVEST_SOURCES, type HarvestRun } from '../../harvestApi';
import { cn } from '../../ui/utils';

/** Props of {@link HarvestRunsList}. */
export interface HarvestRunsListProps {
  runs: HarvestRun[];
  /** Id of the run whose results are open, or null. */
  selectedId: string | null;
  onSelect: (runId: string) => void;
}

/** One-line summary of a run's outcome counts. */
export function runSummary(run: HarvestRun): string {
  const { counts } = run;
  const parts = [`${counts.downloaded + counts.filed} filed`];
  if (counts.paywalled > 0) parts.push(`${counts.paywalled} paywalled`);
  if (counts.in_library > 0) parts.push(`${counts.in_library} already in library`);
  if (counts.failed > 0) parts.push(`${counts.failed} failed`);
  return `${counts.total} found · ${parts.join(' · ')}`;
}

const sourceLabel = (id: string) => HARVEST_SOURCES.find(source => source.id === id)?.label ?? id;

/** The sources of a run that could not be searched, with their plain-words reason (empty when all answered). */
export function sourceProblems(run: HarvestRun): Array<{ source: string; label: string; message: string }> {
  return Object.entries(run.source_status ?? {})
    .filter(([, status]) => status.state !== 'ok')
    .map(([source, status]) => ({ source, label: sourceLabel(source), message: status.message }));
}

/** Past harvest runs, newest first; choosing one opens its results. */
export function HarvestRunsList({ runs, selectedId, onSelect }: HarvestRunsListProps) {
  if (runs.length === 0) {
    return <p className="text-sm text-muted-foreground">No harvest runs yet.</p>;
  }
  return (
    <ul aria-label="Harvest runs" className="divide-y divide-border rounded-md border border-border">
      {runs.map(run => {
        const selected = run.id === selectedId;
        const problems = sourceProblems(run);
        return (
          <li key={run.id}>
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onSelect(run.id)}
              className={cn(
                'flex w-full flex-col items-start gap-0.5 p-3 text-left text-sm hover:bg-accent',
                selected && 'bg-accent text-accent-foreground',
              )}
            >
              <span className="w-full truncate font-medium" title={run.topic}>{run.topic}</span>
              <span className="w-full truncate text-xs text-muted-foreground">
                {run.category} · {run.created_at.slice(0, 10)} · {runSummary(run)}
              </span>
              {problems.map(problem => (
                <span
                  key={problem.source}
                  data-testid="harvest-source-error"
                  className="w-full text-xs font-medium text-red-600"
                >
                  {problem.label} could not be searched: {problem.message}
                </span>
              ))}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
