import type { LiveGraphNode, PublicationInfo } from './data/liveAgent';
import { parseArxivId, parseCrossrefDoi } from './publicationLinks';
import { resolveViewerNodeId } from './kgCatalog';

const KG_CITATION_RE = /\[KG:\s*([^\]]+?)\s*\]/gi;
const TAGGED_CITE_RE = /\[(KG|PDF|OPS):\s*([^\]]+?)\s*\]/gi;
const MARKDOWN_BOLD_RE = /\*\*([^*]+)\*\*/g;
const CODE_FENCE_RE = /```[\s\S]*?```/g;
const PDF_FILENAME_RE = /\b([A-Za-z0-9][A-Za-z0-9._-]*\.pdf)\b/gi;
const GRAPH_ID_PREFIX_RE = /^(rsoxs_v\d+|bl1101|xray_demo|tiled):\s*(.+)$/i;
const COOKBOOK_PAGE_RE = /^page(esafs?|proposals?|samples?|blueskyruns?)$/i;
const ESAF_LABEL_RE = /\bESAF[:\s-]*([0-9]{4}[-_]\d{4,6})\b/i;

export type AnswerHighlightSegment = { text: string; bold: boolean };

/** Split answer text into plain/bold segments for KG citations, PDF filenames, and markdown bold. */
export function splitAnswerHighlightSegments(text: string): AnswerHighlightSegment[] {
  type Span = { start: number; end: number };
  const spans: Span[] = [];

  MARKDOWN_BOLD_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = MARKDOWN_BOLD_RE.exec(text)) !== null) {
    spans.push({ start: match.index, end: match.index + match[0].length });
  }

  TAGGED_CITE_RE.lastIndex = 0;
  while ((match = TAGGED_CITE_RE.exec(text)) !== null) {
    spans.push({ start: match.index, end: match.index + match[0].length });
  }

  PDF_FILENAME_RE.lastIndex = 0;
  while ((match = PDF_FILENAME_RE.exec(text)) !== null) {
    spans.push({ start: match.index, end: match.index + match[0].length });
  }

  if (spans.length === 0) return [{ text, bold: false }];

  spans.sort((a, b) => a.start - b.start || a.end - b.end);
  const merged: Span[] = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (!last || span.start > last.end) {
      merged.push({ ...span });
    } else if (span.end > last.end) {
      last.end = span.end;
    }
  }

  const segments: AnswerHighlightSegment[] = [];
  let cursor = 0;
  for (const span of merged) {
    if (cursor < span.start) {
      segments.push({ text: text.slice(cursor, span.start), bold: false });
    }
    let boldText = text.slice(span.start, span.end);
    if (boldText.startsWith('**') && boldText.endsWith('**')) {
      boldText = boldText.slice(2, -2);
    }
    if (boldText) segments.push({ text: boldText, bold: true });
    cursor = span.end;
  }
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), bold: false });
  }
  return segments.length > 0 ? segments : [{ text, bold: false }];
}
const DOI_URL_PREFIX_RE = /^https?:\/\/(?:dx\.)?doi\.org\//i;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function normalizeCitationName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/\s+snippet$/i, '')
    .replace(/\s*\([^)]*\)\s*$/g, '');
}

function normalizeNodeLookupName(value: string): string {
  return normalizeCitationName(value.replace(/^matkg:/i, '').replace(/[_-]+/g, ' '));
}

function normalizeCode(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase();
}

function normalizePublicationRef(value: string): string {
  return value.trim().toLowerCase().replace(DOI_URL_PREFIX_RE, '');
}

