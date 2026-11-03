import { FlaskConical, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { TERMINAL_JOB_STATES } from './extractionApi';
import { extractionKeys, useCancelJob, useExportKg, useJob, useRunningExtraction } from './extractionHooks';
import { ExtractionIdleForm } from './extraction/ExtractionIdleForm';
import { ExtractionRunView } from './extraction/ExtractionRunView';
import { useJobLogStream } from './extraction/useJobLogStream';
import { useQueryClient } from '@tanstack/react-query';

/**
 * Extraction tab (A8, T42). While a `library_extract` job exists it shows the running view
 * (progress, live log, Cancel); a run already in progress is re-attached after a page reload through
 * `GET /v1/jobs?state=running&kind=library_extract`. When the run ends the finished or failed view
 * stays until "New extraction". Otherwise it shows the idle form. It never changes the Settings model.
 */
export function ExtractionTab() {
  const queryClient = useQueryClient();
  const running = useRunningExtraction(true);
  const [watch, setWatch] = useState<{ id: string; name: string | null } | null>(null);

  // Re-attach to a run found on the server (page reload, or started elsewhere).
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    if (!watch && running.data && running.data.id !== dismissed) setWatch({ id: running.data.id, name: null });
  }, [running.data, watch, dismissed]);

  const job = useJob(watch?.id ?? null);
  const terminal = job.data ? TERMINAL_JOB_STATES.has(job.data.state) : false;
  const logs = useJobLogStream(watch?.id ?? null, { active: watch !== null && !terminal });
  const cancel = useCancelJob();
  const exportKg = useExportKg();

  let body;
  if (watch && job.data) {
    body = (
      <ExtractionRunView
        job={job.data}
        kgName={watch.name}
        lines={logs.lines}
        streaming={logs.streaming}
        cancelling={cancel.isPending}
        exporting={exportKg.isPending}
        onCancel={() => cancel.mutate(watch.id, { onError: error => toast.error(error.message) })}
        onExport={() => {
          if (!watch.name) return;
          exportKg.mutate(watch.name, {
            onSuccess: result => toast.success(`Exported ${result.file_name}`),
            onError: error => toast.error(error.message),
          });
        }}
        onNew={() => {
          setDismissed(watch.id);
          setWatch(null);
          void queryClient.invalidateQueries({ queryKey: extractionKeys.running });
          void queryClient.invalidateQueries({ queryKey: extractionKeys.bundles });
        }}
      />
    );
  } else if ((watch && job.isPending) || running.isPending) {
    body = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 aria-hidden="true" className="animate-spin" /> Loading…
      </p>
    );
  } else {
    body = <ExtractionIdleForm onStarted={(id, name) => setWatch({ id, name })} />;
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
      {body}
    </section>
  );
}
