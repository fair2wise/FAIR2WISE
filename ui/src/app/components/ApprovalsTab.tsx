import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardCheck, Loader2, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { ApiError, HIDDEN_STATUSES } from './apiHttp';
import {
  approvalErrorMessage,
  fetchPendingApprovals,
  groupByKind,
  performApprovalAction,
  planMergeApply,
  shortHash,
  type ApprovalAction,
  type ApprovalItem,
} from './approvalsApi';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import { Button } from './ui/button';

/** Query key of the pending queue. */
export const pendingApprovalsKey = ['pending-approvals'] as const;
/** The queue is re-read this often while the page is open (and right after every decision). */
export const REFRESH_MS = 15_000;

type Decision = 'approve' | 'reject';

/** What the confirmation dialog is about: the item, the decision and the action that will be sent. */
interface Confirm {
  item: ApprovalItem;
  decision: Decision;
  action: ApprovalAction;
  /** Hash the decision is bound to (plan hash, or the merge dry-run hash), shown before confirming. */
  hash: string | null;
  /** Body fields merged into the action (the merge `plan_sha256`). */
  override: Record<string, unknown>;
}

interface Outcome {
  ok: boolean;
  message: string;
}

/** The hash a decision on `item` is bound to: the plan's hash, or the merge dry-run hash the page holds. */
export function decisionHash(item: ApprovalItem, mergeHashes: Record<string, string>): string | null {
  if (item.kind === 'merge_apply') return mergeHashes[item.id] ?? null;
  return item.plan?.plan_sha256 ?? null;
}

/** Decision button labels per kind (a case review "accepts" or "rejects"; a paused job "resumes"). */
export function decisionLabels(item: ApprovalItem): { approve: string; reject: string } {
  if (item.kind === 'review_case') return { approve: 'Accept', reject: 'Reject' };
  if (item.kind === 'paused_job') return { approve: 'Resume', reject: 'Cancel job' };
  if (item.kind === 'merge_apply') return { approve: 'Approve apply', reject: 'Reject' };
  return { approve: 'Approve', reject: 'Reject' };
}

function Field({ label, value, mono = false }: { label: string; value: unknown; mono?: boolean }) {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0)) return null;
  const shown = Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-32 shrink-0 text-muted-foreground">{label}</dt>
      <dd className={`break-all ${mono ? 'font-mono text-xs' : ''}`}>{shown}</dd>
    </div>
  );
}

function PlanDetails({ item }: { item: ApprovalItem }) {
  const plan = item.plan;
  return (
    <dl className="grid gap-1" data-testid="approval-details">
      <Field label="Job" value={item.job_id} mono />
      <Field label="Corpus" value={item.corpus_scope} />
      <Field label="Target" value={item.target} mono />
      <Field label="Snapshot" value={item.snapshot_ref} mono />
      <Field label="Reason" value={item.reason} />
      {plan ? (
        <>
          <Field label="Plan" value={plan.plan_id} mono />
          <Field label="Plan hash" value={plan.plan_sha256} mono />
          <Field
            label="Changes"
            value={`${plan.creates ?? 0} creates, ${plan.links ?? 0} links, ${plan.supersessions ?? 0} supersessions${plan.affected ? `, ${plan.affected} affected ids` : ''}`}
          />
          <Field label="Base revision" value={plan.base_revision} mono />
        </>
      ) : null}
      <Field label="Requested by" value={item.requested_by} />
      <Field label="Submitted" value={item.submitted_at} />
    </dl>
  );
}

function ItemDetails({ item }: { item: ApprovalItem }) {
  switch (item.kind) {
    case 'paused_job':
      return (
        <dl className="grid gap-1" data-testid="approval-details">
          <Field label="Job" value={item.job_id} mono />
          <Field label="Kind" value={item.job_kind} />
          <Field label="Corpus" value={item.corpus_scope} />
          <Field label="Pause reason" value={item.pause_reason} />
          <Field label="Requested by" value={item.requested_by} />
          <Field label="Submitted" value={item.submitted_at} />
        </dl>
      );
    case 'review_case':
      return (
        <dl className="grid gap-1" data-testid="approval-details">
          <Field label="Case" value={item.case_id} mono />
          <Field label="Corpus" value={item.corpus} />
          <Field label="Type" value={item.case_type} />
          <Field label="Reason" value={item.reason} />
          <Field label="Route" value={item.route} />
          <Field label="Priority" value={item.priority} />
          <Field label="Revision" value={item.revision} mono />
        </dl>
      );
    case 'cost_control':
      return (
        <dl className="grid gap-1" data-testid="approval-details">
          <Field label="Switch" value={item.enabled === null ? null : item.enabled ? 'enabled' : 'disabled'} />
          <Field label="Cap (USD)" value={item.budget_cap_usd} />
          <Field label="Spend" value={item.spend} mono />
          <Field label="Routes" value={item.routes.map(route => `${route.method} ${route.path}`)} mono />
        </dl>
      );
    case 'merge_apply':
      return (
        <dl className="grid gap-1" data-testid="approval-details">
          <Field label="Corpus" value={item.corpus} />
          <Field label="Accepted cases" value={item.accepted_cases} />
          <Field label="Case ids" value={item.case_ids} mono />
        </dl>
      );
    default:
      return <PlanDetails item={item} />;
  }
}