function publicationMatchKeys(publication: PublicationInfo): string[] {
  const keys = new Set<string>();
  const add = (value?: string | null) => {
    const normalized = normalizePublicationRef(value ?? '');
    if (normalized) keys.add(normalized);
  };

  add(publication.source_paper);
  add(publication.paper_title);
  add(publication.doi);

  const crossref = parseCrossrefDoi(publication);
  if (crossref) {
    add(crossref);
    add(crossref.replace('/', '_'));
    add(`${crossref.replace('/', '_')}.pdf`);
    const slashIndex = crossref.indexOf('/');
    if (slashIndex > 0) {
      add(`${crossref.slice(0, slashIndex)}${crossref.slice(slashIndex + 1)}.pdf`);
    }
  }

  const arxiv = parseArxivId(publication);
  if (arxiv) {
    add(arxiv);
    add(`arxiv:${arxiv}`);
    add(`${arxiv}.pdf`);
  }

  return [...keys];
}

function keysOverlap(a: string[], b: string[]): boolean {
  const setB = new Set(b);
  return a.some(key => setB.has(key));
}

function mentionMinLength(key: string): number {
  if (key.endsWith('.pdf') || key.includes('/') || key.startsWith('arxiv:')) return 4;
  return 16;
}

function findEarliestMention(answer: string, keys: string[]): number {
  const lower = answer.toLowerCase();
  let best = -1;
  for (const key of keys) {
    if (key.length < mentionMinLength(key)) continue;
    const index = lower.indexOf(key.toLowerCase());
    if (index >= 0 && (best < 0 || index < best)) best = index;
  }
  return best;
}

function collectPublicationResponseRefs(
  answer: string,
  responsePublications: PublicationInfo[],
): Array<{ index: number; keys: string[] }> {
  const refs: Array<{ index: number; keys: string[] }> = [];

  PDF_FILENAME_RE.lastIndex = 0;
  let pdfMatch: RegExpExecArray | null;
  while ((pdfMatch = PDF_FILENAME_RE.exec(answer)) !== null) {
    refs.push({
      index: pdfMatch.index,
      keys: publicationMatchKeys({ source_paper: pdfMatch[1] }),
    });
  }

  responsePublications.forEach((publication, offset) => {
    const keys = publicationMatchKeys(publication);
    if (keys.length === 0) return;
    const mention = findEarliestMention(answer, keys);
    refs.push({
      index: mention >= 0 ? mention : answer.length + offset,
      keys,
    });
  });

  return refs;
}

function nodePublicationKeys(node: LiveGraphNode): string[] {
  const keys = new Set<string>();
  for (const publication of node.publications ?? []) {
    for (const key of publicationMatchKeys(publication)) keys.add(key);
  }
  return [...keys];
}

function collectPublicationNodeRefs(
  answer: string,
  responsePublications: PublicationInfo[],
  nodes: LiveGraphNode[],
): Array<{ index: number; nodeId: string }> {
  const responseRefs = collectPublicationResponseRefs(answer, responsePublications);
  const refs: Array<{ index: number; nodeId: string }> = [];

  for (const node of nodes) {
    const nodeKeys = nodePublicationKeys(node);
    if (nodeKeys.length === 0) continue;

    let bestIndex = -1;
    let matched = false;

    for (const responseRef of responseRefs) {
      if (!keysOverlap(nodeKeys, responseRef.keys)) continue;
      matched = true;
      if (bestIndex < 0 || responseRef.index < bestIndex) bestIndex = responseRef.index;
    }

    const mentionIndex = findEarliestMention(answer, nodeKeys);
    if (mentionIndex >= 0) {
      matched = true;
      if (bestIndex < 0 || mentionIndex < bestIndex) bestIndex = mentionIndex;
    }

    if (matched) {
      refs.push({
        index: bestIndex >= 0 ? bestIndex : answer.length,
        nodeId: node.id,
      });
    }
  }

  return refs;
}

function isSnippetNode(node: LiveGraphNode): boolean {
  const type = node.type.toLowerCase();
  return type.includes('codesnippet') || type.includes('code')
    || Boolean(node.code_snippet || node.function_name);
}

function snippetNodes(nodes: LiveGraphNode[]): LiveGraphNode[] {
  return nodes.filter(isSnippetNode);
}

