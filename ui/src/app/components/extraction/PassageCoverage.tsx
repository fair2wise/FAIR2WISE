import { TriangleAlert } from 'lucide-react';
import {
  PASSAGE_WARN_RATIO,
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

const row = (counts: PassageCounts) => {
  const percent = percentOf(counts.with_passage, counts.terms);
  return percent === null ? 'no terms' : `${n(counts.with_passage)} of ${n(counts.terms)} (${percent}%)`;
};

/**
 * Supporting-text coverage of an extraction (`summary.passages`): the terms that carry a passage, how the passages were
 * found (quoted from the model, located by searching the paper), the model sentences that were not found in the paper
 * and the terms left without text, per paper and in total, with a warning line for papers under 90%. All text is plain.
 */
export function ExtractionCoverage({ passages }: { passages: PassagesBlock }) {
  const under = papersUnderThreshold(passages);
  const papers = Object.entries(passages.papers).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="space-y-2" data-testid="run-passages" aria-label="Supporting text recorded">
      <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
        <div>
          <dt className="text-muted-foreground">Terms with supporting text</dt>
          <dd className="font-medium" data-testid="passages-total">{row(passages.total)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Quoted</dt>
          <dd className="font-medium">{n(passages.total.quoted)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Located</dt>
          <dd className="font-medium">{n(passages.total.located)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Sentences rejected</dt>
          <dd className="font-medium">{n(passages.total.rejected)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Terms without</dt>
          <dd className="font-medium">{n(passages.total.uncovered)}</dd>
        </div>
      </dl>
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
                <th scope="col" className="font-normal">Without</th>
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
                  <td>{n(counts.uncovered)}</td>
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
        supporting text. {n(total.papers_processed)} paper{total.papers_processed === 1 ? '' : 's'} searched
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
              <th scope="col" className="font-normal">Still without</th>
            </tr>
          </thead>
          <tbody>
            {searched.map(([name, result]) => (
              <tr key={name}>
                <th scope="row" className="pr-2 font-medium">{name}</th>
                <td className="pr-2">{n(result.terms)}</td>
                <td className="pr-2">{n(result.with_passage_before)}</td>
                <td className="pr-2">{n(result.located_added)}</td>
                <td>{n(result.still_uncovered)}</td>
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
