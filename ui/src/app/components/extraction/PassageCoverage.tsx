import { TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';
import {
  PASSAGE_WARN_RATIO,
  RELATION_WARN_RATIO,
  RECOVERY_REASON_TEXT,
  type PassageCounts,
  type PassagesBlock,
  type RecoveryBlock,
} from '../extractionApi';

const nf = new Intl.NumberFormat('en-US');
const n = (value: number | undefined) => nf.format(value ?? 0);

/** Whole-number percentage of `part` in `whole`; `null` when `whole` is 0. */
export function percentOf(part: number, whole: number): number | null {
  return whole > 0 ? Math.round((part / whole) * 100) : null;
}

/** Names of the papers with terms where fewer than {@link PASSAGE_WARN_RATIO} carry a passage, with their percentage. */
export function papersUnderThreshold(passages: PassagesBlock): Array<{ name: string; percent: number }> {
  return Object.entries(passages.papers)
    .filter(([, row]) => row.terms > 0 && row.with_passage < PASSAGE_WARN_RATIO * row.terms)
    .map(([name, row]) => ({ name, percent: percentOf(row.with_passage, row.terms) ?? 0 }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Papers with relations where fewer than {@link RELATION_WARN_RATIO} have a sentence naming both ends, with their percentage. */
export function papersUnderRelationThreshold(passages: PassagesBlock): Array<{ name: string; percent: number }> {
  return Object.entries(passages.papers)
    .filter(([, row]) => (row.relations ?? 0) > 0 && (row.relations_with_evidence ?? 0) < RELATION_WARN_RATIO * (row.relations ?? 0))
    .map(([name, row]) => ({ name, percent: percentOf(row.relations_with_evidence ?? 0, row.relations ?? 0) ?? 0 }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

const relationRow = (counts: PassageCounts) => {
  const percent = percentOf(counts.relations_with_evidence ?? 0, counts.relations ?? 0);
  return percent === null
    ? 'no relations'
    : `${n(counts.relations_with_evidence)} of ${n(counts.relations)} (${percent}%)`;
};

const row = (counts: PassageCounts) => {
  const percent = percentOf(counts.with_passage, counts.terms);
  return percent === null ? 'no terms' : `${n(counts.with_passage)} of ${n(counts.terms)} (${percent}%)`;
};

/** Both coverage rows use this grid, so their columns line up: five equal columns from a small screen up, two below. */
const GRID = 'grid grid-cols-2 gap-2 text-xs sm:grid-cols-5';

/** One cell of the coverage grid: a label (up to two lines, the same height in every cell) over a right-aligned-figure value. */
function Stat({ label, testId, children }: { label: string; testId?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col justify-between gap-1 rounded-md border border-border bg-muted/30 px-3 py-2">
      <dt className="min-h-[2rem] leading-4 text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium tabular-nums" data-testid={testId}>{children}</dd>
    </div>
  );
}

/**
 * Supporting-text coverage of an extraction (`summary.passages`): the terms that carry a passage, how the passages were
 * found (quoted from the model, located by searching the paper), the model sentences that were not found in the paper
 * and the terms left without text, per paper and in total, with a warning line for papers under 90%. All text is plain.
 */
export function ExtractionCoverage({ passages }: { passages: PassagesBlock }) {
  const under = papersUnderThreshold(passages);
  const relationsUnder = papersUnderRelationThreshold(passages);
  const hasRelations = (passages.total.relations ?? 0) > 0;
  const papers = Object.entries(passages.papers).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="space-y-2" data-testid="run-passages" aria-label="Supporting text recorded">
      <dl className={GRID} data-testid="run-term-coverage">
        <Stat label="Terms with supporting text" testId="passages-total">{row(passages.total)}</Stat>
        <Stat label="Quoted">{n(passages.total.quoted)}</Stat>
        <Stat label="Located">{n(passages.total.located)}</Stat>
        <Stat label="Sentences rejected">{n(passages.total.rejected)}</Stat>
        <Stat label="Terms without">{n(passages.total.uncovered)}</Stat>
      </dl>
      {hasRelations && (
        <dl className={GRID} data-testid="run-relation-evidence">
          <Stat label="Relations with a sentence naming both ends" testId="relations-total">{relationRow(passages.total)}</Stat>
          <Stat label="Relation sentences stored">{n(passages.total.evidence_sentences)}</Stat>
          <Stat label="Relations without">{n(passages.total.relations_without)}</Stat>
        </dl>
      )}
      {relationsUnder.length > 0 && (
        <p role="status" className="flex items-start gap-1 text-xs text-amber-700 dark:text-amber-300" data-testid="relations-warning">
          <TriangleAlert aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
          <span>
            Under {Math.round(RELATION_WARN_RATIO * 100)}% of the relations have a sentence naming both ends for{' '}
            {relationsUnder.map(item => `${item.name} (${item.percent}%)`).join(', ')}.
          </span>
        </p>
      )}
      {under.length > 0 && (
        <p role="status" className="flex items-start gap-1 text-xs text-amber-700 dark:text-amber-300" data-testid="passages-warning">
          <TriangleAlert aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
          <span>
            Under {Math.round(PASSAGE_WARN_RATIO * 100)}% of the terms have supporting text for{' '}
            {under.map(item => `${item.name} (${item.percent}%)`).join(', ')}. Find supporting text on this page can add
            more.
          </span>
        </p>
      )}
      {papers.length > 0 && (
        <details className="text-xs" data-testid="passages-papers">
          <summary className="cursor-pointer">Per paper ({papers.length})</summary>
          <table className="mt-1 w-full text-left">
            <thead>
              <tr className="text-muted-foreground">
                <th scope="col" className="pr-2 font-normal">Paper</th>
                <th scope="col" className="pr-2 font-normal">Terms with text</th>
                <th scope="col" className="pr-2 font-normal">Quoted</th>
                <th scope="col" className="pr-2 font-normal">Located</th>
                <th scope="col" className="pr-2 font-normal">Rejected</th>
                <th scope="col" className="pr-2 font-normal">Without</th>
                <th scope="col" className="font-normal">Relations with a sentence</th>
              </tr>
            </thead>
            <tbody>
              {papers.map(([name, counts]) => (
                <tr key={name}>
                  <th scope="row" className="pr-2 font-medium">{name}</th>
                  <td className="pr-2">{row(counts)}</td>
                  <td className="pr-2">{n(counts.quoted)}</td>
                  <td className="pr-2">{n(counts.located)}</td>
                  <td className="pr-2">{n(counts.rejected)}</td>
                  <td className="pr-2">{n(counts.uncovered)}</td>
                  <td>{relationRow(counts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

/** Plain words for a paper a recovery skipped; the server's own text is the fallback for an unknown reason. */
export function recoveryReasonText(reason: string | undefined, serverText: string | undefined): string {
  return (reason && RECOVERY_REASON_TEXT[reason]) || serverText || 'This paper was skipped.';
}

/**
 * The result of "Find supporting text" (`summary.recovery`): how many terms got text and how many passages, the terms
 * that still have none, a line per searched paper, and the skipped papers with the reason in plain words.
 */
export function RecoveryResult({ recovery }: { recovery: RecoveryBlock }) {
  const { total } = recovery;
  const entries = Object.entries(recovery.papers).sort(([a], [b]) => a.localeCompare(b));
  const searched = entries.filter(([, result]) => !result.reason);
  const skipped = entries.filter(([, result]) => result.reason);
  return (
    <section className="space-y-2 text-sm" data-testid="recovery-result" aria-label="Supporting text found">
      <p data-testid="recovery-summary">
        {total.located_added > 0
          ? `Found supporting text for ${n(total.newly_covered)} more term${total.newly_covered === 1 ? '' : 's'} `
            + `(${n(total.located_added)} passage${total.located_added === 1 ? '' : 's'}).`
          : 'Nothing new to add.'}{' '}
        {n(total.still_uncovered)} term{total.still_uncovered === 1 ? '' : 's'} still {total.still_uncovered === 1 ? 'has' : 'have'} no
        supporting text.{' '}
        {(total.relations ?? 0) > 0
          ? `Relations: ${n(total.evidence_added)} sentence${total.evidence_added === 1 ? '' : 's'} naming both ends found for `
            + `${n(total.relations_newly_covered)} relation${total.relations_newly_covered === 1 ? '' : 's'}; `
            + `${n(total.relations_still_without)} still ${total.relations_still_without === 1 ? 'has' : 'have'} none. `
          : ''}
        {(total.publication_terms_corrected ?? 0) > 0
          ? `Publication records (title, authors, year, venue) were set from the library on ${n(total.publication_terms_corrected)} term${total.publication_terms_corrected === 1 ? '' : 's'}. `
          : ''}
        {n(total.papers_processed)} paper{total.papers_processed === 1 ? '' : 's'} searched
        {total.papers_skipped > 0 ? `, ${n(total.papers_skipped)} skipped` : ''}.
      </p>
      {searched.length > 0 && (
        <table className="w-full text-left text-xs" data-testid="recovery-papers">
          <thead>
            <tr className="text-muted-foreground">
              <th scope="col" className="pr-2 font-normal">Paper</th>
              <th scope="col" className="pr-2 font-normal">Terms</th>
              <th scope="col" className="pr-2 font-normal">Had text</th>
              <th scope="col" className="pr-2 font-normal">Added</th>
              <th scope="col" className="pr-2 font-normal">Still without</th>
              <th scope="col" className="pr-2 font-normal">Relation sentences added</th>
              <th scope="col" className="font-normal">Pages without text</th>
            </tr>
          </thead>
          <tbody>
            {searched.map(([name, result]) => (
              <tr key={name}>
                <th scope="row" className="pr-2 font-medium">{name}</th>
                <td className="pr-2">{n(result.terms)}</td>
                <td className="pr-2">{n(result.with_passage_before)}</td>
                <td className="pr-2">{n(result.located_added)}</td>
                <td className="pr-2">{n(result.still_uncovered)}</td>
                <td className="pr-2">{n(result.evidence_added)}</td>
                <td>{(result.pages_without_text ?? 0) > 0 ? `${n(result.pages_without_text)} of ${n(result.pages)}` : '0'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {skipped.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-700 dark:text-amber-300" data-testid="recovery-skipped">
          {skipped.map(([name, result]) => (
            <li key={name}>
              <span className="font-medium">{name}</span>: {recoveryReasonText(result.reason, result.text)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
