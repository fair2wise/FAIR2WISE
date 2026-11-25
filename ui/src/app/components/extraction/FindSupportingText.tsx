import { Ban, Loader2, TextSearch } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError } from '../apiHttp';
import {
  PASSAGES_JOB_KIND,
  TERMINAL_JOB_STATES,
  fetchJobs,
  passagesReasonText,
  type JobRunSummary,
} from '../extractionApi';
import { FAST_POLL_MS, useCancelJob, useJob, useJobSummary, useStartPassages } from '../extractionHooks';
import { useLive } from '../tabActivity';
import { Button } from '../ui/button';
import { RecoveryResult } from './PassageCoverage';

/** Props of {@link FindSupportingText}. */
export interface FindSupportingTextProps {
  /** The existing KG bundle to search for; `null` disables the button (nothing chosen yet). */
  name: string | null;
}

const newNonce = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

const errorText = (error: unknown): string => {
  if (error instanceof ApiError) return passagesReasonText(error.reason, error.message);
  return error instanceof Error ? error.message : 'The request failed.';
};

/**
 * "Find supporting text" on the existing KG of the Extract page's picker: starts the `library_passages` job, which searches
 * the KG's source papers for the sentences that mention each term (no model is used; text that is already there is
 * kept). While it runs it shows the papers done of total and the paper in progress, with Cancel; at the end it shows the
 * coverage block (counts, and the papers that were skipped with the reason in plain words). A running job of this KG
 * is picked up again after a page reload. All server text is rendered as plain text.
 */
export function FindSupportingText({ name }: FindSupportingTextProps) {
  const live = useLive();
  const start = useStartPassages();
  const cancel = useCancelJob();
  const [started, setStarted] = useState<{ jobId: string; name: string } | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const jobId = started && started.name === name ? started.jobId : null;
  const job = useJob(jobId, FAST_POLL_MS, live);
  const summary = useJobSummary(jobId, FAST_POLL_MS, live);

  // A page reload: find the job of this bundle that is still moving.
  useEffect(() => {
    if (!name || jobId) return;
    let cancelled = false;
    fetchJobs({ kind: PASSAGES_JOB_KIND, detail: 'summary', limit: 5 })
      .then(({ jobs }) => {
        const moving = jobs.find(item => (item.state === 'running' || item.state === 'queued') && item.summary?.params.bundle === name);
        if (!cancelled && moving) setStarted({ jobId: moving.id, name });
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [name, jobId]);

  const state = job.data?.state ?? summary.data?.state ?? null;
  const terminal = state !== null && TERMINAL_JOB_STATES.has(state);
  const moving = jobId !== null && !terminal;
  const data: JobRunSummary | null = summary.data ?? null;
  const recovery = data?.recovery ?? null;

  const begin = () => {
    if (!name) return;
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
        disabled={!name || start.isPending || moving}
        onClick={begin}
        className="disabled:cursor-not-allowed disabled:opacity-50"
      >
        {start.isPending || moving ? <Loader2 aria-hidden="true" className="animate-spin" /> : <TextSearch aria-hidden="true" />}
        Find supporting text
      </Button>
      {name && (
        <div className="basis-full space-y-2" data-testid="find-supporting-text">
          <p id="find-supporting-text-help" className="text-xs text-muted-foreground">
            Searches the source papers for the sentences that mention each term; no model is used; existing text is kept.
          </p>
          {startError && <p role="alert" className="text-sm text-destructive">{startError}</p>}
          {moving && (
            <div className="space-y-1 text-xs" role="status" data-testid="passages-progress">
              <p>
                {data
                  ? `${data.papers.done} of ${data.papers.total} papers searched${data.papers.failed ? `, ${data.papers.failed} failed` : ''}`
                  : 'Waiting for the search to start…'}
                {data?.current_paper ? <> · now <span className="font-medium">{data.current_paper}</span></> : null}
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={cancel.isPending}
                onClick={() => jobId && cancel.mutate(jobId)}
              >
                <Ban aria-hidden="true" /> Cancel
              </Button>
            </div>
          )}
          {state === 'cancelled' && (
            <p role="status" className="text-xs">Cancelled. The papers searched before the stop keep what was found.</p>
          )}
          {state === 'failed' && (
            <p role="alert" className="text-sm text-destructive">
              The search did not finish{job.data?.error ? `: ${job.data.error}` : '.'} Papers searched before it stopped keep what was found.
            </p>
          )}
          {recovery && (terminal || moving) && <RecoveryResult recovery={recovery} />}
        </div>
      )}
    </>
  );
}