export type InlineCiteKind = 'kg' | 'pdf' | 'ops';

export interface InlineCite {
  kind: InlineCiteKind;
  body: string;
  graphId?: string;
  name: string;
}

export function parseInlineCite(text: string): InlineCite | null {
  const match = text.trim().match(/^\[(KG|PDF|OPS):\s*([^\]]+?)\s*\]$/i);
  if (!match) return null;
  const kind = match[1].toLowerCase() as InlineCiteKind;
  const body = match[2].trim();
  if (kind === 'kg') {
    const prefixed = body.match(GRAPH_ID_PREFIX_RE);
    if (prefixed) {
      return { kind, body, graphId: prefixed[1], name: prefixed[2].trim() };
    }
  }
  return { kind, body, name: body };
}

function nodeMatchesNormalizedName(node: LiveGraphNode, names: Set<string>): boolean {
  return names.has(normalizeCitationName(node.label))
    || names.has(normalizeNodeLookupName(node.label))
    || names.has(normalizeCitationName(node.id))
    || names.has(normalizeNodeLookupName(node.id))
    || Boolean(node.function_name && names.has(normalizeCitationName(node.function_name)));
}

/** Small candidate pool so citation parsing never walks a 10k-node dump. */
export function citationLookupPool(
  text: string,
  nodes: LiveGraphNode[],
  highlightedIds: string[] = [],
): LiveGraphNode[] {
  if (nodes.length === 0) return [];
  const wanted = new Set(highlightedIds.map(id => id.toLowerCase()));
  const names = new Set<string>();
  TAGGED_CITE_RE.lastIndex = 0;
  let tagged: RegExpExecArray | null;
  while ((tagged = TAGGED_CITE_RE.exec(text)) !== null) {
    const parsed = parseInlineCite(tagged[0]);
    if (!parsed) continue;
    names.add(normalizeCitationName(parsed.name));
    names.add(normalizeNodeLookupName(parsed.name));
    names.add(normalizeCitationName(parsed.body));
  }
  PDF_FILENAME_RE.lastIndex = 0;
  let pdf: RegExpExecArray | null;
  while ((pdf = PDF_FILENAME_RE.exec(text)) !== null) {
    names.add(normalizeCitationName(pdf[1]));
  }
  const hasFence = text.includes('```');
  if (wanted.size === 0 && names.size === 0 && !hasFence) {
    return nodes.length > 400 ? [] : nodes;
  }
  return nodes.filter(node => {
    if (wanted.has(node.id.toLowerCase())) return true;
    if (names.size > 0 && nodeMatchesNormalizedName(node, names)) return true;
    if (hasFence && isSnippetNode(node)) return true;
    return false;
  });
}

function resolveKgCitation(citation: string, nodes: LiveGraphNode[]): string | null {
  const parsed = parseInlineCite(`[KG: ${citation}]`);
  const name = parsed?.name || citation;
  const graphId = parsed?.graphId;
  const pool = graphId
    ? nodes.filter(node => (node.graph_id || '').toLowerCase() === graphId.toLowerCase())
    : nodes;
  const search = pool.length > 0 ? pool : nodes;
  const graph = { nodes: search, edges: [], source_path: '' };
  return resolveViewerNodeId(graph, name) || resolveViewerNodeId(graph, citation);
}

export function resolveInlineCiteNodeId(
  cite: InlineCite | string,
  nodes: LiveGraphNode[],
  publications: PublicationInfo[] = [],
): string | null {
  const parsed = typeof cite === 'string' ? parseInlineCite(cite) : cite;
  if (!parsed) {
    if (typeof cite === 'string' && /\.pdf$/i.test(cite.trim())) {
      return parseKgCitationNodeIds(cite, nodes, publications)[0] ?? null;
    }
    return null;
  }
  if (parsed.kind === 'kg') return resolveKgCitation(parsed.body, nodes);
  const filename = parsed.name.match(/\b[\w.-]+\.pdf\b/i)?.[0] || parsed.name;
  const pdfHits = collectPublicationNodeRefs(filename, publications, nodes);
  return pdfHits[0]?.nodeId ?? resolveKgCitation(parsed.name, nodes);
}

