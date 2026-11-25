import { Ban, Loader2, Wrench } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError } from '../apiHttp';
import {
  RELATIONS_JOB_KIND,
  TERMINAL_JOB_STATES,
  fetchJobs,
  isUserSchemaId,
  relationsReasonText,
  type JobRunSummary,
} from '../extractionApi';
import { FAST_POLL_MS, useCancelJob, useJob, useJobSummary, useStartRelationsRepair } from '../extractionHooks';
import { useLive } from '../tabActivity';
import { Button } from '../ui/button';

/** Props of {@link RepairRelations}. */
export interface RepairRelationsProps {
  /** The existing KG bundle; `null` when nothing is chosen. */
  name: string | null;
  /** The bundle's locked schema id: the button renders only for a user schema (`user/<name>@vN`). */
  schema: string | null;
}

const newNonce = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
const nf = new Intl.NumberFormat('en-US');

const errorText = (error: unknown): string => {
  if (error instanceof ApiError) return relationsReasonText(error.reason, error.message);
  return error instanceof Error ? error.message : 'The request failed.';
};

/** The repair counters of a `library_relations` run summary (`counters.repair_*`), or `null` before the job reported any. */
export function repairCounts(summary: JobRunSummary | null): Record<string, number> | null {
  if (!summary) return null;
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(summary.counters)) {
    if (key.startsWith('repair_')) out[key.slice('repair_'.length)] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** One sentence for the result of a repair. */
export function repairSentence(counts: Record<string, number>): string {
  const n = (key: string) => nf.format(counts[key] ?? 0);
  const swapped = counts.swapped ?? 0;
  const attributes = counts.attributes ?? 0;
  if (swapped === 0 && attributes === 0) {
    return `Nothing to change: ${n('relations')} relations judged, ${n('invalid')} left unverified (they fit the schema neither way)`
      + `${(counts.unknown_object ?? 0) > 0 ? `, ${n('unknown_object')} point at a term this KG does not have` : ''}.`;
  }
  return `${n('swapped')} ${swapped === 1 ? 'relation was' : 'relations were'} the wrong way round and now read from the right term; `
    + `${n('attributes')} ${attributes === 1 ? 'was a value' : 'were values'} and ${attributes === 1 ? 'is' : 'are'} now stored as `
    + `${attributes === 1 ? 'a property' : 'properties'} of the term; ${n('invalid')} left unverified; ${n('terms_changed')} terms changed. `
    + 'The previous terms.json and kg.json are kept in the bundle (one generation back).';
}

/**
 * "Repair relations" on the existing KG of the Extract page's picker, for a KG on a user schema only: starts the
 * `library_relations` job, which applies the extraction's own rules to the stored relations (a relation valid only the other
 * way round moves to the right term; a relation on a value-type slot becomes a property), with no model. Shows the job while it
 * runs, with Cancel, then one sentence of counts. A running job of this KG is picked up again after a page reload.
 */
export function RepairRelations({ name, schema }: RepairRelationsProps) {
  const live = useLive();
  const start = useStartRelationsRepair();
  const cancel = useCancelJob();
  const [started, setStarted] = useState<{ jobId: string; name: string } | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const eligible = name !== null && isUserSchemaId(schema);
  const jobId = started && started.name === name ? started.jobId : null;
  const job = useJob(jobId, FAST_POLL_MS, live);
  const summary = useJobSummary(jobId, FAST_POLL_MS, live);

  useEffect(() => {
    if (!eligible || !name || jobId) return;
    let cancelled = false;
    fetchJobs({ kind: RELATIONS_JOB_KIND, detail: 'summary', limit: 5 })
      .then(({ jobs }) => {
        const moving = jobs.find(item => (item.state === 'running' || item.state === 'queued') && item.summary?.params.bundle === name);
        if (!cancelled && moving) setStarted({ jobId: moving.id, name });
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [eligible, name, jobId]);

  if (!eligible || !name) return null;

  const state = job.data?.state ?? summary.data?.state ?? null;
  const terminal = state !== null && TERMINAL_JOB_STATES.has(state);
  const moving = jobId !== null && !terminal;
  const counts = repairCounts(summary.data ?? null);

  const begin = () => {
    setStartError(null);
    start.mutate({ name, nonce: newNonce() }, {
      onSuccess: result => setStarted({ jobId: result.id, name }),
      onError: error => setStartError(errorText(error)),
    });
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={start.isPending || moving}
        onClick={begin}
        className="disabled:cursor-not-allowed disabled:opacity-50"
      >
        {start.isPending || moving ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Wrench aria-hidden="true" />}
        Repair relations
      </Button>
      <div className="basis-full space-y-2" data-testid="repair-relations">
        <p className="text-xs text-muted-foreground">
          Judges the stored relations against this KG&apos;s schema: a relation that is valid only the other way round moves to the
          right term, a relation on a value slot (string, number) becomes a property of the term. No model is used; the previous
          files are kept.
        </p>
        {startError && <p role="alert" className="text-sm text-destructive">{startError}</p>}
        {moving && (
          <div className="space-y-1 text-xs" role="status" data-testid="relations-progress">
            <p>Repairing the relations of {name}…</p>
            <Button type="button" variant="outline" size="sm" disabled={cancel.isPending} onClick={() => jobId && cancel.mutate(jobId)}>
              <Ban aria-hidden="true" /> Cancel
            </Button>
          </div>
        )}
        {state === 'cancelled' && <p role="status" className="text-xs">Cancelled before anything was written.</p>}
        {state === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            The repair did not finish{job.data?.error ? `: ${job.data.error}` : '.'} The KG&apos;s files are as they were.
          </p>
        )}
        {terminal && state === 'succeeded' && counts && (
          <p className="text-sm" role="status" data-testid="relations-result">{repairSentence(counts)}</p>
        )}
      </div>
    </>
  );
}
