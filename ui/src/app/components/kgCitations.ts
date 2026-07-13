import type { LiveGraphNode, PublicationInfo } from './data/liveAgent';
import { parseArxivId, parseCrossrefDoi } from './publicationLinks';

const KG_CITATION_RE = /\[KG:\s*([^\]]+?)\s*\]/gi;
const MARKDOWN_BOLD_RE = /\*\*([^*]+)\*\*/g;
const CODE_FENCE_RE = /```[\s\S]*?```/g;
const PDF_FILENAME_RE = /\b([A-Za-z0-9][A-Za-z0-9._-]*\.pdf)\b/gi;

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

  KG_CITATION_RE.lastIndex = 0;
  while ((match = KG_CITATION_RE.exec(text)) !== null) {
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

function resolveKgCitation(citation: string, nodes: LiveGraphNode[]): string | null {
  const normalized = normalizeCitationName(citation);
  if (!normalized) return null;

  for (const node of nodes) {
    if (normalizeCitationName(node.label) === normalized) return node.id;
  }
  for (const node of nodes) {
    if (normalizeNodeLookupName(node.label) === normalized) return node.id;
  }
  for (const node of nodes) {
    if (node.function_name && normalizeCitationName(node.function_name) === normalized) return node.id;
  }
  for (const node of nodes) {
    if (normalizeCitationName(node.id) === normalized) return node.id;
  }
  for (const node of nodes) {
    if (normalizeNodeLookupName(node.id) === normalized) return node.id;
  }
  return null;
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

  KG_CITATION_RE.lastIndex = 0;
  let kgMatch: RegExpExecArray | null;
  while ((kgMatch = KG_CITATION_RE.exec(answer)) !== null) {
    const nodeId = resolveKgCitation(kgMatch[1], nodes);
    if (nodeId) refs.push({ index: kgMatch.index, nodeId });
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
