import { PublicationInfo } from './data/liveAgent';

const DOI_URL_PREFIX_RE = /^https?:\/\/(?:dx\.)?doi\.org\//i;
const CROSSREF_DOI_RE = /^10\.\d{4,}\/[^\s]+$/i;
const DOI_PDF_FILENAME_RE = /^(10\.\d{4,})[_/](.+)\.pdf$/i;
/** Wiley-style PDF names omit the DOI slash: 10.1002/aenm.201702831 → 10.1002aenm.201702831.pdf */
const DOI_PDF_FILENAME_STRIPPED_RE = /^(10\.\d{4,})([a-z][a-z0-9]*\.\d+)\.pdf$/i;
const ARXIV_ID_RE = /^(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)$/i;
const ARXIV_PDF_FILENAME_RE = /^(?:arxiv[_-]?)?(\d{4}\.\d{4,5}(?:v\d+)?)\.pdf$/i;
const SEMANTIC_SCHOLAR_SEARCH = 'https://www.semanticscholar.org/search?q=';

export type PublicationLinkKind = 'doi' | 'arxiv' | 'search' | null;

export interface PublicationLinks {
  primaryUrl: string | null;
  primaryKind: PublicationLinkKind;
  /** Secondary discovery link (Semantic Scholar — avoids Google Scholar CAPTCHAs). */
  searchUrl: string | null;
  directLabel: string | null;
}

function normalizeIdentifier(raw?: string): string {
  const trimmed = (raw ?? '').trim();
  if (!trimmed) return '';
  return trimmed.replace(DOI_URL_PREFIX_RE, '').trim();
}

function stripTrailingPunctuation(value: string): string {
  return value.replace(/[.,;)]+$/, '');
}

function doiFromPdfFilename(source: string): string | null {
  const withSeparator = source.match(DOI_PDF_FILENAME_RE);
  if (withSeparator) return `${withSeparator[1]}/${withSeparator[2]}`;

  const slashStripped = source.match(DOI_PDF_FILENAME_STRIPPED_RE);
  if (slashStripped) return `${slashStripped[1]}/${slashStripped[2]}`;

  return null;
}

export function parseCrossrefDoi(publication: PublicationInfo): string | null {
  const source = publication.source_paper?.trim() ?? '';
  const sourceDoi = doiFromPdfFilename(source);
  const id = normalizeIdentifier(publication.doi || sourceDoi || '');
  if (!id) return null;
  if (/^arxiv:/i.test(id)) return null;

  const candidate = stripTrailingPunctuation(id);
  if (ARXIV_ID_RE.test(candidate)) return null;
  if (CROSSREF_DOI_RE.test(candidate)) return candidate;
  if (/^10\.\d{4,}\//.test(candidate)) return candidate;
  return null;
}

export function parseArxivId(publication: PublicationInfo): string | null {
  for (const raw of [normalizeIdentifier(publication.doi), publication.source_paper?.trim() ?? '']) {
    if (!raw) continue;
    const sourceMatch = raw.match(ARXIV_PDF_FILENAME_RE);
    if (sourceMatch) return sourceMatch[1];
    const match = raw.match(ARXIV_ID_RE);
    if (match) return match[1];
  }
  return null;
}

export function doiUrl(doi: string): string {
  return `https://doi.org/${doi}`;
}

export function arxivUrl(arxivId: string): string {
  return `https://arxiv.org/abs/${arxivId}`;
}

function isPdfFilename(value: string): boolean {
  return value.toLowerCase().endsWith('.pdf');
}

export function buildPublicationSearchQuery(publication: PublicationInfo): string | null {
  const crossref = parseCrossrefDoi(publication);
  if (crossref) return crossref;

  const arxiv = parseArxivId(publication);
  if (arxiv) return `arXiv:${arxiv}`;

  const title = publication.paper_title?.trim();
  const author = publication.authors?.[0]?.trim();
  const year = publication.publication_year;

  if (title) {
    return [title, author, year ? String(year) : undefined].filter(Boolean).join(' ');
  }

  const rawDoi = normalizeIdentifier(publication.doi);
  if (rawDoi && !isPdfFilename(rawDoi)) return rawDoi;

  const source = publication.source_paper?.trim();
  if (source && !isPdfFilename(source)) return source;

  return null;
}

/** Semantic Scholar search — stable for outbound links; Google Scholar often CAPTCHAs. */
export function semanticScholarSearchUrl(publication: PublicationInfo): string | null {
  const query = buildPublicationSearchQuery(publication);
  if (!query) return null;
  return `${SEMANTIC_SCHOLAR_SEARCH}${encodeURIComponent(query)}`;
}

export function getPublicationLinks(publication: PublicationInfo): PublicationLinks {
  const crossref = parseCrossrefDoi(publication);
  const arxiv = parseArxivId(publication);
  const searchUrl = semanticScholarSearchUrl(publication);

  if (crossref) {
    return {
      primaryUrl: doiUrl(crossref),
      primaryKind: 'doi',
      searchUrl,
      directLabel: `doi.org/${crossref}`,
    };
  }

  if (arxiv) {
    return {
      primaryUrl: arxivUrl(arxiv),
      primaryKind: 'arxiv',
      searchUrl,
      directLabel: `arxiv.org/abs/${arxiv}`,
    };
  }

  return {
    primaryUrl: searchUrl,
    primaryKind: searchUrl ? 'search' : null,
    searchUrl,
    directLabel: null,
  };
}

export function primaryLinkTitle(kind: PublicationLinkKind): string {
  switch (kind) {
    case 'doi':
      return 'Open paper (DOI)';
    case 'arxiv':
      return 'Open paper (arXiv)';
    case 'search':
      return 'Search on Semantic Scholar';
    default:
      return 'Open publication';
  }
}
