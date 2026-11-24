import { ExternalLink, Upload } from 'lucide-react';
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { isOpenableUrl, needsPdf, type HarvestCandidate } from '../../harvestApi';
import { isPdfFile } from '../../libraryApi';
import { Button } from '../../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../ui/table';
import { cn } from '../../ui/utils';
import { HarvestStatusBadge } from './HarvestStatusBadge';
import { SuppliedBadge } from '../SuppliedBadge';

/** Props of {@link HarvestResults}. */
export interface HarvestResultsProps {
  candidates: HarvestCandidate[];
  /** Id of the candidate whose PDF is being filed or that is being dismissed (its controls show busy). */
  busyId: string | null;
  /** Offer the PDF the user saved for a candidate that needs one (paywalled, blocked or failed). */
  onFile: (candidate: HarvestCandidate, file: File) => void;
  onDismiss: (candidate: HarvestCandidate) => void;
  /** Called with the names of files rejected client-side because they are not PDFs. */
  onRejected: (names: string[]) => void;
  /** Shown at the top of the queue: the multi-file drop area. */
  batch?: ReactNode;
}

/** The queue in working order: best tier first, then highest score, then title (the server's own order). */
export function sortQueue(candidates: readonly HarvestCandidate[]): HarvestCandidate[] {
  return [...candidates].sort((a, b) => a.tier.localeCompare(b.tier) || b.score - a.score || a.title.localeCompare(b.title));
}

/** DOI or arXiv id of a candidate, for display. */
function identifier(candidate: HarvestCandidate): string | null {
  return candidate.doi ?? (candidate.arxiv_id ? `arXiv:${candidate.arxiv_id}` : null);
}

/** Where "Open at publisher" goes: the server's `open_url` (DOI or landing page, behind the library proxy when set), else the landing page. */
export function publisherHref(candidate: HarvestCandidate): string | null {
  if (isOpenableUrl(candidate.open_url)) return candidate.open_url;
  return isOpenableUrl(candidate.landing_url) ? candidate.landing_url : null;
}

