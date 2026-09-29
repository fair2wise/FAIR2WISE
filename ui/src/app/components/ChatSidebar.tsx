import { useEffect, useMemo, useRef, useState, type Dispatch, type KeyboardEvent, type SetStateAction } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUp, Check, Copy, Share2 } from 'lucide-react';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { CodeBlock } from './CodeBlock';
import { ExampleQuery } from './data/mockupData';
import { GraphMockup, inducedSubgraph, type GraphMockupHandle } from './GraphMockup';
import { CITE_FOCUS_HOPS, looksLikeTiledIdentityRef, resolveViewerNodeId } from './kgCatalog';
import { parseKgCitationNodeIds, collectAnswerCitations, citationBibliographyLabel, citationInlineLabel, citationLookupPool, materialsProjectUrl, splitAnswerCitationSegments, type AnswerCitation, type CitationSourceKind } from './kgCitations';
import { PublicationList } from './PublicationList';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from './ui/resizable';
import type { ChatMessage, TurnAnnotationState } from './chatSessions';
import { TurnAnnotationBar } from './TurnAnnotation';
import { currentTurnId, recordUiEvent, setActiveTurnId, traceUrl } from './uiTelemetry';
import {
  AgentChatHistoryMessage,
  AgentChatResponse,
  ChatProgressEvent,
  GraphPayload,
  PendingAction,
  PendingCandidate,
  PublicationInfo,
  ThinkingStep,
  queryAgentActionStream,
  queryLiveAgentStream,
} from './data/liveAgent';

function PaperDetails({ paper }: { paper: PendingCandidate }) {
  const meta = [
    paper.repository,
    paper.publication_year,
    paper.doi ? `DOI ${paper.doi}` : null,
    paper.source_paper,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      {paper.recommended && (
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-600">
          Recommended
        </p>
      )}
      {paper.unavailable && (
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-rose-600">
          Download unavailable
        </p>
      )}
      <p className="text-sm font-semibold text-slate-800">{paper.title}</p>
      {meta && <p className="mt-0.5 text-xs text-slate-500">{meta}</p>}
      {paper.abstract && (
        <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-500">
          {paper.abstract}
        </p>
      )}
    </>
  );
}

function DecisionCard({
  pending,
  disabled,
  onExtractionDecision,
}: {
  pending: PendingAction;
  disabled: boolean;
  onExtractionDecision: (decision: 'yes' | 'no') => void;
}) {
  if (pending.kind === 'download' && pending.papers && pending.papers.length > 0) {
    return (
      <div className="overflow-hidden rounded-xl border border-sky-200 bg-sky-50/70">
        <div className="border-b border-sky-200/80 px-4 py-3">
          <p className="text-sm font-semibold text-sky-900">Candidate Papers:</p>
        </div>
        {pending.papers.map((paper, idx) => {
          const paperIndex = paper.index ?? idx;
          return (
            <div key={paperIndex} className={idx > 0 ? 'border-t border-sky-200/80' : ''}>
              <div className="px-4 py-3.5">
                <PaperDetails paper={paper} />
              </div>
            </div>
          );
        })}
        <div className="border-t border-sky-200/80 bg-white/40 px-4 py-3">
          <p className="text-xs leading-relaxed text-slate-600">
            Ask in chat which paper to download by title, number, DOI, or repository.
            You can also say not to download any paper.
          </p>
        </div>
      </div>
    );
  }

  const candidate = pending.candidate;
  return (
    <div className="overflow-hidden rounded-xl border border-sky-200 bg-sky-50/70">
      {candidate && (
        <div className="px-4 py-3.5">
          <PaperDetails paper={candidate} />
        </div>
      )}
      <div className="flex gap-2 border-t border-sky-200/80 bg-white/40 px-4 py-3">
        <button
          type="button"
          disabled={disabled}
          onClick={() => onExtractionDecision('yes')}
          className="inline-flex items-center rounded-lg bg-sky-500 px-6 py-3 text-sm font-medium text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run extraction
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onExtractionDecision('no')}
          className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-6 py-3 text-sm font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Skip
        </button>
      </div>
    </div>
  );
}

function sourceKindLabel(kind: CitationSourceKind): string {
  if (kind === 'paper') return 'Paper';
  if (kind === 'ops') return 'Ops';
  if (kind === 'rag') return 'RAG';
  if (kind === 'tiled') return 'Tiled';
  if (kind === 'mp') return 'Materials Project';
  return 'KG';
}

function CitationChip({
  citation,
  onCitationClick,
  turnId,
}: {
  citation: AnswerCitation;
  onCitationClick?: (nodeId: string) => void;
  turnId?: string;
}) {
  const mpHref = (citation.sourceKind === 'mp' || citation.kind === 'mp')
    ? materialsProjectUrl(citation)
    : '';
  const clickable = Boolean(mpHref || (onCitationClick && (citation.nodeId || citation.name)));
  const [tip, setTip] = useState<{ x: number; y: number; below: boolean } | null>(null);

  function showTip(target: HTMLElement) {
    const rect = target.getBoundingClientRect();
    setTip({
      x: Math.min(Math.max(rect.left + rect.width / 2, 152), window.innerWidth - 152),
      y: rect.bottom + 8,
      below: true,
    });
  }

  return (
    <span className="relative mx-0.5 inline-block">
      <button
        type="button"
        aria-label={`Citation ${citation.n}: ${citation.name}`}
        className={`inline-flex h-5 min-w-5 translate-y-[-0.15em] items-center justify-center rounded-full bg-sky-100 px-1.5 align-super text-[10px] font-semibold text-sky-800 hover:bg-sky-200 ${
          clickable ? 'cursor-pointer' : 'cursor-default'
        }`}
        onPointerEnter={event => showTip(event.currentTarget)}
        onPointerLeave={() => setTip(null)}
        onFocus={event => showTip(event.currentTarget)}
        onBlur={() => setTip(null)}
        onClick={event => {
          event.preventDefault();
          event.stopPropagation();
          recordUiEvent('citation_click', {
            cite_type: citation.kind,
            source_kind: citation.sourceKind,
            target: citation.nodeId || citation.name || citation.raw,
          }, turnId || currentTurnId());
          if (mpHref) {
            window.open(mpHref, '_blank', 'noopener,noreferrer');
            return;
          }
          if (!onCitationClick) return;
          onCitationClick(citation.nodeId || citation.name);
        }}
        data-citation-chip={String(citation.n)}
        data-citation-node={citation.nodeId || citation.name}
      >
        {citation.n}
      </button>
      {tip && createPortal(
        <span
          role="tooltip"
          className={`pointer-events-none fixed z-50 w-72 -translate-x-1/2 rounded-md border border-slate-200 bg-white p-3 text-left text-xs shadow-lg ${
            tip.below ? '' : '-translate-y-full'
          }`}
          style={{ left: tip.x, top: tip.y }}
        >
          <span className="block font-semibold text-slate-800">{citation.name}</span>
          <span className="mt-1 block text-[11px] text-slate-500">
            {citation.type || 'Entity'}
            {' · '}
            {sourceKindLabel(citation.sourceKind)}
            {citation.graphId ? ` · ${citation.graphId}` : ''}
          </span>
          {citation.snippet && (
            <span className="mt-2 block max-h-28 overflow-y-auto leading-relaxed text-slate-600">
              {citation.snippet}
            </span>
          )}
        </span>,
        document.body,
      )}
    </span>
  );
}

