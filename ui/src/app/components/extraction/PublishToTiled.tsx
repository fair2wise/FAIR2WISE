import { CheckCircle2, Loader2, UploadCloud, XCircle } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { ApiError, HIDDEN_STATUSES } from '../apiHttp';
import { canPublish } from '../capabilitiesApi';
import { useCapabilities } from '../capabilitiesHooks';
import {
  publishReasonText,
  type PublishPreview,
} from '../publishApi';
import { usePublishPreview, usePublishView, useStartPublish } from '../publishHooks';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { Label } from '../ui/label';

/** Props of {@link PublishToTiled}. */
export interface PublishToTiledProps {
  /** The bundle to publish; `null` disables the button (nothing chosen yet). */
  name: string | null;
}

const STAGE_LABELS: ReadonlyArray<[string, string]> = [
  ['export', 'Export the KG'],
  ['adopt', 'Adopt it as a frozen release'],
  ['stage', 'Stage the publication plan'],
  ['plan', 'Wait for the plan'],
];

const errorText = (error: unknown): string => {
  if (error instanceof ApiError) return publishReasonText(error.reason, error.message);
  return error instanceof Error ? error.message : 'The request failed.';
};

/**
 * "Publish to Tiled" (owner decision 2026-11-10): one button on a finished KG bundle that exports it, adopts it as
 * `<corpus>/<vN>` and stages a publication plan for this container's own Tiled. It approves nothing: the dialog says
 * the plan still needs the owner's approval, and the success state links to Pending Approvals, where the existing
 * approval route is used. Rendered only when `GET /v1/capabilities` says `can_publish: true`; hidden again when the
 * server answers 401/403/404/405 to the preview.
 */