function resolveCodeBlockToNode(content: string, nodes: LiveGraphNode[]): string | null {
  const candidates = snippetNodes(nodes);
  if (!content.trim() || candidates.length === 0) return null;

  const defMatch = content.match(/^\s*def\s+([a-zA-Z_]\w*)\s*\(/m);
  if (defMatch) {
    const fn = defMatch[1];
    for (const node of candidates) {
      if (node.function_name === fn) return node.id;
      if (normalizeCitationName(node.label) === normalizeCitationName(fn)) return node.id;
    }
  }

  const normalizedBlock = normalizeCode(content);
  if (normalizedBlock.length >= 24) {
    for (const node of candidates) {
      if (!node.code_snippet) continue;
      const normalizedSnippet = normalizeCode(node.code_snippet);
      const probe = normalizedSnippet.slice(0, Math.min(120, normalizedSnippet.length));
      if (probe.length >= 24 && normalizedBlock.includes(probe)) return node.id;
      const blockProbe = normalizedBlock.slice(0, Math.min(120, normalizedBlock.length));
      if (blockProbe.length >= 24 && normalizedSnippet.includes(blockProbe)) return node.id;
    }
  }

  for (const node of candidates) {
    const fn = node.function_name?.trim();
    if (fn && content.includes(fn)) return node.id;
  }

  return null;
}

function collectSnippetDefRefs(answer: string, nodes: LiveGraphNode[]): Array<{ index: number; nodeId: string }> {
  const refs: Array<{ index: number; nodeId: string }> = [];
  for (const node of snippetNodes(nodes)) {
    const fn = node.function_name?.trim();
    if (!fn) continue;
    const re = new RegExp(`\\bdef\\s+${escapeRegExp(fn)}\\s*\\(`, 'g');
    let match: RegExpExecArray | null;
    while ((match = re.exec(answer)) !== null) {
      refs.push({ index: match.index, nodeId: node.id });
    }
  }
  return refs;
}

function collectOrderedNodeRefs(
  answer: string,
  nodes: LiveGraphNode[],
  responsePublications: PublicationInfo[] = [],
): string[] {
  const refs: Array<{ index: number; nodeId: string }> = [];

  TAGGED_CITE_RE.lastIndex = 0;
  let taggedMatch: RegExpExecArray | null;
  while ((taggedMatch = TAGGED_CITE_RE.exec(answer)) !== null) {
    const nodeId = resolveInlineCiteNodeId(taggedMatch[0], nodes, responsePublications);
    if (nodeId) refs.push({ index: taggedMatch.index, nodeId });
  }

  CODE_FENCE_RE.lastIndex = 0;
  let fenceMatch: RegExpExecArray | null;
  while ((fenceMatch = CODE_FENCE_RE.exec(answer)) !== null) {
    const content = fenceMatch[0]
      .replace(/^```[^\n]*\n?/, '')
      .replace(/\n?```$/, '');
    const nodeId = resolveCodeBlockToNode(content, nodes);
    if (nodeId) refs.push({ index: fenceMatch.index, nodeId });
  }

  refs.push(...collectSnippetDefRefs(answer, nodes));
  refs.push(...collectPublicationNodeRefs(answer, responsePublications, nodes));

  refs.sort((a, b) => a.index - b.index);

  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const ref of refs) {
    if (seen.has(ref.nodeId)) continue;
    seen.add(ref.nodeId);
    ordered.push(ref.nodeId);
  }
  return ordered;
}

