import { useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { Check, Copy, FileSearch, Search } from 'lucide-react';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { searchPublications, type PublicationInfo } from './data/liveAgent';
import { PublicationList } from './PublicationList';
import { Checkbox } from './ui/checkbox';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

function formatCitation(publication: PublicationInfo): string {
  const authors = publication.authors?.filter(Boolean).join(', ');
  const year = publication.publication_year ? ` (${publication.publication_year}).` : '';
  const title = publication.paper_title || publication.source_paper || 'Untitled publication';
  const journal = publication.journal ? ` ${publication.journal}.` : '';
  const volumeIssue = [
    publication.volume,
    publication.issue ? `(${publication.issue})` : '',
  ].filter(Boolean).join('');
  const pages = publication.pages_range ? ` ${publication.pages_range}.` : '';
  const doi = publication.doi ? ` doi:${publication.doi}` : '';
  const lead = authors ? `${authors}${year}` : (publication.publication_year ? `${publication.publication_year}.` : '');
  return [lead, title ? ` ${title}.` : '', journal, volumeIssue ? ` ${volumeIssue}.` : '', pages, doi]
    .join('')
    .replace(/\s+/g, ' ')
    .trim();
}

export function AppPaperSearchButton() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [includeExternal, setIncludeExternal] = useState(false);
  const [publications, setPublications] = useState<PublicationInfo[]>([]);
  const [matchedNodeIds, setMatchedNodeIds] = useState<string[]>([]);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState('');
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const trimmedQuery = query.trim();

  async function runSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!trimmedQuery || loading) return;
    setLoading(true);
    setSearched(true);
    setError('');
    setCopiedKey(null);
    try {
      const response = await searchPublications(trimmedQuery, {
        maxResults: 20,
        includeExternal,
      });
      setPublications(response.publications ?? []);
      setMatchedNodeIds(response.matched_node_ids ?? []);
      setSource(response.source);
    } catch (err) {
      setPublications([]);
      setMatchedNodeIds([]);
      setSource('');
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  function handleQueryKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    void runSearch();
  }

  function copyCitation(publication: PublicationInfo, index: number) {
    const citation = formatCitation(publication);
    const key = `${publication.doi || publication.paper_title || publication.source_paper || index}-${index}`;
    navigator.clipboard.writeText(citation).then(() => {
      setCopiedKey(key);
      window.setTimeout(() => {
        setCopiedKey(prev => (prev === key ? null : prev));
      }, 2000);
    }).catch(() => {
      // Clipboard unavailable — fail silently.
    });
  }

  return (
    <>
      <ButtonWithIcon
        text="Paper search"
        icon={<FileSearch size={16} strokeWidth={2} aria-hidden="true" />}
        isSecondary
        size="small"
        aria-label="Paper search"
        onClick={() => setOpen(true)}
      />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full bg-white text-slate-800 sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>Paper search</SheetTitle>
            <SheetDescription>Citation discovery from the FAIR2WISE knowledge graph</SheetDescription>
          </SheetHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-4 px-4 pb-4">
            <form className="space-y-3" onSubmit={runSearch}>
              <textarea
                value={query}
                onChange={event => setQuery(event.target.value)}
                onKeyDown={handleQueryKeyDown}
                placeholder="Describe what you're writing about or paste a paragraph..."
                rows={6}
                className="w-full resize-none rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-sky-400 focus:bg-white focus:ring-2 focus:ring-sky-100"
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <Checkbox
                    checked={includeExternal}
                    onCheckedChange={checked => setIncludeExternal(checked === true)}
                    disabled={loading}
                    className="border-slate-300 data-[state=checked]:border-sky-600 data-[state=checked]:bg-sky-600"
                  />
                  Include papers beyond the knowledge graph
                </label>
                <button
                  type="submit"
                  disabled={!trimmedQuery || loading}
                  className="inline-flex h-8 items-center justify-center gap-2 rounded-md bg-slate-900 px-3 text-xs font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Search size={14} aria-hidden="true" />
                  {loading ? 'Searching' : 'Search'}
                </button>
              </div>
            </form>

            {loading && (
              <div className="flex items-center gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-600">
                <AsciiOrb size={24} className="text-sky-600" interactive={false} />
                Searching ranked KG nodes and publication metadata
              </div>
            )}

            {error && (
              <AppErrorMessage title="Paper search failed" className="text-xs">
                {error}
              </AppErrorMessage>
            )}

            {!loading && searched && !error && publications.length === 0 && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs leading-relaxed text-slate-600">
                No publications found for this query in the selected graph.
              </div>
            )}

            {publications.length > 0 && (
              <div className="min-h-0 flex-1 overflow-y-auto border-t border-slate-200 pt-1">
                <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                  <span>{publications.length} publication{publications.length === 1 ? '' : 's'}</span>
                  <span>{source || 'kg'} · {matchedNodeIds.length} matched node{matchedNodeIds.length === 1 ? '' : 's'}</span>
                </div>
                <PublicationList
                  publications={publications}
                  intro={null}
                  showSupportingNodes
                  renderActions={(publication, index) => {
                    const key = `${publication.doi || publication.paper_title || publication.source_paper || index}-${index}`;
                    const copied = copiedKey === key;
                    return (
                      <button
                        type="button"
                        aria-label={copied ? 'Copied citation' : 'Copy citation'}
                        title={copied ? 'Copied citation' : 'Copy citation'}
                        onClick={() => copyCitation(publication, index)}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
                      >
                        {copied ? (
                          <Check size={14} className="text-emerald-600" />
                        ) : (
                          <Copy size={14} />
                        )}
                      </button>
                    );
                  }}
                />
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
