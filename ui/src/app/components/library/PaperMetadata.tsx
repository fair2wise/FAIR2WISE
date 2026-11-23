import { useId, useState } from 'react';
import type { LibraryPaper } from '../libraryApi';

/** Authors shown before the rest collapse into "+N". */
export const AUTHORS_SHOWN = 3;

/** Marker for a field the server does not hold for a paper (never a blank cell). */
export function NotStored({ what }: { what: string }) {
  return (
    <span className="text-muted-foreground" title={`${what} is not stored for this paper`}>
      not stored
    </span>
  );
}

/** Publication year, or the "not stored" marker; a year read from the folder name says so in its tooltip. */
export function PaperYear({ paper }: { paper: Pick<LibraryPaper, 'year' | 'year_source'> }) {
  if (paper.year == null) return <NotStored what="Year" />;
  return (
    <span title={paper.year_source === 'folder' ? 'Year taken from the year folder' : 'Year from the category manifest'}>
      {paper.year}
    </span>
  );
}

/** First authors, then "+N"; the full list is the tooltip. "not stored" when the server holds none. */
export function PaperAuthors({ paper }: { paper: Pick<LibraryPaper, 'authors' | 'authors_total'> }) {
  const names = paper.authors ?? [];
  if (names.length === 0) return <NotStored what="Authors" />;
  const total = Math.max(paper.authors_total ?? 0, names.length);
  const rest = total - Math.min(AUTHORS_SHOWN, names.length);
  return (
    <span title={names.join(', ') + (total > names.length ? ` and ${total - names.length} more` : '')}>
      {names.slice(0, AUTHORS_SHOWN).join(', ')}
      {rest > 0 ? <span className="text-muted-foreground"> +{rest}</span> : null}
    </span>
  );
}

/** Abstract collapsed to two lines with a Show/Hide toggle, or the "not stored" marker. */
export function PaperAbstract({ paper }: { paper: Pick<LibraryPaper, 'abstract' | 'abstract_truncated' | 'file_name'> }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!paper.abstract) {
    return (
      <p className="text-xs">
        Abstract: <NotStored what="The abstract" />
      </p>
    );
  }
  return (
    <div className="text-xs">
      <p id={id} className={open ? 'whitespace-normal break-words' : 'line-clamp-2 whitespace-normal break-words'}>
        {paper.abstract}
      </p>
      <button
        type="button"
        className="text-primary underline-offset-4 hover:underline"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${open ? 'Hide' : 'Show'} abstract of ${paper.file_name}`}
        onClick={() => setOpen(value => !value)}
      >
        {open ? 'Hide abstract' : 'Show abstract'}
      </button>
      {open && paper.abstract_truncated ? <span className="text-muted-foreground"> (cut at 2000 characters)</span> : null}
    </div>
  );
}
