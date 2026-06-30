import type { ReactNode } from 'react';
import { ExternalLink } from 'lucide-react';
import { PublicationInfo } from './data/liveAgent';
import { PublicationFavoriteButton } from './PublicationFavoriteButton';
import { getPublicationLinks, primaryLinkTitle } from './publicationLinks';

function formatAuthors(authors?: string[]) {
  if (!authors || authors.length === 0) return '';
  if (authors.length <= 3) return authors.join(', ');
  return `${authors.slice(0, 3).join(', ')} +${authors.length - 3}`;
}

function formatPages(pages?: number[]) {
  if (!pages || pages.length === 0) return '';
  const sorted = [...pages].sort((a, b) => a - b);
  return `p. ${sorted.join(', ')}`;
}

const linkClass = 'text-sky-700 hover:text-sky-800 hover:underline';

function PublicationAnchor({
  href,
  title,
  children,
  className = '',
}: {
  href: string;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={className}
    >
      {children}
    </a>
  );
}

export function PublicationList({
  publications,
  intro = 'Here are a list of relevant publications:',
  showSupportingNodes = false,
  showFavorite = true,
  renderActions,
}: {
  publications: PublicationInfo[];
  intro?: string | null;
  showSupportingNodes?: boolean;
  showFavorite?: boolean;
  renderActions?: (publication: PublicationInfo, index: number) => ReactNode;
}) {
  if (!publications.length) return null;

  return (
    <div className="mt-4 space-y-3">
      {intro && <p className="text-xs leading-relaxed text-slate-700">{intro}</p>}
      <div className="space-y-3">
        {publications.map((publication, index) => {
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
            <div key={`${title}-${index}`} className="text-xs leading-relaxed text-slate-700">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 font-bold text-slate-800">
                  {links.primaryUrl ? (
                    <PublicationAnchor
                      href={links.primaryUrl}
                      title={primaryTitle}
                      className={`inline-flex items-start gap-1 font-bold text-slate-800 ${linkClass}`}
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
    </div>
  );
}