export function PublishToTiled({ name }: PublishToTiledProps) {
  const capabilities = useCapabilities();
  const preview = usePublishPreview();
  const start = useStartPublish();
  const [open, setOpen] = useState(false);
  const [again, setAgain] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [data, setData] = useState<PublishPreview | null>(null);
  const [hidden, setHidden] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const view = usePublishView(jobId);

  if (!canPublish(capabilities) || hidden) return null;

  const load = (flag: boolean) => {
    if (!name) return;
    preview.mutate({ name, again: flag }, {
      onSuccess: result => setData(result),
      onError: error => {
        if (error instanceof ApiError && HIDDEN_STATUSES.has(error.status)) setHidden(true);
      },
    });
  };

  const openDialog = () => {
    if (!name) return;
    setAgain(false);
    setJobId(null);
    setStartError(null);
    setData(null);
    setOpen(true);
    load(false);
  };

  const confirm = () => {
    if (!name) return;
    setStartError(null);
    start.mutate({ name, again }, {
      onSuccess: result => setJobId(result.job_id),
      onError: error => setStartError(errorText(error)),
    });
  };

  const detail = view.data?.library_publish ?? null;
  const jobState = view.data?.job.state ?? null;
  const failure = detail?.failure ?? null;
  const planned = detail?.plan?.plan_sha256 ?? null;
  const running = jobId !== null && !failure && !planned && jobState !== 'failed' && jobState !== 'cancelled';
  const problems = data?.problems ?? [];
  const showResult = jobId !== null;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!name}
        onClick={openDialog}
        className="disabled:cursor-not-allowed disabled:opacity-50"
      >
        <UploadCloud aria-hidden="true" />
        Publish to Tiled
      </Button>
      <Dialog open={open} onOpenChange={next => { if (!running) setOpen(next); }}>
        <DialogContent data-testid="publish-dialog" className="top-[12%] [translate:-50%_0]">
          <DialogHeader>
            <DialogTitle>Publish {name} to Tiled</DialogTitle>
            <DialogDescription>
              This exports the KG, adopts it as a frozen release and stages a publication plan for this container&apos;s Tiled.
            </DialogDescription>
          </DialogHeader>

          {!showResult && (
            <div className="space-y-3 text-sm">
              {preview.isPending && (
                <p className="flex items-center gap-2 text-slate-500" role="status">
                  <Loader2 aria-hidden="true" className="animate-spin" /> Checking…
                </p>
              )}
              {preview.isError && (
                <p role="alert" className="text-red-600">{errorText(preview.error)}</p>
              )}
              {data && (
                <ol className="list-decimal space-y-1 pl-5" data-testid="publish-steps">
                  <li>Export <code>{data.release_file}</code> to the release folder.</li>
                  <li>
                    Adopt it as <code data-testid="publish-ref">{data.snapshot_ref}</code> (a frozen release in this
                    container; the version is chosen automatically and an existing release is never overwritten).
                  </li>
                  <li>
                    Stage a publication plan to <code>{data.target ?? 'this container’s Tiled'}</code>.{' '}
                    <strong>The plan still needs your approval</strong> on {data.review_page}; nothing is published until
                    you approve it there.
                  </li>
                </ol>
              )}
              {data?.previous && (
                <div className="space-y-1 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <p>
                    This exact KG was already published (job {data.previous.job_id}, {data.previous.state}). Confirming
                    without a new version shows that run again.
                  </p>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="publish-again"
                      checked={again}
                      onCheckedChange={value => {
                        setAgain(value === true);
                        load(value === true);
                      }}
                    />
                    <Label htmlFor="publish-again">Publish a new version anyway</Label>
                  </div>
                </div>
              )}
              {problems.map(problem => (
                <p key={problem.reason} role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-red-700">
                  {publishReasonText(problem.reason, problem.message)}
                </p>
              ))}
              {startError && <p role="alert" className="text-red-600">{startError}</p>}
            </div>
          )}

          {showResult && (
            <div className="space-y-3 text-sm" data-testid="publish-progress">
              <ul className="space-y-1">
                {STAGE_LABELS.map(([stage, label]) => {
                  const status = detail?.stages[stage] ?? 'pending';
                  return (
                    <li key={stage} className="flex items-center gap-2" data-testid={`publish-stage-${stage}`}>
                      {status === 'completed' ? <CheckCircle2 aria-hidden="true" className="text-green-600" size={16} />
                        : status === 'failed' || status === 'quarantined' ? <XCircle aria-hidden="true" className="text-red-600" size={16} />
                        : running ? <Loader2 aria-hidden="true" className="animate-spin" size={16} /> : <span className="w-4" />}
                      {label} <span className="text-slate-500">({status})</span>
                    </li>
                  );
                })}
              </ul>
              {failure && (
                <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-red-700">
                  {publishReasonText(failure.reason, failure.message)}
                </p>
              )}
              {planned && detail && (
                <div role="status" className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3" data-testid="publish-success">
                  <p>
                    Plan staged: <code>{detail.snapshot_ref}</code>, hash <code>{planned.slice(0, 12)}…</code>
                    {detail.plan?.creates !== null && detail.plan?.creates !== undefined
                      ? `, ${detail.plan.creates} creates, ${detail.plan.links ?? 0} links` : ''}.
                  </p>
                  <p>Waiting for your approval. Nothing is published yet.</p>
                  <Link to="/approvals" className="font-medium underline" onClick={() => setOpen(false)}>
                    Open Pending Approvals
                  </Link>
                </div>
              )}
              {!failure && !planned && jobState === 'succeeded' && (
                <p role="status" className="text-slate-500">
                  The plan is being prepared; it will appear on Pending Approvals.{' '}
                  <Link to="/approvals" className="font-medium underline" onClick={() => setOpen(false)}>Open Pending Approvals</Link>
                </p>
              )}
            </div>
          )}

          <DialogFooter>
            {!showResult && (
              <>
                <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
                <Button
                  type="button"
                  className="bg-slate-900! text-white hover:bg-slate-800! disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={!data || problems.length > 0 || start.isPending || preview.isPending}
                  onClick={confirm}
                >
                  {start.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
                  Publish to Tiled
                </Button>
              </>
            )}
            {showResult && (
              <Button type="button" variant="outline" disabled={running} onClick={() => setOpen(false)}>Close</Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
