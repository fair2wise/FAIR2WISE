import type { PendingAction, PublicationInfo } from './data/liveAgent';

export interface ChatMessage {
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
  pending?: PendingAction | null;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export interface ChatSessionStore {
  activeSessionId: string;
  sessions: ChatSession[];
}

export const CHAT_SESSIONS_STORAGE_KEY = 'fair2wise.chat.sessions.v2';
export const LEGACY_CHAT_STORAGE_KEY = 'fair2wise.chat.messages.v1';
export const MAX_STORED_MESSAGES = 80;
export const NEW_CHAT_TITLE = 'New chat';

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function titleFromFirstPrompt(prompt: string): string {
  const normalized = prompt.replace(/\s+/g, ' ').trim();
  if (!normalized) return NEW_CHAT_TITLE;
  return normalized.length <= 60 ? normalized : `${normalized.slice(0, 59).trimEnd()}…`;
}

export function createChatSession(now = Date.now()): ChatSession {
  return {
    id: newSessionId(),
    title: NEW_CHAT_TITLE,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function asPublicationArray(value: unknown): PublicationInfo[] {
  return Array.isArray(value)
    ? value.filter((item): item is PublicationInfo => Boolean(item) && typeof item === 'object')
    : [];
}

export function normalizeChatMessage(value: unknown): ChatMessage | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<ChatMessage>;
  if (
    typeof candidate.id !== 'string'
    || (candidate.role !== 'user' && candidate.role !== 'assistant')
    || typeof candidate.content !== 'string'
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
    pending: candidate.pending && typeof candidate.pending === 'object'
      ? candidate.pending as PendingAction
      : null,
  };
}

function normalizedMessages(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(normalizeChatMessage)
    .filter((message): message is ChatMessage => Boolean(message))
    .slice(-MAX_STORED_MESSAGES);
}

function normalizeSession(value: unknown): ChatSession | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<ChatSession>;
  if (typeof candidate.id !== 'string' || !candidate.id) return null;
  const createdAt = typeof candidate.createdAt === 'number' && Number.isFinite(candidate.createdAt)
    ? candidate.createdAt
    : Date.now();
  const updatedAt = typeof candidate.updatedAt === 'number' && Number.isFinite(candidate.updatedAt)
    ? candidate.updatedAt
    : createdAt;
  const messages = normalizedMessages(candidate.messages);
  const firstUser = messages.find(message => message.role === 'user');
  return {
    id: candidate.id,
    title: typeof candidate.title === 'string' && candidate.title.trim()
      ? candidate.title.trim()
      : titleFromFirstPrompt(firstUser?.content ?? ''),
    createdAt,
    updatedAt,
    messages,
  };
}

function storeFromSessions(sessions: ChatSession[], requestedActiveId?: unknown): ChatSessionStore {
  const unique = sessions.filter((session, index) => (
    sessions.findIndex(candidate => candidate.id === session.id) === index
  ));
  if (unique.length === 0) unique.push(createChatSession());
  const activeSessionId = typeof requestedActiveId === 'string'
    && unique.some(session => session.id === requestedActiveId)
    ? requestedActiveId
    : unique[0].id;
  return { activeSessionId, sessions: unique };
}

export function loadChatSessionStore(): ChatSessionStore {
  if (typeof window === 'undefined') return storeFromSessions([]);
  try {
    const raw = window.localStorage.getItem(CHAT_SESSIONS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<ChatSessionStore>;
      const sessions = Array.isArray(parsed.sessions)
        ? parsed.sessions.map(normalizeSession).filter((session): session is ChatSession => Boolean(session))
        : [];
      return storeFromSessions(sessions, parsed.activeSessionId);
    }
  } catch {
    // Fall through to legacy migration or a fresh session.
  }

  try {
    const legacy = window.localStorage.getItem(LEGACY_CHAT_STORAGE_KEY);
    if (legacy) {
      const messages = normalizedMessages(JSON.parse(legacy));
      const session = createChatSession();
      session.messages = messages;
      const firstUser = messages.find(message => message.role === 'user');
      session.title = titleFromFirstPrompt(firstUser?.content ?? '');
      return storeFromSessions([session], session.id);
    }
  } catch {
    // Invalid legacy data becomes a fresh session.
  }
  return storeFromSessions([]);
}

export function saveChatSessionStore(store: ChatSessionStore): void {
  if (typeof window === 'undefined') return;
  const sessions = store.sessions.map(session => ({
    ...session,
    messages: session.messages.slice(-MAX_STORED_MESSAGES),
  }));
  try {
    window.localStorage.setItem(
      CHAT_SESSIONS_STORAGE_KEY,
      JSON.stringify({ ...store, sessions }),
    );
    window.localStorage.removeItem(LEGACY_CHAT_STORAGE_KEY);
  } catch {
    // Storage quota or privacy mode must not break chat.
  }
}

export function matchingChatSessions(sessions: ChatSession[], query: string): ChatSession[] {
  const normalized = query.trim().toLocaleLowerCase();
  return [...sessions]
    .filter(session => !normalized || session.title.toLocaleLowerCase().includes(normalized))
    .sort((a, b) => b.updatedAt - a.updatedAt);
}
