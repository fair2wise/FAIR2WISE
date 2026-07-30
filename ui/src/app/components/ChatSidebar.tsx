import { useEffect, useMemo, useRef, useState, type Dispatch, type KeyboardEvent, type SetStateAction } from 'react';
import { ArrowUp, Check, Copy, Share2 } from 'lucide-react';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { CodeBlock } from './CodeBlock';
import { ExampleQuery } from './data/mockupData';
import { GraphMockup } from './GraphMockup';
import { parseKgCitationNodeIds, splitAnswerHighlightSegments } from './kgCitations';
import { PublicationList } from './PublicationList';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from './ui/resizable';
import type { ChatMessage } from './chatSessions';
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

function AnswerHighlightText({ text }: { text: string }) {
  const segments = splitAnswerHighlightSegments(text);
  return (
    <>
      {segments.map((segment, i) =>
        segment.bold ? (
          <strong key={i} className="font-semibold text-sky-900">{segment.text}</strong>
        ) : (
          <span key={i}>{segment.text}</span>
        )
      )}
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

const HIDE_KG_LINK_STATUSES = new Set([
  'awaiting_download_decision',
  'awaiting_extraction_decision',
  'no_new_papers',
]);

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
  if (message.publications?.length) {
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

function MessageText({ text, cursor = false }: { text: string; cursor?: boolean }) {
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
              <AnswerHighlightText text={para} />
              {cursorHere && <span className="ml-px animate-pulse text-slate-400">▌</span>}
            </p>
          );
        });
      })}
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

export function ChatSidebar({ graph, activeQuery, sessionId, messages, setMessages, onGraphUpdate, onSelect }: Props) {
  const [inputValue, setInputValue] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [streamedLen, setStreamedLen] = useState(0);
  const [streamGraph, setStreamGraph] = useState<GraphPayload | null>(null);
  const [streamNodeIds, setStreamNodeIds] = useState<string[]>([]);
  const [pinnedViewId, setPinnedViewId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [isKgViewer, setIsKgViewer] = useState(false);
  const [kgViewerNodeLimit, setKgViewerNodeLimit] = useState<number | 'all'>(100);
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
    stopGeneration();
    setInputValue('');
    setCopiedMessageId(null);
    setPinnedViewId(null);
    const lastAssistant = [...messages].reverse().find(message => message.role === 'assistant');
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
    onGraphUpdate(result.graph);
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
    };
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
    setSteps([]);
    setStreamGraph(null);
    setStreamNodeIds([]);
    setPinnedViewId(null);
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
    setSteps([]);
    setStreamGraph(null);
    setStreamNodeIds([]);
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
    if (!streamGraph) return graph;
    const nodes = new Map(graph.nodes.map(node => [node.id, node]));
    for (const node of streamGraph.nodes) nodes.set(node.id, node);
    const edgeKey = (edge: { source: string; target: string; predicate: string }) =>
      `${edge.source}\u0000${edge.predicate}\u0000${edge.target}`;
    const edges = new Map(graph.edges.map(edge => [edgeKey(edge), edge]));
    for (const edge of streamGraph.edges) edges.set(edgeKey(edge), edge);
    return {
      nodes: Array.from(nodes.values()),
      edges: Array.from(edges.values()),
      source_path: graph.source_path || streamGraph.source_path,
    };
  }, [graph, streamGraph]);
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
  const citedNodeIds = useMemo(() => {
    if (!citationMessageId) return [];

    const highlightedIds = activeQuery.nodeIds;
    const lookupNodes = highlightedIds.length > 0
      ? displayGraph.nodes.filter(node => highlightedIds.includes(node.id))
      : displayGraph.nodes;
    return parseKgCitationNodeIds(citationAnswerText, lookupNodes, citationPublications);
  }, [
    citationMessageId,
    citationAnswerText,
    citationPublications,
    displayGraph.nodes,
    activeQuery.nodeIds,
  ]);
  const citationAnimationKey = citationMessageId && citedNodeIds.length > 0 ? citationMessageId : '';

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
              label: updated.label,
              type: updated.type,
              description: updated.description,
              publications: updated.publications,
              code_snippet: updated.code_snippet,
              code_language: updated.code_language,
              function_name: updated.function_name,
              linked_code_snippets: updated.linked_code_snippets,
            }
          : node
      )),
    });
  }

  const graphView = (
    <GraphMockup
      graph={displayGraph}
      highlightedNodeIds={isThinking && streamNodeIds.length ? streamNodeIds : activeQuery.nodeIds}
      citedNodeIds={citedNodeIds}
      citationAnimationKey={citationAnimationKey}
      isKgViewer={isKgViewer}
      kgViewerNodeLimit={kgViewerNodeLimit}
      onToggleKgViewer={() => setIsKgViewer(value => !value)}
      onKgViewerNodeLimitChange={setKgViewerNodeLimit}
      onNodeUpdated={handleGraphNodeUpdated}
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
                                          <MessageText text={message.content} />
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
                                      <MessageText text={displayText} cursor={showAnswerCursor} />
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
                                        />
                                      </div>
                                    )}
                                  </div>
                                </div>
                                ) : null}

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
