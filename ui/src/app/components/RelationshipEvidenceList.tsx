import { useState } from 'react';
import type { RelationshipEvidence, RelationshipEvidenceItem } from './data/liveAgent';

/** Most sentences a relationship shows before "Show more". */
export const RELATION_EVIDENCE_COLLAPSED = 3;
/** Longest sentence drawn (the server already cuts them; this keeps a hostile payload from flooding the card). */
const EVIDENCE_MAX_CHARS = 1500;

/** Plain, bounded sentences of one relationship: malformed entries are dropped. */
export function relationshipSentences(item: Pick<RelationshipEvidence, 'evidence'>): Array<Required<Pick<RelationshipEvidenceItem, 'text'>> & {
  page?: number;
  label: 'located' | 'quoted' | null;
}> {
  const out: Array<{ text: string; page?: number; label: 'located' | 'quoted' | null }> = [];
  for (const entry of Array.isArray(item.evidence) ? item.evidence : []) {
    const text = entry && typeof entry.text === 'string' ? entry.text.trim() : '';
    if (!text) continue;
    out.push({
      text: text.length > EVIDENCE_MAX_CHARS ? `${text.slice(0, EVIDENCE_MAX_CHARS - 1)}…` : text,
      page: typeof entry.page === 'number' && Number.isFinite(entry.page) ? entry.page : undefined,
      label: entry.kind === 'located' ? 'located' : entry.kind === 'quoted' ? 'quoted' : null,
    });
  }
  return out;
}

/**
 * The sentences that support one relationship on the node card: a collapsed list (plain text only, never HTML), the first few
 * shown, the rest behind "Show more", each with its page and a `located` label (found in the paper naming both ends). Nothing
 * is drawn for a relationship with no evidence; when only a count arrived the count is stated and no text is invented, with
 * the same wording rule as passages (`disclosed`, from `GET /v1/capabilities`).
 */
export function RelationshipEvidenceList({ item, disclosed }: { item: RelationshipEvidence; disclosed: boolean | null }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const sentences = relationshipSentences(item);
  const recorded = item.evidence_count;
  if (recorded <= 0) return null;
  if (sentences.length === 0) {
    return (
      <p className="mt-1 text-[11px] text-slate-500" data-testid="relationship-evidence-withheld">
        {recorded} supporting sentence{recorded === 1 ? '' : 's'} recorded
        {disclosed === true && '; the text is not provided to this view.'}
        {disclosed === false && '; showing them is not enabled on this deployment.'}
        {disclosed === null && '.'}
      </p>
    );
  }
  const shown = all ? sentences : sentences.slice(0, RELATION_EVIDENCE_COLLAPSED);
  const hidden = sentences.length - shown.length;
  return (
    <div className="mt-1" data-testid="relationship-evidence">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
        className="text-[11px] font-medium text-sky-700 hover:text-sky-800 hover:underline"
      >
        {open ? 'Hide' : 'Show'} supporting text ({recorded})
      </button>
      {open && (
        <>
          <ul className="mt-1 space-y-1.5 border-l-2 border-slate-200 pl-2.5">
            {shown.map((sentence, index) => (
              <li key={index} className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-600">
                {sentence.text}
                {sentence.page !== undefined && <span className="ml-1 text-slate-400">(p. {sentence.page})</span>}
                {sentence.label && (
                  <span
                    className="ml-1 text-slate-400"
                    data-testid="evidence-kind"
                    title={sentence.label === 'located'
                      ? 'Found by searching the paper for a sentence that names both ends of the relationship.'
                      : 'A sentence the extraction quoted, checked against the paper.'}
                  >
                    [{sentence.label}]
                  </span>
                )}
              </li>
            ))}
          </ul>
          {hidden > 0 && (
            <button
              type="button"
              onClick={() => setAll(true)}
              className="mt-1 text-[11px] font-medium text-sky-700 hover:text-sky-800 hover:underline"
            >
              Show {hidden} more
            </button>
          )}
          {recorded > sentences.length && (
            <p className="mt-1 text-[11px] text-slate-400">{recorded - sentences.length} more recorded, not shown.</p>
          )}
        </>
      )}
    </div>
  );
}