/**
 * Pending Approvals page (owner instruction 2026-11-09). Lists everything `GET /v1/approvals/pending` says is
 * waiting for a person, grouped by kind, and decides each item through the existing route the server named for
 * it (publication approval bound to the shown plan hash, job cancel/resume, case review at the shown revision,
 * merge apply after a dry run). Every Approve asks for confirmation and shows the hash it binds to; refusals are
 * shown with their typed code (409 `stale_approval`, 403, 501). The page re-reads the queue every
 * `REFRESH_MS` and after each decision. `App` shows it only when `GET /v1/capabilities` reports `can_approve`.
 */
export function ApprovalsTab() {
  const queryClient = useQueryClient();
  const queue = useQuery({
    queryKey: pendingApprovalsKey,
    queryFn: () => fetchPendingApprovals(),
    retry: false,
    refetchInterval: REFRESH_MS,
  });
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [mergeHashes, setMergeHashes] = useState<Record<string, string>>({});
  const refresh = () => queryClient.invalidateQueries({ queryKey: pendingApprovalsKey });

  const decide = useMutation({
    mutationFn: (c: Confirm) => performApprovalAction(c.action, c.override),
    onSuccess: (_result, c) => {
      const verb = c.decision === 'approve' ? decisionLabels(c.item).approve : decisionLabels(c.item).reject;
      setOutcomes(current => ({ ...current, [c.item.id]: { ok: true, message: `${verb}: done (${c.action.method} ${c.action.path}).` } }));
    },
    onError: (error, c) => {
      setOutcomes(current => ({ ...current, [c.item.id]: { ok: false, message: approvalErrorMessage(error) } }));
      if (error instanceof ApiError && error.code === 'stale_approval') {
        setMergeHashes(current => {
          const next = { ...current };
          delete next[c.item.id];
          return next;
        });
      }
    },
    onSettled: () => {
      setConfirm(null);
      void refresh();
    },
  });

  const plan = useMutation({
    mutationFn: (item: ApprovalItem) => planMergeApply(item),
    onSuccess: (hash, item) => {
      if (hash) {
        setMergeHashes(current => ({ ...current, [item.id]: hash }));
        setOutcomes(current => ({ ...current, [item.id]: { ok: true, message: `Planned: hash ${shortHash(hash)}. Approve to apply exactly this plan.` } }));
      } else {
        setOutcomes(current => ({ ...current, [item.id]: { ok: true, message: 'The dry run planned nothing to apply.' } }));
      }
    },
    onError: (error, item) => setOutcomes(current => ({ ...current, [item.id]: { ok: false, message: approvalErrorMessage(error) } })),
  });

  const open = (item: ApprovalItem, decision: Decision) => {
    const action = decision === 'approve' ? item.approve : item.reject;
    if (!action) return;
    const hash = decisionHash(item, mergeHashes);
    const override = item.kind === 'merge_apply' && decision === 'approve' && hash ? { plan_sha256: hash } : {};
    setConfirm({ item, decision, action, hash, override });
  };

  const unavailable = queue.error instanceof ApiError && HIDDEN_STATUSES.has(queue.error.status);
  const groups = groupByKind(queue.data?.items ?? []);

  return (
    <section
      aria-labelledby="approvals-tab-title"
      className="flex h-full w-full flex-col gap-4 overflow-auto rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <header className="flex flex-wrap items-center gap-2">
        <ClipboardCheck aria-hidden="true" size={24} />
        <h2 id="approvals-tab-title" className="text-lg font-semibold">
          Pending Approvals
        </h2>
        <span className="text-sm text-muted-foreground">
          {queue.data ? `${queue.data.total} waiting` : queue.isPending ? 'loading...' : ''}
          {queue.data?.generated_at ? ` · as of ${queue.data.generated_at}` : ''}
          {` · refreshes every ${REFRESH_MS / 1000} s`}
        </span>
        <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={() => void refresh()} disabled={queue.isFetching}>
          {queue.isFetching ? <Loader2 aria-hidden="true" className="mr-1 animate-spin" size={14} /> : <RefreshCw aria-hidden="true" className="mr-1" size={14} />}
          Refresh
        </Button>
      </header>

      {queue.isError ? (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {unavailable ? 'The pending-approvals route is not available to this caller on this server.' : approvalErrorMessage(queue.error)}
        </p>
      ) : null}

      {queue.isSuccess && groups.length === 0 ? (
        <p data-testid="approvals-empty" className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          Nothing is waiting for approval.
        </p>
      ) : null}

      {groups.map(group => (
        <section key={group.kind} aria-labelledby={`approvals-group-${group.kind}`} className="flex flex-col gap-3" data-testid="approval-group">
          <header>
            <h3 id={`approvals-group-${group.kind}`} className="text-base font-semibold">
              {group.label} <span className="font-normal text-muted-foreground">({group.items.length})</span>
            </h3>
            {group.help ? <p className="text-xs text-muted-foreground">{group.help}</p> : null}
          </header>
          {group.items.map(item => {
            const labels = decisionLabels(item);
            const outcome = outcomes[item.id];
            const mergeHash = item.kind === 'merge_apply' ? mergeHashes[item.id] ?? null : null;
            const busy = decide.isPending || plan.isPending;
            return (
              <article key={item.id} data-testid="approval-item" data-kind={item.kind} className="flex flex-col gap-3 rounded-lg border border-border p-4">
                <ItemDetails item={item} />
                {item.kind === 'merge_apply' ? (
                  <p className="text-xs text-muted-foreground">
                    {mergeHash ? `Planned hash ${shortHash(mergeHash)}` : 'Run the dry run first; the apply names the planned hash.'}
                  </p>
                ) : null}
                <div className="flex flex-wrap items-center gap-2">
                  {item.kind === 'merge_apply' && item.plan_route ? (
                    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => plan.mutate(item)}>
                      Plan (dry run)
                    </Button>
                  ) : null}
                  {item.approve ? (
                    <Button type="button" size="sm" disabled={busy || (item.kind === 'merge_apply' && !mergeHash)} onClick={() => open(item, 'approve')}>
                      {labels.approve}
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">no one-click route: this item needs a typed value through the routes listed</span>
                  )}
                  {item.reject ? (
                    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => open(item, 'reject')}>
                      {labels.reject}
                    </Button>
                  ) : item.approve ? (
                    <span className="text-xs text-muted-foreground">no reject route</span>
                  ) : null}
                </div>
                {outcome ? (
                  <p
                    role={outcome.ok ? 'status' : 'alert'}
                    data-testid="approval-outcome"
                    className={`rounded-md border p-2 text-sm ${outcome.ok ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-destructive/40 bg-destructive/10 text-destructive'}`}
                  >
                    {outcome.message}
                  </p>
                ) : null}
              </article>
            );
          })}
        </section>
      ))}

      <AlertDialog open={confirm !== null} onOpenChange={isOpen => { if (!isOpen && !decide.isPending) setConfirm(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirm ? `${decisionLabels(confirm.item)[confirm.decision]} ${confirm.item.kind.replace('_', ' ')} ${confirm.item.id}?` : ''}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="flex flex-col gap-2 text-sm">
                {confirm?.hash ? (
                  <p>
                    This decision is bound to the hash shown below; the server refuses it (409 stale_approval) if the plan changed since.
                    <br />
                    <code data-testid="confirm-hash" className="break-all font-mono text-xs">{confirm.hash}</code>
                  </p>
                ) : (
                  <p>{confirm?.decision === 'reject' ? 'The item is taken out of the queue through the server route below.' : 'The server route below is called with the body it published for this item.'}</p>
                )}
                {confirm ? (
                  <code className="break-all font-mono text-xs text-muted-foreground">
                    {confirm.action.method} {confirm.action.path}
                    {confirm.action.body || Object.keys(confirm.override).length ? ` ${JSON.stringify({ ...(confirm.action.body ?? {}), ...confirm.override })}` : ''}
                  </code>
                ) : null}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={decide.isPending}>Back</AlertDialogCancel>
            <AlertDialogAction
              disabled={decide.isPending}
              onClick={event => {
                event.preventDefault();
                if (confirm) decide.mutate(confirm);
              }}
            >
              {decide.isPending ? <Loader2 aria-hidden="true" className="mr-1 animate-spin" size={14} /> : null}
              {confirm ? `Confirm ${decisionLabels(confirm.item)[confirm.decision].toLowerCase()}` : 'Confirm'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