/** Ordered unique node IDs from KG citations, code snippets, and publication/PDF references. */
export function parseKgCitationNodeIds(
  answer: string,
  nodes: LiveGraphNode[],
  responsePublications: PublicationInfo[] = [],
): string[] {
  if (!answer.trim() || nodes.length === 0) {
    if (responsePublications.length === 0 || nodes.length === 0) return [];
    return collectOrderedNodeRefs('', nodes, responsePublications);
  }
  return collectOrderedNodeRefs(answer, nodes, responsePublications);
}

export type CitationSourceKind = 'paper' | 'ops' | 'rag' | 'tiled' | 'kg';

export interface AnswerCitation {
  n: number;
  raw: string;
  aliases: string[];
  kind: InlineCiteKind | 'pdf';
  sourceKind: CitationSourceKind;
  nodeId: string | null;
  name: string;
  type: string;
  graphId: string;
  snippet: string;
  title?: string;
  authors?: string[];
  venue?: string;
  year?: number;
  page?: string;
  esaf?: string;
  proposal?: string;
  sample?: string;
  scan?: string;
}

export type AnswerRenderSegment =
  | { type: 'text'; text: string; bold?: boolean }
  | { type: 'cite'; citation: AnswerCitation };

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function lookupRecord(source: Record<string, unknown> | undefined, keys: string[]): string {
  if (!source) return '';
  const lower = Object.fromEntries(
    Object.entries(source).map(([key, value]) => [key.toLowerCase(), value]),
  );
  for (const key of keys) {
    const found = firstString(lower[key.toLowerCase()]);
    if (found) return found;
  }
  return '';
}

function nodeIdentityFields(node: LiveGraphNode | undefined): Pick<AnswerCitation, 'esaf' | 'proposal' | 'sample' | 'scan'> {
  if (!node) return {};
  const extra = (node.extra_fields || {}) as Record<string, unknown>;
  const props: Record<string, unknown> = {};
  for (const entry of node.properties || []) {
    if (!entry || typeof entry !== 'object') continue;
    const record = entry as Record<string, unknown>;
    const label = firstString(record.property, record.label, record.key, record.name).toLowerCase();
    if (label) props[label] = record.value ?? record.val;
  }
  const haystack = `${node.id} ${node.label} ${JSON.stringify(extra)}`;
  const pick = (keys: string[], pattern: RegExp) => (
    lookupRecord(extra, keys)
    || lookupRecord(props, keys)
    || (haystack.match(pattern)?.[1] ?? '')
  );
  return {
    esaf: pick(['esaf', 'esaf_number', 'esaf_id'], /\bESAF[:\s-]*([A-Za-z0-9-]+)/i),
    proposal: pick(['proposal', 'proposal_id', 'proposal_number', 'proposal_code'], /\bproposal[:\s-]*([A-Za-z0-9-]+)/i),
    sample: pick(['sample', 'sample_id', 'sample_name', 'sample_code'], /\bsample[:\s-]*([A-Za-z0-9-]+)/i),
    scan: pick(['scan', 'scan_id', 'scan_uid', 'run_uid', 'uid'], /\bscan[:\s-]*([A-Za-z0-9-]+)/i),
  };
}

function citationSourceKind(cite: InlineCite | { kind: 'pdf'; body: string; name: string; graphId?: string }, node?: LiveGraphNode): CitationSourceKind {
  const graphId = (cite.graphId || node?.graph_id || '').toLowerCase();
  if (cite.kind === 'ops' || graphId.startsWith('bl1101')) return 'ops';
  if (graphId === 'tiled' || graphId.startsWith('tiled') || cite.kind === 'kg' && /^tiled:/i.test(cite.body)) return 'tiled';
  if (cite.kind === 'pdf') return /\bp\.\s*\d+/i.test(cite.body) ? 'rag' : 'paper';
  if (graphId.startsWith('rsoxs') || graphId === 'xray_demo') return 'kg';
  return cite.kind === 'kg' ? 'kg' : 'paper';
}

