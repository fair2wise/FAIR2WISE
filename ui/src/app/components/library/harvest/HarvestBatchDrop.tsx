import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Upload } from 'lucide-react';
import { useEffect, useRef, useState, type DragEvent } from 'react';
import { MAX_FILES_PER_DROP, type HarvestFileResult } from '../../harvestApi';
import { harvestKeys } from '../../harvestHooks';
import { libraryKeys } from '../../libraryHooks';
import { Button } from '../../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';
import { cn } from '../../ui/utils';
import {
  BATCH_CONCURRENCY,
  confirmOne,
  dropOne,
  matchedTitle,
  planDrop,
  runPool,
  type BatchOutcome,
  type BatchRow,
} from './batchDrop';

/** Props of {@link HarvestBatchDrop}. */
export interface HarvestBatchDropProps {
  /** The open run: every file is matched against its open candidates. */
  runId: string;
  /** A file was filed (strong match, or confirmed here): the panel shows it with Undo. */
  onFiled: (file: File, result: HarvestFileResult) => void;
}

function describe(outcome: BatchOutcome): string {
  switch (outcome.state) {
    case 'queued': return 'Waiting';
    case 'sending': return 'Sending…';
    case 'filed':
      if (outcome.duplicate) return 'Already in the library';
      return outcome.confirmed ? 'Filed (confirmed by you)' : outcome.kind === 'strong' ? 'Strong match: filed' : 'Filed';
    case 'confirm': return outcome.answer.verdict.kind === 'mismatch' ? 'Mismatch: not filed' : 'Weak match: not filed';
    case 'none': return 'No match: not stored';
    case 'rejected': return outcome.message;
    case 'error': return `Failed: ${outcome.message}`;
  }
}

/**
 * The multi-file drop at the top of the queue: up to {@link MAX_FILES_PER_DROP} PDFs, each sent as its own request
 * (two at a time) and matched against all open papers of the run. Only a strong match is filed; a weak one waits
 * for Confirm; a file that matches nothing is listed and never stored; one failed file does not stop the others.
 */
export function HarvestBatchDrop({ runId, onFiled }: HarvestBatchDropProps) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [refused, setRefused] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const live = useRef(true);
  const nextKey = useRef(0);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
    void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
  };
  const setOutcome = (key: string, outcome: BatchOutcome) => {
    if (live.current) setRows(current => current.map(row => (row.key === key ? { ...row, outcome } : row)));
  };

  const handle = async (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    const plan = planDrop(files);
    if (!plan.ok) {
      setRefused(plan.message);
      return;
    }
    setRefused(null);
    const fresh: BatchRow[] = [
      ...plan.send.map(file => ({ key: `f${nextKey.current++}`, file, outcome: { state: 'queued' } as BatchOutcome })),
      ...plan.notPdf.map(file => ({
        key: `f${nextKey.current++}`, file,
        outcome: { state: 'rejected', message: 'Not sent: only PDF files can be filed' } as BatchOutcome,
      })),
    ];
    setRows(fresh);
    setSending(true);
    await runPool(fresh.filter(row => row.outcome.state === 'queued'), BATCH_CONCURRENCY, async row => {
      setOutcome(row.key, { state: 'sending' });
      const { outcome, result } = await dropOne(runId, row.file);
      setOutcome(row.key, outcome);
      if (result && !result.duplicate) {
        onFiled(row.file, result);
        refresh();
      }
    }, () => !live.current);
    if (live.current) setSending(false);
    refresh();
  };

  const confirm = async (row: BatchRow) => {
    if (row.outcome.state !== 'confirm') return;
    const target = row.outcome.answer.candidate.id;
    setOutcome(row.key, { state: 'sending' });
    const { outcome, result } = await confirmOne(target, row.file);
    setOutcome(row.key, outcome);
    if (result && !result.duplicate) onFiled(row.file, result);
    refresh();
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!sending) void handle(event.dataTransfer.files);
  };

  return (
    <div className="space-y-2" data-testid="harvest-batch">
      <div
        data-dragging={dragging}
        onDragOver={event => {
          event.preventDefault();
          if (!sending) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'flex flex-wrap items-center gap-3 rounded-md border-2 border-dashed border-border p-3 text-sm',
          dragging && 'border-primary bg-accent text-accent-foreground',
        )}
      >
        <Upload aria-hidden="true" className="size-4 text-muted-foreground" />
        <p className="min-w-0 flex-1">
          Drop up to {MAX_FILES_PER_DROP} PDFs here. Each is matched to a paper below; only a strong match is filed.
        </p>
        <Button type="button" variant="outline" size="sm" disabled={sending} onClick={() => inputRef.current?.click()}
          className="disabled:cursor-not-allowed disabled:opacity-50">
          {sending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Upload aria-hidden="true" />}
          Choose PDFs
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="application/pdf,.pdf"
          hidden
          data-testid="harvest-batch-input"
          aria-label="PDF files for the whole run"
          disabled={sending}
          onChange={event => {
            void handle(event.target.files);
            event.target.value = '';
          }}
        />
      </div>

      {refused ? <p role="alert" className="text-sm text-destructive" data-testid="harvest-batch-refused">{refused}</p> : null}

      {rows.length > 0 ? (
        <Table aria-label="Dropped files">
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Matched paper</TableHead>
              <TableHead>Result</TableHead>
              <TableHead><span className="sr-only">Action</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(row => (
              <TableRow key={row.key} data-outcome={row.outcome.state}>
                <TableCell className="max-w-48 truncate" title={row.file.name}>{row.file.name}</TableCell>
                <TableCell className="max-w-72 truncate" title={matchedTitle(row.outcome) ?? undefined}>
                  {matchedTitle(row.outcome) ?? <span className="text-muted-foreground">None</span>}
                </TableCell>
                <TableCell>{describe(row.outcome)}</TableCell>
                <TableCell>
                  {row.outcome.state === 'confirm' ? (
                    <Button type="button" size="sm" variant="outline" aria-label={`Confirm ${row.file.name}`}
                      onClick={() => void confirm(row)}>
                      Confirm
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
