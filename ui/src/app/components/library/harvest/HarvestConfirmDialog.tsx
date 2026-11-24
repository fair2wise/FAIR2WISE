import { AlertTriangle, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { HarvestCandidate, HarvestNeedsConfirmation } from '../../harvestApi';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../../ui/dialog';
import { Label } from '../../ui/label';

/** A dropped PDF the server did not file because the match was weak or a mismatch: the user decides. */
export interface PendingConfirmation {
  /** The queue row the file was dropped on. */
  candidate: HarvestCandidate;
  file: File;
  answer: HarvestNeedsConfirmation;
}

/** Props of {@link HarvestConfirmDialog}. */
export interface HarvestConfirmDialogProps {
  pending: PendingConfirmation | null;
  /** A request for this file is in flight. */
  busy: boolean;
  /** "File anyway": repeat the request with `confirm=true`. */
  onFileAnyway: (pending: PendingConfirmation) => void;
  /** "File it under ... instead": repeat the request with `to=<id>`. */
  onFileElsewhere: (pending: PendingConfirmation, otherId: string) => void;
  onCancel: () => void;
}

function Side({ heading, title, dois, testId }: { heading: string; title: string | null; dois: string[]; testId: string }) {
  return (
    <div className="min-w-0 space-y-1 rounded-md border border-slate-200 p-3" data-testid={testId}>
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{heading}</p>
      <p className="text-sm font-medium break-words text-slate-900">
        {title && title.trim() !== '' ? title : <span className="font-normal text-slate-500">No title found</span>}
      </p>
      <p className="text-xs break-all text-slate-700">
        {dois.length > 0 ? <>DOI: {dois.join(', ')}</> : <span className="text-slate-500">No DOI found</span>}
      </p>
    </div>
  );
}

/**
 * Asks before a PDF is filed that the server could not match strongly: both DOIs and both titles side by side, a
 * plain warning for a mismatch (with an explicit tick before "File anyway"), and, when the PDF's DOI belongs to
 * another paper still waiting in the run, a button to file it there instead.
 */
export function HarvestConfirmDialog({ pending, busy, onFileAnyway, onFileElsewhere, onCancel }: HarvestConfirmDialogProps) {
  const [checked, setChecked] = useState(false);
  const key = pending ? `${pending.candidate.id}:${pending.file.name}:${pending.file.size}` : '';
  useEffect(() => setChecked(false), [key]);

  const verdict = pending?.answer.verdict;
  const mismatch = verdict?.kind === 'mismatch';
  const other = pending?.answer.other_candidate ?? null;

  return (
    <Dialog open={pending !== null} onOpenChange={next => { if (!next && !busy) onCancel(); }}>
      <DialogContent
        data-testid="harvest-confirm-dialog"
        className="top-[10%] right-0 left-0 mx-auto max-h-[85vh] translate-x-0 translate-y-0 overflow-y-auto sm:max-w-2xl"
      >
        <DialogHeader>
          <DialogTitle>{mismatch ? 'This PDF names a different paper' : 'Check this PDF before it is filed'}</DialogTitle>
          <DialogDescription>
            {pending ? <>Nothing has been stored yet. File: <span className="font-medium">{pending.file.name}</span></> : null}
          </DialogDescription>
        </DialogHeader>

        {pending && verdict ? (
          <div className="space-y-3 text-sm text-slate-800">
            <div className="grid gap-3 sm:grid-cols-2">
              <Side
                heading="The paper in the queue"
                title={pending.answer.candidate.title}
                dois={pending.answer.candidate.doi ? [pending.answer.candidate.doi] : []}
                testId="confirm-paper"
              />
              <Side heading="The PDF you dropped" title={verdict.found_title} dois={verdict.found_dois} testId="confirm-pdf" />
            </div>
            <p data-testid="confirm-reason">{verdict.reason[0]?.toUpperCase()}{verdict.reason.slice(1)}.</p>
            {mismatch ? (
              <div role="alert" className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900">
                <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                <p>
                  Warning: the DOI in this PDF is not the DOI of the paper in the queue. A preprint or repository copy
                  of the same work can carry its own DOI, but this may also be the wrong paper. Filing it records
                  both DOIs.
                </p>
              </div>
            ) : (
              <p className="text-slate-600">
                The server could not confirm that this is the right paper. File it only if you have checked.
              </p>
            )}
            {mismatch ? (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="harvest-confirm-checked"
                  checked={checked}
                  onCheckedChange={value => setChecked(value === true)}
                />
                <Label htmlFor="harvest-confirm-checked" className="text-sm text-slate-800">
                  I have checked: this PDF is the right paper for the entry on the left
                </Label>
              </div>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>
          {pending && other ? (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => onFileElsewhere(pending, other.id)}
              className="disabled:cursor-not-allowed disabled:opacity-50"
            >
              File it under {other.title} instead
            </Button>
          ) : null}
          <Button
            type="button"
            className="bg-slate-900! text-white hover:bg-slate-800! disabled:cursor-not-allowed disabled:opacity-50"
            disabled={busy || !pending || (mismatch && !checked)}
            onClick={() => pending && onFileAnyway(pending)}
          >
            {busy ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
            File anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