function pageFromCiteBody(body: string): string {
  return body.match(/\bp\.\s*(\d+)\b/i)?.[1] || '';
}

function enrichCitation(
  raw: string,
  parsed: InlineCite | { kind: 'pdf'; body: string; name: string },
  nodes: LiveGraphNode[],
  publications: PublicationInfo[],
): Omit<AnswerCitation, 'n'> {
  const nodeId = resolveInlineCiteNodeId(
    parsed.kind === 'pdf' && !raw.startsWith('[') ? raw : (parsed.kind === 'pdf' ? `[PDF: ${parsed.body}]` : raw),
    nodes,
    publications,
  );
  const node = nodeId ? nodes.find(item => item.id === nodeId) : undefined;
  const pub = node?.publications?.[0]
    || publications.find(item => {
      const keys = [item.source_paper, item.paper_title, item.doi].filter(Boolean).map(value => String(value).toLowerCase());
      return keys.some(key => raw.toLowerCase().includes(key) || parsed.body.toLowerCase().includes(key));
    });
  const identity = nodeIdentityFields(node);
  const graphId = parsed.kind === 'kg' && 'graphId' in parsed
    ? (parsed.graphId || node?.graph_id || '')
    : (node?.graph_id || '');
  const name = node?.label || parsed.name.replace(/\s+p\.\s*\d+\s*$/i, '') || raw;
  let type = node?.type || (parsed.kind === 'pdf' ? 'Publication' : parsed.kind === 'ops' ? 'Ops' : 'Entity');
  if ((!identity.esaf) && ESAF_LABEL_RE.test(`${name} ${raw}`)) {
    identity.esaf = (`${name} ${raw}`.match(ESAF_LABEL_RE)?.[1] || '').replace('_', '-');
  }
  if ((type === 'Entity' || type === 'Unknown') && identity.esaf) type = 'ESAF';
  return {
    raw,
    kind: parsed.kind,
    sourceKind: citationSourceKind(parsed, node),
    nodeId,
    name,
    type,
    graphId,
    snippet: (node?.description || pub?.abstract_text || '').trim(),
    title: pub?.paper_title || undefined,
    authors: pub?.authors,
    venue: pub?.journal || undefined,
    year: pub?.publication_year,
    page: pageFromCiteBody(parsed.body) || (pub?.pages_range ? String(pub.pages_range) : undefined),
    ...identity,
  };
}

export function collectAnswerCitations(
  text: string,
  nodes: LiveGraphNode[],
  publications: PublicationInfo[] = [],
): AnswerCitation[] {
  const lookup = nodes.length > 400 ? citationLookupPool(text, nodes) : nodes;
  const found: Array<{ index: number; raw: string; parsed: InlineCite | { kind: 'pdf'; body: string; name: string } }> = [];

  const taggedRanges: Array<{ start: number; end: number }> = [];
  TAGGED_CITE_RE.lastIndex = 0;
  let tagged: RegExpExecArray | null;
  while ((tagged = TAGGED_CITE_RE.exec(text)) !== null) {
    const parsed = parseInlineCite(tagged[0]);
    if (!parsed) continue;
    taggedRanges.push({ start: tagged.index, end: tagged.index + tagged[0].length });
    found.push({ index: tagged.index, raw: tagged[0], parsed });
  }

  PDF_FILENAME_RE.lastIndex = 0;
  let pdf: RegExpExecArray | null;
  while ((pdf = PDF_FILENAME_RE.exec(text)) !== null) {
    const index = pdf.index;
    const overlaps = taggedRanges.some(range => index >= range.start && index < range.end);
    if (overlaps) continue;
    found.push({
      index,
      raw: pdf[1],
      parsed: { kind: 'pdf', body: pdf[1], name: pdf[1] },
    });
  }

  found.sort((a, b) => a.index - b.index);
  const citations: AnswerCitation[] = [];
  const identityToCitation = new Map<string, AnswerCitation>();

  for (const item of found) {
    const enriched = enrichCitation(item.raw, item.parsed, lookup, publications);
    const identity = enriched.nodeId || `${enriched.kind}:${enriched.name.toLowerCase()}:${enriched.graphId}`;
    const existing = identityToCitation.get(identity);
    if (existing) {
      if (item.raw !== existing.raw && !existing.aliases.includes(item.raw)) {
        existing.aliases.push(item.raw);
      }
      continue;
    }
    const citation: AnswerCitation = { ...enriched, n: citations.length + 1, aliases: [] };
    if (COOKBOOK_PAGE_RE.test(citation.name) && !citation.nodeId) continue;
    identityToCitation.set(identity, citation);
    citations.push(citation);
  }
  return citations;
}

