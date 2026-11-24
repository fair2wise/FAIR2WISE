import { FlaskConical, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { TERMINAL_JOB_STATES, type JobInfo } from './extractionApi';
import { FAST_POLL_MS, useCancelJob, useExportKg, useExtractionRuns, useJob, useJobSummary } from './extractionHooks';
import { ExtractionIdleForm } from './extraction/ExtractionIdleForm';
import { ExtractionRunView } from './extraction/ExtractionRunView';
import { PublishToTiled } from './extraction/PublishToTiled';
import { RunsList } from './extraction/RunsList';
import { useJobLogPolling } from './extraction/useJobLogPolling';
import { useLive } from './tabActivity';

/** What the tab shows: a run of the server's list, or the form for a new one. */
type Choice = { kind: 'auto' } | { kind: 'run'; id: string } | { kind: 'new' };

const isMoving = (state: string) => state === 'running' || state === 'queued';

/**
 * Extraction tab (A8, T42). Everything it shows about a run comes from the job store through the API: the runs
 * list (newest first, bounded), the run's progress summary and its log (polled with a cursor), so a page reload or
 * a tab switch loses nothing. Opening the tab shows the running job, else the most recent run, else the form;
 * "New extraction" opens the form. Polling stops while the tab or the page is hidden and resumes on return.
 * It never changes the Settings model.
 */
export function ExtractionTab() {
  const live = useLive();
  const runs = useExtractionRuns(live);
  const [choice, setChoice] = useState<Choice>({ kind: 'auto' });
  // A just-started run is shown at once, before the list has caught up.
  const [started, setStarted] = useState<{ id: string; name: string } | null>(null);

  const list = runs.data ?? [];
  const autoId = (list.find(run => isMoving(run.state)) ?? list[0])?.id ?? null;
  const selectedId = choice.kind === 'run' ? choice.id : choice.kind === 'auto' ? autoId : null;
  const name = started && started.id === selectedId ? started.name : null;

  useEffect(() => {
    // Once the list shows the started run, forget the shortcut.
    if (started && list.some(run => run.id === started.id)) setStarted(null);
  }, [list, started]);

  const job = useJob(selectedId, FAST_POLL_MS, live);
  const summary = useJobSummary(selectedId, FAST_POLL_MS, live);
  const listed = list.find(run => run.id === selectedId);
  const state = job.data?.state ?? listed?.state;
  const terminal = state ? TERMINAL_JOB_STATES.has(state) : false;
  const logs = useJobLogPolling(selectedId, { live, terminal });
  const cancel = useCancelJob();
  const exportKg = useExportKg();

  const current: JobInfo | null = selectedId && (job.data || listed)
    ? { ...(job.data ?? (listed as JobInfo)), summary: summary.data ?? listed?.summary ?? undefined,
        ...(summary.data || listed?.summary ? {
          progress: { done: (summary.data ?? listed?.summary)!.papers.done, total: (summary.data ?? listed?.summary)!.papers.total },
          error: job.data?.error ?? (summary.data ?? listed?.summary)!.error,
        } : {}) }
    : null;

  let body;
  if (selectedId && current) {
    body = (
      <ExtractionRunView
        job={current}
        kgName={name}
        lines={logs.lines}
        logTotal={logs.total}
        polling={logs.polling}
        logError={logs.error}
        live={live}
        cancelling={cancel.isPending}
        exporting={exportKg.isPending}
        publishSlot={<PublishToTiled name={current.summary?.params.bundle ?? name} />}
        onCancel={() => cancel.mutate(selectedId, { onError: error => toast.error(error.message) })}
        onExport={() => {
          const bundle = current.summary?.params.bundle ?? name;
          if (!bundle) return;
          exportKg.mutate(bundle, {
            onSuccess: result => toast.success(`Exported ${result.file_name}`),
            onError: error => toast.error(error.message),
          });
        }}
        onNew={() => setChoice({ kind: 'new' })}
      />
    );
  } else if (selectedId || (choice.kind === 'auto' && runs.isPending)) {
    body = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 aria-hidden="true" className="animate-spin" /> Loading…
      </p>
    );
  } else {
    body = (
      <ExtractionIdleForm
        onStarted={(id, bundle) => {
          setStarted({ id, name: bundle });
          setChoice({ kind: 'run', id });
        }}
      />
    );
  }

  return (
    <section
      aria-labelledby="extraction-tab-title"
      className="flex h-full w-full flex-col gap-4 overflow-y-auto rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <div className="flex items-center gap-2">
        <FlaskConical aria-hidden="true" size={24} />
        <h2 id="extraction-tab-title" className="text-lg font-semibold">
          Extraction
        </h2>
      </div>
      <p className="text-sm text-muted-foreground">Build or continue a knowledge graph from library papers.</p>
      <RunsList runs={list} selectedId={selectedId} onSelect={id => setChoice({ kind: 'run', id })} />
      {body}
    </section>
  );
}