function bibliographyLine(citation: AnswerCitation): string {
  if (citation.sourceKind === 'tiled' || (citation.graphId || '').toLowerCase() === 'tiled') {
    return citationBibliographyLabel(citation);
  }
  if (citation.title) {
    const authors = formatAuthors(citation.authors);
    const venue = [citation.venue, citation.year, citation.page ? `p.${citation.page}` : '']
      .filter(Boolean)
      .join(', ');
    return [citation.title, authors, venue].filter(Boolean).join(' · ');
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
  return [citation.name, citation.type, citation.graphId || sourceKindLabel(citation.sourceKind)]
    .filter(Boolean)
    .join(' · ');
}

function CitationBibliography({
  citations,
  onCitationClick,
  turnId,
}: {
  citations: AnswerCitation[];
  onCitationClick?: (nodeId: string) => void;
  turnId?: string;
}) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3 border-t border-slate-200 pt-3">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">References</p>
      <ol className="space-y-1.5">
        {citations.map(citation => (
          <li key={`${citation.n}:${citation.raw}`}>
            <button
              type="button"
              className={`flex w-full gap-2 rounded-md px-1 py-0.5 text-left text-xs leading-relaxed text-slate-600 ${
                citation.sourceKind === 'mp' || (onCitationClick && (citation.nodeId || citation.name))
                  ? 'hover:bg-sky-50'
                  : 'cursor-default'
              }`}
              onClick={event => {
                event.preventDefault();
                event.stopPropagation();
                recordUiEvent('citation_click', {
                  cite_type: citation.kind,
                  source_kind: citation.sourceKind,
                  target: citation.nodeId || citation.name || citation.raw,
                }, turnId || currentTurnId());
                const mpHref = (citation.sourceKind === 'mp' || citation.kind === 'mp')
                  ? materialsProjectUrl(citation)
                  : '';
                if (mpHref) {
                  window.open(mpHref, '_blank', 'noopener,noreferrer');
                  return;
                }
                if (!onCitationClick) return;
                onCitationClick(citation.nodeId || citation.name);
              }}
              data-citation-ref={String(citation.n)}
              data-citation-node={citation.nodeId || citation.name}
            >
              <span className="w-4 shrink-0 font-semibold text-sky-700">{citation.n}.</span>
              <span>
                <span className="mr-1 rounded bg-slate-100 px-1 py-px text-[10px] font-medium uppercase tracking-wide text-slate-500">
                  {sourceKindLabel(citation.sourceKind)}
                </span>
                {bibliographyLine(citation)}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function AnswerHighlightText({
  text,
  citations = [],
  onCitationClick,
  turnId,
}: {
  text: string;
  citations?: AnswerCitation[];
  onCitationClick?: (nodeId: string) => void;
  turnId?: string;
}) {
  const segments = splitAnswerCitationSegments(text, citations);
  let preceding = '';
  return (
    <>
      {segments.map((segment, i) => {
        if (segment.type === 'cite') {
          const label = citationInlineLabel(segment.citation, preceding);
          preceding += `${label} `;
          return (
            <span key={`cite-${i}-${segment.citation.n}`}>
              {label ? <span>{label}</span> : null}
              <CitationChip
                citation={segment.citation}
                onCitationClick={onCitationClick}
                turnId={turnId}
              />
            </span>
          );
        }
        preceding += segment.text;
        if (segment.bold) {
          return <strong key={i} className="font-semibold text-sky-900">{segment.text}</strong>;
        }
        return <span key={i}>{segment.text}</span>;
      })}
    </>
  );
}

function formatAuthors(authors?: string[]) {
  if (!authors || authors.length === 0) return '';
  if (authors.length <= 3) return authors.join(', ');
  return `${authors.slice(0, 3).join(', ')} +${authors.length - 3}`;
}

const PUBLICATIONS_INTRO = 'Here are a list of relevant publications:\n\n';
const ALTERNATIVE_PUBLICATIONS_INTRO =
  'These references from the knowledge graph are alternative sources that may help answer your question:\n\n';
const MORE_EVIDENCE_PUBLICATIONS_INTRO =
  'Relevant Publications and Sources — More Evidence Needed:\n\n';

const INSUFFICIENT_EVIDENCE_STATUSES = new Set([
  'insufficient_json_graph',
  'insufficient_evidence',
  'stop_insufficient',
  'max_rounds',
]);

function showsAlternativePublications(message: ChatMessage): boolean {
  if (message.status === 'insufficient_evidence') return false;
  if (INSUFFICIENT_EVIDENCE_STATUSES.has(message.status ?? '')) return true;
  return message.status === 'stopped_by_user'
    && /not enough direct evidence/i.test(message.content);
}

function showsMoreEvidencePublications(message: ChatMessage): boolean {
  return message.status === 'insufficient_evidence';
}

export function publicationSectionHeading(message: ChatMessage): string {
  if (showsMoreEvidencePublications(message)) {
    return 'Relevant Publications and Sources — More Evidence Needed:';
  }
  if (showsAlternativePublications(message)) {
    return 'Alternative Publications and Sources:';
  }
  return 'Relevant Publications and Sources:';
}

function isExtractionSkipped(message: ChatMessage): boolean {
  return message.status === 'stopped_by_user'
    && /will not run extraction/i.test(message.content);
}

const CITE_FOCUS_STORAGE_KEY = 'fair2wise.citeFocusNodeId';
const HIDE_KG_LINK_STATUSES = new Set([
  'awaiting_download_decision',
  'awaiting_extraction_decision',
  'no_new_papers',
]);

function readStoredCiteFocus(): string | null {
  try {
    return window.sessionStorage.getItem(CITE_FOCUS_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredCiteFocus(nodeId: string | null) {
  try {
    if (nodeId) window.sessionStorage.setItem(CITE_FOCUS_STORAGE_KEY, nodeId);
    else window.sessionStorage.removeItem(CITE_FOCUS_STORAGE_KEY);
  } catch {
    // sessionStorage may be unavailable
  }
}

function shouldShowKnowledgeGraph(message: ChatMessage): boolean {
  if ((message.highlightNodeIds?.length ?? 0) === 0) return false;
  if (message.pending) return false;
  if (message.status && HIDE_KG_LINK_STATUSES.has(message.status)) return false;
  return true;
}

export function publicationsBlockText(
  publications: PublicationInfo[],
  markdown = false,
  alternative = false,
  moreEvidenceNeeded = false,
): string {
  if (!publications.length) return '';
  const entries = publications.map(publication => {
    const title = publication.paper_title || publication.source_paper || 'Untitled publication';
    const titleLine = markdown ? `**${title}**` : title;
    const authors = formatAuthors(publication.authors);
    const meta = [authors, publication.publication_year, publication.journal].filter(Boolean).join(' · ');
    const source = publication.doi
      ? `DOI ${publication.doi}`
      : publication.source_paper;
    return [titleLine, meta, source].filter(Boolean).join('\n');
  });
  const intro = moreEvidenceNeeded
    ? MORE_EVIDENCE_PUBLICATIONS_INTRO
    : alternative
      ? ALTERNATIVE_PUBLICATIONS_INTRO
      : PUBLICATIONS_INTRO;
  return intro + entries.join('\n\n');
}

function assistantCopyText(message: ChatMessage): string {
  const parts = [message.content];
  if (message.publications?.length && !message.pending) {
    parts.push(publicationsBlockText(
      message.publications,
      false,
      showsAlternativePublications(message),
      showsMoreEvidencePublications(message),
    ));
  }
  return parts.join('\n\n');
}

type MessageSegment = { type: 'code' | 'text'; content: string };

function parseMessageSegments(text: string): MessageSegment[] {
  const segments: MessageSegment[] = [];
  const fence = /```([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = fence.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }
    segments.push({ type: 'code', content: match[1].replace(/^[^\n]*\n?/, '') });
    lastIndex = fence.lastIndex;
  }
  const rest = text.slice(lastIndex);
  // An unterminated fence appears mid-stream; render it in the code box too.
  const openFence = rest.indexOf('```');
  if (openFence !== -1) {
    if (openFence > 0) segments.push({ type: 'text', content: rest.slice(0, openFence) });
    segments.push({ type: 'code', content: rest.slice(openFence + 3).replace(/^[^\n]*\n?/, '') });
  } else if (rest) {
    segments.push({ type: 'text', content: rest });
  }
  return segments;
}

function MessageText({
  text,
  cursor = false,
  nodes = [],
  publications = [],
  onCitationClick,
  turnId,
}: {
  text: string;
  cursor?: boolean;
  nodes?: GraphPayload['nodes'];
  publications?: PublicationInfo[];
  onCitationClick?: (nodeId: string) => void;
  turnId?: string;
}) {
  const lookup = citationLookupPool(text, nodes);
  const citations = collectAnswerCitations(text, lookup, publications);
  const segments = parseMessageSegments(text);
  const lastIndex = segments.length - 1;
  return (
    <div className="space-y-4">
      {segments.map((segment, i) => {
        const isLast = i === lastIndex;
        if (segment.type === 'code') {
          return (
            <CodeBlock
              key={i}
              content={segment.content}
              cursor={cursor && isLast}
            />
          );
        }
        const paras = segment.content.split(/\n{2,}/).filter(Boolean);
        return paras.map((para, j) => {
          const cursorHere = cursor && isLast && j === paras.length - 1;
          return (
            <p key={`${i}-${j}`} className="whitespace-pre-wrap">
              <AnswerHighlightText
                text={para}
                citations={citations}
                onCitationClick={onCitationClick}
                turnId={turnId}
              />
              {cursorHere && <span className="ml-px animate-pulse text-slate-400">▌</span>}
            </p>
          );
        });
      })}
      <CitationBibliography citations={citations} onCitationClick={onCitationClick} turnId={turnId} />
    </div>
  );
}

function progressEventToStep(event: ChatProgressEvent): ThinkingStep {
  return {
    id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    phase: event.phase,
    label: event.message,
    detail: event.phase === 'orchestrator_decision'
      ? [event.action, event.agent].filter(Boolean).join(' → ')
      : undefined,
    round: event.round,
    state: 'active',
  };
}

function ThinkingStatus({ status, elapsedSeconds }: { status: string; elapsedSeconds: number }) {
  return (
    <div className="mt-10 flex min-w-0 items-center gap-2 text-sm text-slate-500">
      <span className="flex h-6 w-6 shrink-0 items-center justify-center overflow-visible">
        <AsciiOrb size={16} className="text-sky-400" interactive={false} />
      </span>
      <span className="min-w-0 flex-1 truncate leading-relaxed">{status}</span>
      <span className="shrink-0 tabular-nums">{elapsedSeconds}s</span>
    </div>
  );
}

interface Props {
  graph: GraphPayload;
  activeQuery: ExampleQuery;
  sessionId: string;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  onGraphUpdate: (graph: GraphPayload) => void;
  onSelect: (q: ExampleQuery) => void;
  onLoadCatalog?: () => void;
}
const MAX_REQUEST_HISTORY_MESSAGES = 8;

function requestHistoryFromMessages(messages: ChatMessage[]): AgentChatHistoryMessage[] {
  return messages
    .filter(message => (
      (message.role === 'user' || message.role === 'assistant')
      && message.content.trim().length > 0
    ))
    .slice(-MAX_REQUEST_HISTORY_MESSAGES)
    .map(message => ({
      role: message.role,
      content: message.content,
    }));
}


interface MessageExchange {
  key: string;
  user: ChatMessage | null;
  assistants: ChatMessage[];
}

function groupMessageExchanges(messages: ChatMessage[]): MessageExchange[] {
  const exchanges: MessageExchange[] = [];
  let current: MessageExchange | null = null;

  for (const message of messages) {
    if (message.role === 'user') {
      if (current) exchanges.push(current);
      current = { key: message.id, user: message, assistants: [] };
      continue;
    }
    if (!current) {
      exchanges.push({ key: message.id, user: null, assistants: [message] });
      continue;
    }
    current.assistants.push(message);
  }

  if (current) exchanges.push(current);
  return exchanges;
}

export function queryGraphFromResult(result: AgentChatResponse): GraphPayload | null {
  const ids = result.node_ids ?? [];
  const payload = result.graph;
  if (!payload || ids.length === 0) return null;
  if (payload.nodes.length === 0) return null;
  const present = new Set(payload.nodes.map(node => node.id));
  const matched = ids.filter(id => present.has(id));
  const tiledIds = new Set(
    payload.nodes
      .filter(node => (node.graph_id || '').toLowerCase().startsWith('tiled'))
      .map(node => node.id),
  );
  if (tiledIds.size > 0) {
    const wanted = new Set(matched.length ? matched : ids);
    for (const id of tiledIds) wanted.add(id);
    for (const edge of payload.edges) {
      if (tiledIds.has(edge.source) || tiledIds.has(edge.target)) {
        wanted.add(edge.source);
        wanted.add(edge.target);
      }
    }
    return {
      nodes: payload.nodes.filter(node => wanted.has(node.id)),
      edges: payload.edges.filter(edge => wanted.has(edge.source) && wanted.has(edge.target)),
      source_path: payload.source_path,
    };
  }
  if (matched.length === 0) return inducedSubgraph(payload, ids);
  // Already the retrieve neighborhood (or a close subset) — keep dual-KG tags.
  if (payload.nodes.length <= Math.max(matched.length, ids.length)) return payload;
  return inducedSubgraph(payload, ids);
}

export function raiseViewerLimit(
  previous: number | 'all',
  queriedCount: number,
): number | 'all' {
  if (previous === 'all' || queriedCount <= 0) return previous;
  return previous < queriedCount ? 'all' : previous;
}

export function ChatSidebar({ graph, activeQuery, sessionId, messages, setMessages, onGraphUpdate, onSelect, onLoadCatalog }: Props) {
  const [inputValue, setInputValue] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const progressLogRef = useRef<ChatProgressEvent[]>([]);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [streamedLen, setStreamedLen] = useState(0);
  const [streamGraph, setStreamGraph] = useState<GraphPayload | null>(null);
  const [streamNodeIds, setStreamNodeIds] = useState<string[]>([]);
  const [queryGraph, setQueryGraph] = useState<GraphPayload | null>(null);
  const [pinnedViewId, setPinnedViewId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [isKgViewer, setIsKgViewer] = useState(false);
  const [kgViewerNodeLimit, setKgViewerNodeLimit] = useState<number | 'all'>('all');
  const graphRef = useRef<GraphMockupHandle>(null);
  const [citeFocusNodeId, setCiteFocusNodeId] = useState<string | null>(() => readStoredCiteFocus());
  const prevSessionIdRef = useRef(sessionId);
  const endRef = useRef<HTMLDivElement>(null);
  const activeRequestRef = useRef<AbortController | null>(null);
  const requestSeqRef = useRef(0);
  const requestStartedAtRef = useRef<number | null>(null);
  const stepsRef = useRef<ThinkingStep[]>([]);

  const canStop = isThinking || streamingId !== null;
  const isBusy = canStop;
  const canSubmit = inputValue.trim().length > 0 && !isBusy;
  const thinkingStatus = steps.length > 0
    ? steps[steps.length - 1].label
    : 'Thinking';

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isThinking, steps, streamedLen]);

  useEffect(() => {
    if (!streamingId) return;
    const target = messages.find(message => message.id === streamingId);
    if (!target) {
      setStreamingId(null);
      return;
    }

    const answerLen = target.content.length;

    if (streamedLen < answerLen) {
      const charsPerTick = Math.max(2, Math.ceil(answerLen / 320));
      const interval = window.setInterval(() => {
        setStreamedLen(prev => Math.min(answerLen, prev + charsPerTick));
      }, 16);
      return () => window.clearInterval(interval);
    }

    setStreamingId(null);
  }, [streamingId, streamedLen, messages]);

  useEffect(() => {
    if (!isThinking || requestStartedAtRef.current === null) return;

    const updateElapsed = () => {
      const startedAt = requestStartedAtRef.current;
      if (startedAt === null) return;
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    };

    updateElapsed();
    const interval = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(interval);
  }, [isThinking]);

  useEffect(() => {
    const switched = prevSessionIdRef.current !== sessionId;
    prevSessionIdRef.current = sessionId;
    stopGeneration();
    setInputValue('');
    setCopiedMessageId(null);
    setPinnedViewId(null);
    if (switched) {
      setCiteFocusNodeId(null);
      writeStoredCiteFocus(null);
    }
    const lastAssistant = [...messages].reverse().find(message => message.role === 'assistant');
    if (lastAssistant?.retrievedGraph?.nodes.length) {
      setQueryGraph(lastAssistant.retrievedGraph);
    } else if (switched) {
      setQueryGraph(null);
    }
    if (lastAssistant) {
      if ((lastAssistant.highlightNodeIds?.length ?? 0) > 0) {
        setPinnedViewId(lastAssistant.id);
      }
      applyAssistantSelection(lastAssistant);
    } else {
      onSelect({ id: 'idle', question: '', answer: '', nodeIds: [], confidence: 0 });
    }
  }, [sessionId]);

  useEffect(() => {
    if (!citeFocusNodeId) return;
    const resolved = (queryGraph ? resolveViewerNodeId(queryGraph, citeFocusNodeId) : null)
      || (streamGraph ? resolveViewerNodeId(streamGraph, citeFocusNodeId) : null)
      || (looksLikeTiledIdentityRef(citeFocusNodeId) ? null : resolveViewerNodeId(graph, citeFocusNodeId));
    if (resolved && resolved !== citeFocusNodeId) {
      setCiteFocusNodeId(resolved);
      writeStoredCiteFocus(resolved);
    }
  }, [citeFocusNodeId, graph, queryGraph, streamGraph]);

  useEffect(() => {
    return () => {
      activeRequestRef.current?.abort();
      activeRequestRef.current = null;
    };
  }, []);

  function stopGeneration() {
    activeRequestRef.current?.abort();
    activeRequestRef.current = null;
    requestStartedAtRef.current = null;
    requestSeqRef.current += 1;
    stepsRef.current = [];
    setSteps([]);
    setElapsedSeconds(0);
    setIsThinking(false);
    setStreamGraph(null);
    setStreamNodeIds([]);

    if (streamingId) {
      setMessages(prev => prev.map(message => {
        if (message.id !== streamingId) return message;
        return {
          ...message,
          content: message.content.slice(0, streamedLen),
        };
      }));
    }

    setStreamingId(null);
  }

  function makeProgressHandler(requestId: number, controller: AbortController) {
    return (event: ChatProgressEvent) => {
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      progressLogRef.current = [...progressLogRef.current, event];
      if (event.turn_id) setActiveTurnId(event.turn_id);
      if (event.phase === 'turn_started') return;
      if (event.phase === 'graph_update' && event.graph) {
        const incoming = event.graph;
        setStreamGraph(prev => {
          const base = prev ?? { nodes: [], edges: [], source_path: 'live-stream' };
          const nodeById = new Map(base.nodes.map(node => [node.id, node]));
          for (const node of incoming.nodes) nodeById.set(node.id, node);
          const edgeKey = (e: { source: string; target: string; predicate: string }) =>
            `${e.source}__${e.predicate}__${e.target}`;
          const edgeByKey = new Map(base.edges.map(edge => [edgeKey(edge), edge]));
          for (const edge of incoming.edges) edgeByKey.set(edgeKey(edge), edge);
          return {
            nodes: Array.from(nodeById.values()),
            edges: Array.from(edgeByKey.values()),
            source_path: base.source_path,
          };
        });
        setStreamNodeIds(prev => {
          const ids = new Set(prev);
          for (const id of event.node_ids ?? incoming.nodes.map(node => node.id)) ids.add(id);
          return Array.from(ids);
        });
        return;
      }
      const nextStep = progressEventToStep(event);
      const settled = stepsRef.current.map(step => ({ ...step, state: 'done' as const }));
      stepsRef.current = [...settled, nextStep];
      setSteps(stepsRef.current);
    };
  }

  function applyResult(result: AgentChatResponse, question: string) {
    const elapsed = requestStartedAtRef.current === null
      ? undefined
      : Math.max(1, Math.round((Date.now() - requestStartedAtRef.current) / 1000));
    const highlightNodeIds = result.node_ids ?? [];
    const nextQueryGraph = queryGraphFromResult(result);
    if (nextQueryGraph) {
      setQueryGraph(nextQueryGraph);
      setKgViewerNodeLimit(previous => raiseViewerLimit(previous, nextQueryGraph.nodes.length));
    }
    const assistantMessage: ChatMessage = {
      id: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      role: 'assistant',
      content: result.answer ?? '',
      question,
      highlightNodeIds,
      retrievedNodeIds: result.node_ids ?? [],
      confidence: result.confidence,
      status: result.status,
      graphSourceUsed: result.graph_source_used,
      elapsedSeconds: elapsed,
      publications: result.publications ?? [],
      pending: result.pending ?? null,
      retrievedGraph: nextQueryGraph || undefined,
      turnId: result.turn_id || progressLogRef.current.find(event => event.turn_id)?.turn_id,
      progress: progressLogRef.current.slice(),
    };
    if (assistantMessage.turnId) setActiveTurnId(assistantMessage.turnId);
    setStreamedLen(0);
    if (assistantMessage.content && !assistantMessage.pending) {
      setStreamingId(assistantMessage.id);
    } else {
      setStreamingId(null);
    }
    setMessages(prev => [...prev, assistantMessage]);
    if (highlightNodeIds.length > 0) {
      setPinnedViewId(assistantMessage.id);
    }
    onSelect({
      id: assistantMessage.id,
      question,
      answer: assistantMessage.content,
      nodeIds: highlightNodeIds,
      confidence: result.confidence,
    });
  }

  async function submit(raw: string) {
    const question = raw.trim();
    if (!question || isThinking) return;
    const requestHistory = requestHistoryFromMessages(messages);

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: question,
    };
    setMessages(prev => [...prev, userMessage]);
    setInputValue('');
    setIsThinking(true);
    setElapsedSeconds(0);
    stepsRef.current = [];
    progressLogRef.current = [];
    setSteps([]);
    setStreamGraph(null);
    setStreamNodeIds([]);
    setQueryGraph(null);
    setPinnedViewId(null);
    setCiteFocusNodeId(null);
    writeStoredCiteFocus(null);
    onSelect({ id: 'idle', question: '', answer: '', nodeIds: [], confidence: 0 });
    const controller = new AbortController();
    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    activeRequestRef.current = controller;
    requestStartedAtRef.current = Date.now();

    try {
      const result = await queryLiveAgentStream(
        question,
        makeProgressHandler(requestId, controller),
        controller.signal,
        requestHistory,
        sessionId,
      );
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      applyResult(result, question);
    } catch (error) {
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : String(error);
      const elapsed = requestStartedAtRef.current === null
        ? undefined
        : Math.max(1, Math.round((Date.now() - requestStartedAtRef.current) / 1000));
      setMessages(prev => [
        ...prev,
        {
          id: `agent-error-${Date.now()}`,
          role: 'assistant',
          content: `Agent run failed: ${message}`,
          question,
          highlightNodeIds: [],
          retrievedNodeIds: [],
          confidence: 0,
          status: 'api_error',
          elapsedSeconds: elapsed,
        },
      ]);
    } finally {
      if (requestSeqRef.current === requestId) {
        activeRequestRef.current = null;
        requestStartedAtRef.current = null;
        stepsRef.current = [];
        setIsThinking(false);
        setSteps([]);
        setElapsedSeconds(0);
        // Hand the KG back to the final graph + selection.
        setStreamGraph(null);
        setStreamNodeIds([]);
      }
    }
  }

  async function runExtractionDecision(decision: 'yes' | 'no', sourceMessageId: string) {
    if (isBusy) return;
    const source = messages.find(message => message.id === sourceMessageId);
    recordUiEvent(decision === 'yes' ? 'download_card_accept' : 'download_card_reject', {
      kind: source?.pending?.kind || 'extraction',
    }, source?.turnId);
    setMessages(prev => prev.map(message => (
      message.id === sourceMessageId ? { ...message, pending: null } : message
    )));
    const echo: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: decision === 'yes' ? 'Run extraction' : 'Skip',
    };
    setMessages(prev => [...prev, echo]);
    setIsThinking(true);
    setElapsedSeconds(0);
    stepsRef.current = [];
    progressLogRef.current = [];
    setSteps([]);
    setStreamGraph(null);
    setStreamNodeIds([]);
    setQueryGraph(null);
    setPinnedViewId(null);
    const controller = new AbortController();
    const requestId = requestSeqRef.current + 1;
    requestSeqRef.current = requestId;
    activeRequestRef.current = controller;
    requestStartedAtRef.current = Date.now();

    try {
      const result = await queryAgentActionStream(
        decision,
        'extraction',
        makeProgressHandler(requestId, controller),
        controller.signal,
        undefined,
        sessionId,
        source?.turnId,
      );
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      applyResult(result, echo.content);
    } catch (error) {
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      const message = error instanceof Error ? error.message : String(error);
      const elapsed = requestStartedAtRef.current === null
        ? undefined
        : Math.max(1, Math.round((Date.now() - requestStartedAtRef.current) / 1000));
      setMessages(prev => [
        ...prev,
        {
          id: `agent-error-${Date.now()}`,
          role: 'assistant',
          content: `Agent run failed: ${message}`,
          highlightNodeIds: [],
          retrievedNodeIds: [],
          confidence: 0,
          status: 'api_error',
          elapsedSeconds: elapsed,
        },
      ]);
    } finally {
      if (requestSeqRef.current === requestId) {
        activeRequestRef.current = null;
        requestStartedAtRef.current = null;
        stepsRef.current = [];
        setIsThinking(false);
        setSteps([]);
        setElapsedSeconds(0);
        setStreamGraph(null);
        setStreamNodeIds([]);
      }
    }
  }

  function copyAssistantMessage(message: ChatMessage) {
    recordUiEvent('copy_answer', {}, message.turnId);
    const text = assistantCopyText(message);
    navigator.clipboard.writeText(text).then(() => {
      setCopiedMessageId(message.id);
      window.setTimeout(() => {
        setCopiedMessageId(prev => (prev === message.id ? null : prev));
      }, 2000);
    }).catch(() => {
      // Clipboard unavailable — fail silently.
    });
  }

  async function copyTraceLink(message: ChatMessage) {
    if (!message.turnId) return;
    recordUiEvent('copy_trace_link', { turn_id: message.turnId }, message.turnId);
    try {
      await navigator.clipboard.writeText(traceUrl(message.turnId));
      setCopiedMessageId(`trace-${message.id}`);
      window.setTimeout(() => {
        setCopiedMessageId(prev => (prev === `trace-${message.id}` ? null : prev));
      }, 2000);
    } catch {
      // Clipboard unavailable.
    }
  }

  function regenerateAnswer(message: ChatMessage) {
    const question = message.question?.trim();
    if (!question || isBusy) return;
    recordUiEvent('regenerate', {}, message.turnId);
    submit(question);
  }

  function saveAnnotation(messageId: string, annotation: TurnAnnotationState) {
    setMessages(prev => prev.map(message => (
      message.id === messageId ? { ...message, annotation } : message
    )));
  }

  function applyAssistantSelection(message: ChatMessage) {
    if (message.role !== 'assistant') return;
    onSelect({
      id: message.id,
      question: message.question ?? '',
      answer: message.content,
      nodeIds: message.highlightNodeIds ?? [],
      confidence: message.confidence ?? 0,
    });
  }

  function viewKnowledgeGraph(message: ChatMessage) {
    if (message.role !== 'assistant') return;
    if ((message.highlightNodeIds?.length ?? 0) === 0) return;

    if (pinnedViewId === message.id) {
      setPinnedViewId(null);
      setCiteFocusNodeId(null);
      writeStoredCiteFocus(null);
      onSelect({ id: 'idle', question: '', answer: '', nodeIds: [], confidence: 0 });
      return;
    }

    setPinnedViewId(message.id);
    applyAssistantSelection(message);
  }

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (isBusy) return;
    submit(inputValue);
  }

  function onActionClick() {
    if (canStop) {
      stopGeneration();
      return;
    }
    submit(inputValue);
  }

  const messageExchanges = groupMessageExchanges(messages);
  const displayGraph = useMemo(() => {
    if (streamGraph) return streamGraph;
    const queryIds = isThinking && streamNodeIds.length ? streamNodeIds : activeQuery.nodeIds;
    if (queryIds.length > 0 && queryGraph) return queryGraph;
    return graph;
  }, [activeQuery.nodeIds, graph, isThinking, queryGraph, streamGraph, streamNodeIds]);
  const citationMessageId = activeQuery.id !== 'idle' ? activeQuery.id : null;
  const citationAnswerText = useMemo(() => {
    if (!citationMessageId) return '';

    const message = messages.find(entry => entry.id === citationMessageId);
    if (!message || message.role !== 'assistant') return '';

    if (streamingId === citationMessageId) {
      return message.content.slice(0, streamedLen);
    }
    return message.content;
  }, [citationMessageId, messages, streamingId, streamedLen]);
  const citationPublications = useMemo(() => {
    if (!citationMessageId) return [];

    const message = messages.find(entry => entry.id === citationMessageId);
    if (!message || message.role !== 'assistant') return [];

    return message.publications ?? [];
  }, [citationMessageId, messages]);
  const citationLookupSource = useMemo(() => {
    const merged: typeof graph.nodes = [];
    const seen = new Set<string>();
    for (const extra of [streamGraph, queryGraph, (streamGraph || queryGraph) ? null : { nodes: graph.nodes }]) {
      if (!extra) continue;
      for (const node of extra.nodes) {
        if (seen.has(node.id)) continue;
        seen.add(node.id);
        merged.push(node);
      }
    }
    return merged;
  }, [graph.nodes, queryGraph, streamGraph]);
  const citationLookupNodes = useMemo(() => {
    const highlightedIds = isThinking && streamNodeIds.length ? streamNodeIds : activeQuery.nodeIds;
    return citationLookupPool(
      citationAnswerText,
      citationLookupSource,
      highlightedIds,
    );
  }, [activeQuery.nodeIds, citationAnswerText, citationLookupSource, isThinking, streamNodeIds]);
  const citedNodeIds = useMemo(() => {
    if (!citationMessageId) return [];
    return parseKgCitationNodeIds(citationAnswerText, citationLookupNodes, citationPublications);
  }, [
    citationMessageId,
    citationAnswerText,
    citationPublications,
    citationLookupNodes,
  ]);
  const citationAnimationKey = citationMessageId && citedNodeIds.length > 0 ? citationMessageId : '';

  function focusCitedNode(nodeId: string) {
    const trimmed = nodeId.trim();
    if (!trimmed) return;
    const resolved = (queryGraph ? resolveViewerNodeId(queryGraph, trimmed) : null)
      || (streamGraph ? resolveViewerNodeId(streamGraph, trimmed) : null)
      || (looksLikeTiledIdentityRef(trimmed) ? trimmed : resolveViewerNodeId(graph, trimmed))
      || trimmed;
    setCiteFocusNodeId(resolved);
    writeStoredCiteFocus(resolved);
    setIsKgViewer(false);

    window.requestAnimationFrame(() => {
      graphRef.current?.focusNode(resolved);
    });
  }

  function handleGraphNodeUpdated(updated: GraphPayload['nodes'][number], refreshedGraph?: GraphPayload) {
    if (refreshedGraph) {
      onGraphUpdate(refreshedGraph);
      return;
    }
    onGraphUpdate({
      ...graph,
      nodes: graph.nodes.map(node => (
        node.id === updated.id
          ? {
              ...node,
              ...updated,
            }
          : node
      )),
    });
  }

  const graphView = (
    <GraphMockup
      ref={graphRef}
      graph={isKgViewer ? graph : { nodes: [], edges: [], source_path: graph.source_path }}
      retrievedGraph={streamGraph ?? queryGraph}
      citeFocusNodeId={citeFocusNodeId}
      citeFocusHops={CITE_FOCUS_HOPS}
      highlightedNodeIds={isThinking && streamNodeIds.length ? streamNodeIds : activeQuery.nodeIds}
      citedNodeIds={citedNodeIds}
      citationAnimationKey={citationAnimationKey}
      isKgViewer={isKgViewer}
      kgViewerNodeLimit={kgViewerNodeLimit}
      onToggleKgViewer={() => {
        setIsKgViewer(value => {
          const next = !value;
          if (next && graph.nodes.length === 0) onLoadCatalog?.();
          return next;
        });
      }}
      onKgViewerNodeLimitChange={setKgViewerNodeLimit}
      onNodeUpdated={handleGraphNodeUpdated}
      onNodeClick={nodeId => recordUiEvent('graph_node_click', { node_id: nodeId }, currentTurnId())}
    />
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-white">
      {/* Chat (left) + KG (right) with a draggable divider */}
      <div className="min-h-0 flex-1">
        {isKgViewer ? (
          <div className="flex h-full min-h-0 w-full">
            {graphView}
          </div>
        ) : (
          <ResizablePanelGroup direction="horizontal">
          <ResizablePanel defaultSize={36} minSize={24}>
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex-1 overflow-y-auto px-4 py-4 min-h-0">
                {messageExchanges.map((exchange, exchangeIndex) => (
                  <div
                    key={exchange.key}
                    className={exchangeIndex === 0 ? '' : 'mt-14'}
                  >
                    <div className="space-y-6">
                        {exchange.user && (
                          <div className="flex justify-end">
                            <div className="max-w-[85%] rounded-xl px-3.5 py-2.5 bg-sky-500 text-white text-sm leading-relaxed">
                              {exchange.user.content}
                            </div>
                          </div>
                        )}

                        {exchange.assistants.map(message => {
                          const streaming = streamingId === message.id;
                          const displayText = streaming ? message.content.slice(0, streamedLen) : message.content;
                          const showAnswerCursor = streaming && streamedLen < message.content.length;
                          const publications = message.publications ?? [];
                          const alternativePublications = showsAlternativePublications(message);
                          const showPublications = publications.length > 0
                            && !message.pending
                            && !isExtractionSkipped(message)
                            && (!streaming || streamedLen >= message.content.length);
                          const isPinned = pinnedViewId === message.id;
                          const showKnowledgeGraph = shouldShowKnowledgeGraph(message);
                          const isError = message.status === 'api_error';
                          const showMessageBody = Boolean(
                            message.content
                            || (showPublications && !message.pending),
                          );
                          return (
                            <div key={message.id} className="w-full">
                              <div className="min-w-0 flex-1">
                                {isError ? (
                                  <AppErrorMessage title="Agent run failed">
                                    {message.content.replace(/^Agent run failed:\s*/i, '') || message.content}
                                  </AppErrorMessage>
                                ) : message.pending ? (
                                  <div className="space-y-3">
                                    {message.content && (
                                      <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
                                        <div className="px-4 py-3.5 text-sm leading-relaxed text-slate-700">
                                          <MessageText
                                            text={message.content}
                                            nodes={citationLookupSource}
                                            publications={message.publications ?? []}
                                            onCitationClick={focusCitedNode}
                                            turnId={message.turnId}
                                          />
                                        </div>
                                      </div>
                                    )}
                                    <DecisionCard
                                      pending={message.pending}
                                      disabled={isBusy}
                                      onExtractionDecision={decision =>
                                        runExtractionDecision(decision, message.id)
                                      }
                                    />
                                  </div>
                                ) : showMessageBody ? (
                                <div>
                                  <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
                                    <div className="px-4 py-3.5 text-sm leading-relaxed text-slate-700">
                                      <MessageText
                                        text={displayText}
                                        cursor={showAnswerCursor}
                                        nodes={citationLookupSource}
                                        publications={publications}
                                        onCitationClick={focusCitedNode}
                                        turnId={message.turnId}
                                      />
                                      {!streaming && (
                                        <div className="mt-2 flex justify-start">
                                          <button
                                            type="button"
                                            aria-label={copiedMessageId === message.id ? 'Copied' : 'Copy answer'}
                                            title={copiedMessageId === message.id ? 'Copied' : 'Copy'}
                                            onClick={() => copyAssistantMessage(message)}
                                            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                                          >
                                            {copiedMessageId === message.id ? (
                                              <Check size={15} className="text-emerald-500" />
                                            ) : (
                                              <Copy size={15} />
                                            )}
                                          </button>
                                          <button
                                            type="button"
                                            disabled={!message.turnId}
                                            onClick={() => void copyTraceLink(message)}
                                            className="rounded-lg px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                                          >
                                            {copiedMessageId === `trace-${message.id}` ? 'Trace link copied' : 'Copy trace link'}
                                          </button>
                                          <button
                                            type="button"
                                            disabled={!message.question || isBusy}
                                            onClick={() => regenerateAnswer(message)}
                                            className="rounded-lg px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                                          >
                                            Regenerate
                                          </button>
                                        </div>
                                      )}
                                    </div>
                                    {showPublications && (
                                      <div className="px-4 py-3">
                                        <p className="mb-2 text-sm font-bold text-slate-800">
                                          {publicationSectionHeading(message)}
                                        </p>
                                        {alternativePublications && (
                                          <p className="mb-4 text-xs leading-relaxed text-slate-600">
                                            These references from the knowledge graph are alternative sources
                                            that may help answer your question.
                                          </p>
                                        )}
                                        <PublicationList
                                          publications={publications}
                                          intro={null}
                                          collapseLimit={3}
                                          className="mt-0"
                                          onOpen={publication => recordUiEvent('publication_open', {
                                            title: publication.paper_title || publication.source_paper || '',
                                            doi: publication.doi || '',
                                          }, message.turnId)}
                                        />
                                      </div>
                                    )}
                                  </div>
                                </div>
                                ) : null}

                                {!streaming && message.turnId && (
                                  <TurnAnnotationBar
                                    turnId={message.turnId}
                                    annotation={message.annotation}
                                    onSaved={annotation => saveAnnotation(message.id, annotation)}
                                  />
                                )}

                                {!streaming && !isError && showKnowledgeGraph && (
                                  <div className="mt-3 flex items-center gap-1">
                                    <button
                                      type="button"
                                      onClick={() => viewKnowledgeGraph(message)}
                                      className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-xs transition ${
                                        isPinned
                                          ? 'bg-sky-100 text-sky-700'
                                          : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
                                      }`}
                                    >
                                      <Share2 size={14} strokeWidth={2} aria-hidden="true" />
                                      View Knowledge Graph
                                    </button>
                                  </div>
                                )}

                              </div>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                ))}

                {isThinking && (
                  <ThinkingStatus status={thinkingStatus} elapsedSeconds={elapsedSeconds} />
                )}

                <div ref={endRef} />
              </div>

              <div className="shrink-0 border-t border-slate-200 px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex flex-1 items-center rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                    <input
                      value={inputValue}
                      onChange={e => setInputValue(e.target.value)}
                      onKeyDown={onInputKeyDown}
                      placeholder="Ask a domain-specific question..."
                      className="flex-1 bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400"
                    />
                  </div>
                  <button
                    type="button"
                    aria-label={isBusy ? 'Stop' : 'Send question'}
                    title={canStop ? 'Stop' : 'Send'}
                    onClick={onActionClick}
                    disabled={!canStop && !canSubmit}
                    className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-500 text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-sky-500`}
                  >
                    {canStop ? (
                      <span className="inline-block h-3 w-3 rounded-sm bg-white" aria-hidden="true" />
                    ) : (
                      <ArrowUp size={18} strokeWidth={2.5} />
                    )}
                  </button>
                </div>
              </div>
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle className="bg-slate-200" />

          <ResizablePanel defaultSize={64} minSize={25}>
            <div className="flex h-full min-h-0 w-full">
              {graphView}
            </div>
          </ResizablePanel>
          </ResizablePanelGroup>
        )}
      </div>
    </div>
  );
}