export function citationBibliographyLabel(citation: AnswerCitation): string {
  if (citation.sourceKind === 'tiled' || (citation.graphId || '').toLowerCase() === 'tiled') {
    const esaf = citation.esaf || citation.name.match(ESAF_LABEL_RE)?.[1] || citation.raw.match(ESAF_LABEL_RE)?.[1];
    if (esaf) return `ESAF ${String(esaf).replace('_', '-')}`;
    if (citation.proposal) return `proposal ${citation.proposal}`;
    if (citation.sample) return `sample ${citation.sample}`;
    if (citation.scan) return `scan ${citation.scan}`;
    const type = citation.type && citation.type !== 'Entity' && citation.type !== 'Unknown'
      ? citation.type
      : '';
    if (COOKBOOK_PAGE_RE.test(citation.name)) return type || 'Tiled identity';
    return [citation.name, type].filter(Boolean).join(' · ');
  }
  if (citation.title) {
    return '';
  }
  const ids = [
    citation.esaf ? `ESAF ${citation.esaf}` : '',
    citation.proposal ? `proposal ${citation.proposal}` : '',
    citation.sample ? `sample ${citation.sample}` : '',
    citation.scan ? `scan ${citation.scan}` : '',
  ].filter(Boolean);
  if (ids.length) {
    return [citation.name, citation.graphId, ...ids].filter(Boolean).join(' · ');
  }
  return [citation.name, citation.type, citation.graphId || citation.sourceKind]
    .filter(Boolean)
    .join(' · ');
}

export function splitAnswerCitationSegments(
  text: string,
  citations: AnswerCitation[],
): AnswerRenderSegment[] {
  if (citations.length === 0) {
    return splitAnswerHighlightSegments(text).map(segment => ({
      type: 'text' as const,
      text: segment.text,
      bold: segment.bold,
    }));
  }

  const spans: Array<{ start: number; end: number; citation: AnswerCitation }> = [];
  for (const citation of citations) {
    const needles = [citation.raw, ...citation.aliases];
    for (const needle of needles) {
      if (!needle) continue;
      let from = 0;
      while (from < text.length) {
        const index = text.indexOf(needle, from);
        if (index < 0) break;
        spans.push({ start: index, end: index + needle.length, citation });
        from = index + Math.max(needle.length, 1);
      }
    }
  }
  spans.sort((a, b) => a.start - b.start || a.end - b.end);

  const merged: typeof spans = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last && span.start < last.end) continue;
    merged.push(span);
  }

  const segments: AnswerRenderSegment[] = [];
  let cursor = 0;
  for (const span of merged) {
    if (cursor < span.start) {
      for (const part of splitAnswerHighlightSegments(text.slice(cursor, span.start))) {
        if (part.text) segments.push({ type: 'text', text: part.text, bold: part.bold });
      }
    }
    segments.push({ type: 'cite', citation: span.citation });
    cursor = span.end;
  }
  if (cursor < text.length) {
    for (const part of splitAnswerHighlightSegments(text.slice(cursor))) {
      if (part.text) segments.push({ type: 'text', text: part.text, bold: part.bold });
    }
  }
  return segments.length > 0 ? segments : [{ type: 'text', text }];
}
