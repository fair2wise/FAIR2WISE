import { useState, type ReactNode } from 'react';
import { ExternalLink } from 'lucide-react';
import { PublicationInfo } from './data/liveAgent';
import { PublicationFavoriteButton } from './PublicationFavoriteButton';
import { getPublicationLinks, primaryLinkTitle } from './publicationLinks';
import { cn } from './ui/utils';

function formatAuthors(authors?: string[]) {
  if (!authors || authors.length === 0) return '';
  if (authors.length <= 3) return authors.join(', ');
  return `${authors.slice(0, 3).join(', ')} +${authors.length - 3}`;
}

function formatPages(pages?: number[]) {
  if (!pages || pages.length === 0) return '';
  const sorted = [...pages].sort((a, b) => a - b);
  return `page ${sorted.join(', ')}`;
}

const linkClass = 'text-sky-700 hover:text-sky-800 hover:underline';

function PublicationAnchor({
  href,
  title,
  children,
  className = '',
  onOpen,
}: {
  href: string;
  title: string;
  children: ReactNode;
  className?: string;
  onOpen?: () => void;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={className}
      onClick={() => onOpen?.()}
    >
      {children}
    </a>
  );
}

/** Most snippets a paper's list shows before "Show more". */
export const SNIPPETS_COLLAPSED = 3;
/** Longest snippet drawn (the server already cuts them; this keeps a hostile payload from flooding the card). */
const SNIPPET_MAX_CHARS = 1500;

/** Plain, bounded snippets of one paper: malformed entries are dropped. */
export function paperSnippets(publication: PublicationInfo): Array<{ text: string; page?: number }> {
  const raw = Array.isArray(publication.snippets) ? publication.snippets : [];
  const out: Array<{ text: string; page?: number }> = [];
  for (const item of raw) {
    const text = item && typeof item.text === 'string' ? item.text.trim() : '';
    if (!text) continue;
    out.push({
      text: text.length > SNIPPET_MAX_CHARS ? `${text.slice(0, SNIPPET_MAX_CHARS - 1)}\u2026` : text,
      page: typeof item.page === 'number' && Number.isFinite(item.page) ? item.page : undefined,
    });
  }
  return out;
}

/**
 * The text snippets of one paper that support the entity: a collapsed list (plain text only, never HTML), the
 * first few shown, the rest behind "Show more". Nothing is drawn for a paper with no snippets; when only a count
 * arrived the count is stated and no text is invented; the wording depends on whether the deployment enables
 * passages (`disclosed`, from `GET /v1/capabilities`).
 */