/** One candidate that needs a PDF by hand: Open at publisher, a PDF drop slot and Dismiss. */
function NeedsPdfItem({ candidate, busy, onFile, onDismiss, onRejected }: {
  candidate: HarvestCandidate;
  busy: boolean;
  onFile: HarvestResultsProps['onFile'];
  onDismiss: HarvestResultsProps['onDismiss'];
  onRejected: HarvestResultsProps['onRejected'];
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handle = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    const pdf = files.find(isPdfFile);
    const rejected = files.filter(file => !isPdfFile(file)).map(file => file.name);
    if (rejected.length > 0) onRejected(rejected);
    if (pdf) onFile(candidate, pdf);
  };
  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!busy) handle(event.dataTransfer.files);
  };
  const href = publisherHref(candidate);
  const showDetail = candidate.status !== 'paywalled' && !!candidate.detail;

  return (
    <li
      aria-busy={busy}
      data-dragging={dragging}
      data-candidate-id={candidate.id}
      onDragOver={event => {
        event.preventDefault();
        if (!busy) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn('flex flex-wrap items-center gap-3 p-3', dragging && 'bg-accent text-accent-foreground')}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={candidate.title}>{candidate.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {[candidate.year, identifier(candidate), `tier ${candidate.tier}`].filter(Boolean).join(' · ')}
        </p>
        {showDetail ? (
          <p className="truncate text-xs text-muted-foreground" title={candidate.detail as string}>
            {candidate.detail}
          </p>
        ) : null}
      </div>
      <HarvestStatusBadge status={candidate.status} detail={candidate.detail} />
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
          aria-label={`Open at publisher: ${candidate.title}`}
        >
          <ExternalLink aria-hidden="true" className="size-4" />
          Open at publisher
        </a>
      ) : (
        <span className="text-xs text-muted-foreground">No publisher link</span>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        aria-label={`Choose PDF for ${candidate.title}`}
        data-testid={`harvest-choose-${candidate.id}`}
        onClick={() => inputRef.current?.click()}
        className="disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Upload aria-hidden="true" />
        Choose or drop PDF
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        data-testid={`harvest-file-${candidate.id}`}
        aria-label={`PDF file for ${candidate.title}`}
        disabled={busy}
        onChange={event => {
          handle(event.target.files);
          event.target.value = '';
        }}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={busy}
        aria-label={`Dismiss ${candidate.title}`}
        onClick={() => onDismiss(candidate)}
        className="disabled:cursor-not-allowed disabled:opacity-50"
      >
        Dismiss
      </Button>
    </li>
  );
}

/**
 * When the row that last held focus leaves the queue (filed, or dismissed), move focus to the next row's "Choose PDF"
 * button so a person can go down the list without hunting for the mouse. Nothing moves when focus was elsewhere.
 */
export function useFocusNextAfterRemoval(listRef: { current: HTMLElement | null }, ids: readonly string[]) {
  const previous = useRef<readonly string[]>(ids);
  const focusedId = useRef<string | null>(null);
  useEffect(() => {
    const list = listRef.current;
    const onFocusIn = (event: FocusEvent) => {
      const row = (event.target as HTMLElement | null)?.closest?.('[data-candidate-id]');
      focusedId.current = row ? row.getAttribute('data-candidate-id') : null;
    };
    list?.addEventListener('focusin', onFocusIn);
    return () => list?.removeEventListener('focusin', onFocusIn);
  });
  useEffect(() => {
    const before = previous.current;
    previous.current = ids;
    const held = focusedId.current;
    if (held === null || ids.includes(held) || !before.includes(held)) return;
    const active = document.activeElement;
    if (active && active !== document.body && listRef.current?.contains(active)) return;   // focus is already on a live row
    const after = before.slice(before.indexOf(held) + 1).find(id => ids.includes(id)) ?? ids[ids.length - 1];
    focusedId.current = null;
    if (!after) return;
    listRef.current?.querySelector<HTMLElement>(`[data-testid="harvest-choose-${after}"]`)?.focus();
  }, [ids, listRef]);
}

/** The rights note shown on the queue (and in the docs). */
export const RIGHTS_NOTE = 'Text-mining rights for subscription content are the operator\u2019s responsibility to confirm; '
  + 'this tool only files what a person with access supplies.';

/**
 * Results of one harvest run: the queue of papers that need a PDF (paywalled, blocked by network policy or failed to
 * fetch; people finish these: open the publisher in their own browser, save the PDF, drop it here) and a table of
 * every other candidate with its outcome.
 */
export function HarvestResults({ candidates, busyId, onFile, onDismiss, onRejected, batch }: HarvestResultsProps) {
  const queue = sortQueue(candidates.filter(needsPdf));
  const listRef = useRef<HTMLUListElement>(null);
  useFocusNextAfterRemoval(listRef, queue.map(candidate => candidate.id));
  const rest = candidates.filter(candidate => !needsPdf(candidate));
  if (candidates.length === 0) {
    return <p className="text-sm text-muted-foreground">No papers found yet.</p>;
  }
  return (
    <div className="space-y-4">
      <section aria-labelledby="harvest-queue-title" className="space-y-2">
        <h4 id="harvest-queue-title" className="text-sm font-semibold">
          Needs a PDF ({queue.length} left)
        </h4>
        {queue.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is waiting for a PDF.</p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              These need a human: open the paper at the publisher, save the PDF in your own browser, then choose or
              drop it here. It is checked against the paper and filed in the run&apos;s category by year.
            </p>
            <p className="text-xs text-muted-foreground" data-testid="harvest-rights-note">{RIGHTS_NOTE}</p>
            {batch}
            <ul ref={listRef} aria-label="Papers needing a PDF" className="divide-y divide-border rounded-md border border-border">
              {queue.map(candidate => (
                <NeedsPdfItem
                  key={candidate.id}
                  candidate={candidate}
                  busy={busyId === candidate.id}
                  onFile={onFile}
                  onDismiss={onDismiss}
                  onRejected={onRejected}
                />
              ))}
            </ul>
          </>
        )}
      </section>

      <section aria-labelledby="harvest-results-title" className="space-y-2">
        <h4 id="harvest-results-title" className="text-sm font-semibold">
          Results ({rest.length})
        </h4>
        {rest.length === 0 ? (
          <p className="text-sm text-muted-foreground">No other results.</p>
        ) : (
          <Table aria-label="Harvest results">
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Year</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>DOI</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rest.map(candidate => (
                <TableRow key={candidate.id}>
                  <TableCell className="max-w-72 truncate" title={candidate.relpath ?? candidate.title}>
                    {candidate.title}
                  </TableCell>
                  <TableCell>{candidate.year ?? <span className="text-muted-foreground">?</span>}</TableCell>
                  <TableCell>{candidate.source}</TableCell>
                  <TableCell>{candidate.tier}</TableCell>
                  <TableCell className="max-w-48 truncate" title={identifier(candidate) ?? undefined}>
                    {identifier(candidate) ?? <span className="text-muted-foreground">No DOI</span>}
                  </TableCell>
                  <TableCell>
                    <HarvestStatusBadge status={candidate.status} detail={candidate.detail} />
                    {candidate.provenance ? <SuppliedBadge provenance={candidate.provenance} /> : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  );
}
