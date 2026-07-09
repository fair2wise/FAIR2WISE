import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowUp, Check, Copy, Share2 } from 'lucide-react';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { CodeBlock } from './CodeBlock';
import { ExampleQuery } from './data/mockupData';
import { GraphMockup } from './GraphMockup';
import { parseKgCitationNodeIds } from './kgCitations';
import { PublicationList } from './PublicationList';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from './ui/resizable';
import {
  AgentChatHistoryMessage,
  ChatProgressEvent,
  GraphPayload,
  PublicationInfo,
  ThinkingStep,
  queryLiveAgentStream,
} from './data/liveAgent';

function MarkdownText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith('**') && part.endsWith('**') ? (
          <strong key={i} className="font-semibold text-sky-900">{part.slice(2, -2)}</strong>
        ) : (
          <span key={i}>{part}</span>
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

function publicationsBlockText(publications: PublicationInfo[], markdown = false): string {
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
  return PUBLICATIONS_INTRO + entries.join('\n\n');
}

function assistantCopyText(message: ChatMessage): string {
  const parts = [message.content];
  if (message.publications?.length) {
    parts.push(publicationsBlockText(message.publications));
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
              <MarkdownText text={para} />
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
  chatResetSignal: number;
  onGraphUpdate: (graph: GraphPayload) => void;
  onSelect: (q: ExampleQuery) => void;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  question?: string;
  highlightNodeIds?: string[];
  retrievedNodeIds?: string[];
  confidence?: number;
  status?: string;
  graphSourceUsed?: string | null;
  elapsedSeconds?: number;
  publications?: PublicationInfo[];
}

const CHAT_STORAGE_KEY = 'fair2wise.chat.messages.v1';
const MAX_STORED_MESSAGES = 80;
const MAX_REQUEST_HISTORY_MESSAGES = 8;

function requestHistoryFromMessages(messages: ChatMessage[]): AgentChatHistoryMessage[] {
  return messages
    .filter(message => message.role === 'user' || message.role === 'assistant')
    .slice(-MAX_REQUEST_HISTORY_MESSAGES)
    .map(message => ({
      role: message.role,
      content: message.content,
    }));
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function asPublicationArray(value: unknown): PublicationInfo[] {
  return Array.isArray(value) ? value.filter((item): item is PublicationInfo => Boolean(item) && typeof item === 'object') : [];
}

function normalizeChatMessage(value: unknown): ChatMessage | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<ChatMessage>;
  if (
    typeof candidate.id !== 'string' ||
    (candidate.role !== 'user' && candidate.role !== 'assistant') ||
    typeof candidate.content !== 'string'
  ) {
    return null;
  }

  return {
    id: candidate.id,
    role: candidate.role,
    content: candidate.content,
    question: typeof candidate.question === 'string' ? candidate.question : undefined,
    highlightNodeIds: asStringArray(candidate.highlightNodeIds),
    retrievedNodeIds: asStringArray(candidate.retrievedNodeIds),
    confidence: typeof candidate.confidence === 'number' && Number.isFinite(candidate.confidence)
      ? candidate.confidence
      : undefined,
    status: typeof candidate.status === 'string' ? candidate.status : undefined,
    graphSourceUsed: typeof candidate.graphSourceUsed === 'string' ? candidate.graphSourceUsed : null,
    elapsedSeconds: typeof candidate.elapsedSeconds === 'number' && Number.isInteger(candidate.elapsedSeconds)
      ? candidate.elapsedSeconds
      : undefined,
    publications: asPublicationArray(candidate.publications),
  };
}

function loadStoredMessages(): ChatMessage[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizeChatMessage)
      .filter((message): message is ChatMessage => Boolean(message))
      .slice(-MAX_STORED_MESSAGES);
  } catch {
    return [];
  }
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

export function ChatSidebar({ graph, activeQuery, chatResetSignal, onGraphUpdate, onSelect }: Props) {
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadStoredMessages());
  const [isThinking, setIsThinking] = useState(false);
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [streamedLen, setStreamedLen] = useState(0);
  const [streamGraph, setStreamGraph] = useState<GraphPayload | null>(null);
  const [streamNodeIds, setStreamNodeIds] = useState<string[]>([]);
  const [pinnedViewId, setPinnedViewId] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const restoredSelectionRef = useRef(false);
  const activeRequestRef = useRef<AbortController | null>(null);
  const requestSeqRef = useRef(0);
  const requestStartedAtRef = useRef<number | null>(null);
  const stepsRef = useRef<ThinkingStep[]>([]);

  const isBusy = isThinking || streamingId !== null;
  const canSubmit = inputValue.trim().length > 0 && !isBusy;
  const thinkingStatus = steps.length > 0
    ? steps[steps.length - 1].label
    : 'Starting agent loop…';

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
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages.slice(-MAX_STORED_MESSAGES)));
  }, [messages]);

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
    if (restoredSelectionRef.current) return;
    restoredSelectionRef.current = true;
    const lastAssistant = [...messages].reverse().find(message => message.role === 'assistant');
    if (lastAssistant) {
      if ((lastAssistant.highlightNodeIds?.length ?? 0) > 0) {
        setPinnedViewId(lastAssistant.id);
      }
      applyAssistantSelection(lastAssistant);
    }
  }, []);

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
        event => {
          if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
          if (event.phase === 'graph_update' && event.graph) {
            // Stream retrieved nodes into the KG before the slower steps run.
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
        },
        controller.signal,
        requestHistory,
      );
      if (requestSeqRef.current !== requestId || controller.signal.aborted) return;
      const elapsed = requestStartedAtRef.current === null
        ? undefined
        : Math.max(1, Math.round((Date.now() - requestStartedAtRef.current) / 1000));
      const highlightNodeIds = result.node_ids ?? [];
      onGraphUpdate(result.graph);
      const assistantMessage: ChatMessage = {
        id: `agent-${Date.now()}`,
        role: 'assistant',
        content: result.answer || 'No grounded answer was returned.',
        question,
        highlightNodeIds,
        retrievedNodeIds: result.node_ids ?? [],
        confidence: result.confidence,
        status: result.status,
        graphSourceUsed: result.graph_source_used,
        elapsedSeconds: elapsed,
        publications: result.publications ?? [],
      };
      setStreamedLen(0);
      setStreamingId(assistantMessage.id);
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

  function newChat() {
    stopGeneration();
    setMessages([]);
    setInputValue('');
    setPinnedViewId(null);
    setCopiedMessageId(null);
    onSelect({ id: 'idle', question: '', answer: '', nodeIds: [], confidence: 0 });
  }

  useEffect(() => {
    if (!chatResetSignal) return;
    newChat();
  }, [chatResetSignal]);

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (isBusy) return;
    submit(inputValue);
  }

  function onActionClick() {
    if (isBusy) {
      stopGeneration();
      return;
    }
    submit(inputValue);
  }

  const messageExchanges = groupMessageExchanges(messages);
  const displayGraph = streamGraph ?? graph;
  const canAnimateCitations = !isThinking && streamingId === null;
  const citationMessageId = activeQuery.id !== 'idle' ? activeQuery.id : null;
  const citationAnswerText = useMemo(() => {
    if (!canAnimateCitations || !citationMessageId) return '';

    const message = messages.find(entry => entry.id === citationMessageId);
    if (!message || message.role !== 'assistant') return '';

    return message.content;
  }, [canAnimateCitations, citationMessageId, messages]);
  const citationPublications = useMemo(() => {
    if (!canAnimateCitations || !citationMessageId) return [];

    const message = messages.find(entry => entry.id === citationMessageId);
    if (!message || message.role !== 'assistant') return [];

    return message.publications ?? [];
  }, [canAnimateCitations, citationMessageId, messages]);
  const citedNodeIds = useMemo(() => {
    if (!canAnimateCitations) return [];

    const highlightedIds = activeQuery.nodeIds;
    const lookupNodes = highlightedIds.length > 0
      ? displayGraph.nodes.filter(node => highlightedIds.includes(node.id))
      : displayGraph.nodes;
    return parseKgCitationNodeIds(citationAnswerText, lookupNodes, citationPublications);
  }, [
    canAnimateCitations,
    citationAnswerText,
    citationPublications,
    displayGraph.nodes,
    activeQuery.nodeIds,
  ]);
  const citationAnimationKey = canAnimateCitations ? (citationMessageId ?? '') : '';

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-white">
      {/* KG (left) + chat (right) with a draggable divider */}
      <div className="min-h-0 flex-1">
        <ResizablePanelGroup direction="horizontal">
          <ResizablePanel defaultSize={64} minSize={25}>
            <div className="flex h-full min-h-0 w-full">
              <GraphMockup
                graph={displayGraph}
                highlightedNodeIds={isThinking && streamNodeIds.length ? streamNodeIds : activeQuery.nodeIds}
                citedNodeIds={citedNodeIds}
                citationAnimationKey={citationAnimationKey}
              />
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle className="bg-slate-200" />

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
                          const showPublications = publications.length > 0
                            && (!streaming || streamedLen >= message.content.length);
                          const isPinned = pinnedViewId === message.id;
                          const hasGraphNodes = (message.highlightNodeIds?.length ?? 0) > 0;
                          const isError = message.status === 'api_error';
                          return (
                            <div key={message.id} className="w-full">
                              <div className="min-w-0 flex-1">
                                {isError ? (
                                  <AppErrorMessage title="Agent run failed">
                                    {message.content.replace(/^Agent run failed:\s*/i, '') || message.content}
                                  </AppErrorMessage>
                                ) : (
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
                                      <div className="border-t border-slate-200 bg-white/60 px-4 py-3">
                                        <p className="mb-8 text-sm font-bold text-slate-800">
                                          Relevant Publications and Sources:
                                        </p>
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
                                )}

                                {!streaming && !isError && hasGraphNodes && (
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
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      {/* Full-width ask bar */}
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
            title={isBusy ? 'Stop' : 'Send'}
            onClick={onActionClick}
            disabled={!isBusy && !canSubmit}
            className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-500 text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-sky-500`}
          >
            {isBusy ? (
              <span className="inline-block h-3 w-3 rounded-sm bg-white" aria-hidden="true" />
            ) : (
              <ArrowUp size={18} strokeWidth={2.5} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