function PaperSnippets({ publication, disclosed }: { publication: PublicationInfo; disclosed: boolean | null }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const snippets = paperSnippets(publication);
  const recorded = typeof publication.snippet_count === 'number' ? publication.snippet_count : snippets.length;
  if (snippets.length === 0) {
    return recorded > 0 ? (
      <p className="mt-1 text-[11px] text-slate-500" data-testid="paper-snippets-withheld">
        {recorded} supporting passage{recorded === 1 ? '' : 's'} recorded
        {disclosed === true && '; the text is not provided to this view.'}
        {disclosed === false && '; showing them is not enabled on this deployment.'}
        {disclosed === null && '.'}
      </p>
    ) : null;
  }
  const shown = all ? snippets : snippets.slice(0, SNIPPETS_COLLAPSED);
  const hidden = snippets.length - shown.length;
  return (
    <div className="mt-1" data-testid="paper-snippets">
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
            {shown.map((snippet, index) => (
              <li key={index} className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-600">
                {snippet.text}
                {snippet.page !== undefined && <span className="ml-1 text-slate-400">(p. {snippet.page})</span>}
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
          {recorded > snippets.length && (
            <p className="mt-1 text-[11px] text-slate-400">
              {recorded - snippets.length} more recorded, not shown.
            </p>
          )}
        </>
      )}
    </div>
  );
}

export function PublicationList({
  publications,
  intro = 'Here are a list of relevant publications:',
  showSupportingNodes = false,
  showSnippets = false,
  snippetsDisclosed = null,
  showFavorite = true,
  collapseLimit,
  className = '',
  divided = false,
  renderActions,
  onOpen,
}: {
  publications: PublicationInfo[];
  intro?: string | null;
  showSupportingNodes?: boolean;
  /** List each paper's supporting text snippets (the node card). */
  showSnippets?: boolean;
  /** The deployment releases passages (`capabilities.snippets_disclosed`); `null`/absent is unknown (loading, failed, older server) and keeps the neutral count-only wording; only an explicit `false` says not enabled. */
  snippetsDisclosed?: boolean | null;
  showFavorite?: boolean;
  /** When set, show this many publications until expanded. */
  collapseLimit?: number;
  className?: string;
  divided?: boolean;
  renderActions?: (publication: PublicationInfo, index: number) => ReactNode;
  onOpen?: (publication: PublicationInfo) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!publications.length) return null;

  const shouldCollapse = collapseLimit != null && collapseLimit > 0 && publications.length > collapseLimit;
  const visiblePublications = shouldCollapse && !expanded
    ? publications.slice(0, collapseLimit)
    : publications;
  const hiddenCount = shouldCollapse && !expanded ? publications.length - collapseLimit : 0;

  return (
    <div className={cn('mt-4 space-y-3', className)}>
      {intro && <p className="text-xs leading-relaxed text-slate-700">{intro}</p>}
      <div
        className={cn(
          divided
            ? 'divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200'
            : 'space-y-3',
        )}
      >
        {visiblePublications.map((publication, index) => {
          const title = publication.paper_title || publication.source_paper || 'Untitled publication';
          const authors = formatAuthors(publication.authors);
          const meta = [
            authors,
            publication.publication_year,
            publication.journal,
            formatPages(publication.pages as number[] | undefined),
          ].filter(Boolean).join(' · ');
          const links = getPublicationLinks(publication);
          const showSearchSecondary = Boolean(links.searchUrl);
          const primaryTitle = primaryLinkTitle(links.primaryKind);
          const supportingNodes = publication.supporting_nodes ?? [];
          const actions = renderActions?.(publication, index);

          return (
            <div
              key={`${title}-${index}`}
              className={cn(
                'text-xs leading-relaxed text-slate-700',
                divided && 'bg-white px-3 py-3',
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 font-bold text-slate-800">
                  {links.primaryUrl ? (
                    <PublicationAnchor
                      href={links.primaryUrl}
                      title={primaryTitle}
                      className={`inline-flex items-start gap-1 font-bold text-slate-800 ${linkClass}`}
                      onOpen={() => onOpen?.(publication)}
                    >
                      <span>{title}</span>
                      <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 opacity-60" aria-hidden="true" />
                    </PublicationAnchor>
                  ) : (
                    <span className="font-bold">{title}</span>
                  )}
                </div>
                {(showFavorite || actions) && (
                  <div className="flex shrink-0 items-center gap-0.5">
                    {showFavorite && <PublicationFavoriteButton publication={publication} />}
                    {actions}
                  </div>
                )}
              </div>
              {meta && <div className="mt-0.5 text-slate-600">{meta}</div>}
              {(links.directLabel || showSearchSecondary) && (
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-slate-500">
                  {links.directLabel && links.primaryUrl && links.primaryKind !== 'search' && (
                    <PublicationAnchor href={links.primaryUrl} title={primaryTitle} className={linkClass}>
                      {links.directLabel}
                    </PublicationAnchor>
                  )}
                  {showSearchSecondary && (
                    <>
                      {links.directLabel && <span aria-hidden="true">·</span>}
                      <PublicationAnchor
                        href={links.searchUrl!}
                        title="Search on Semantic Scholar"
                        className={linkClass}
                      >
                        Semantic Scholar
                      </PublicationAnchor>
                    </>
                  )}
                </div>
              )}
              {showSnippets && <PaperSnippets publication={publication} disclosed={snippetsDisclosed} />}
              {showSupportingNodes && supportingNodes.length > 0 && (
                <div className="mt-1 text-slate-500">
                  Supports: {supportingNodes.slice(0, 5).map(node => node.name || node.id).join(', ')}
                  {supportingNodes.length > 5 ? ` +${supportingNodes.length - 5}` : ''}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="text-xs font-medium text-sky-700 hover:text-sky-800 hover:underline"
        >
          Show {hiddenCount} more
        </button>
      )}
      {shouldCollapse && expanded && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="text-xs font-medium text-sky-700 hover:text-sky-800 hover:underline"
        >
          Show fewer
        </button>
      )}
    </div>
  );
}
