import { beforeEach, describe, expect, it } from 'vitest';
import {
  CHAT_SESSIONS_STORAGE_KEY,
  LEGACY_CHAT_STORAGE_KEY,
  createChatSession,
  loadChatSessionStore,
  matchingChatSessions,
  saveChatSessionStore,
  titleFromFirstPrompt,
  type ChatSession,
} from './chatSessions';

describe('chatSessions', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('creates a distinct empty session every time', () => {
    const first = createChatSession(10);
    const second = createChatSession(20);
    expect(first.id).not.toBe(second.id);
    expect(first.messages).toEqual([]);
    expect(second.messages).toEqual([]);
  });

  it('titles sessions from the normalized first prompt', () => {
    expect(titleFromFirstPrompt('  Compare   P3HT and PTB7  ')).toBe('Compare P3HT and PTB7');
    expect(titleFromFirstPrompt('x'.repeat(80))).toBe(`${'x'.repeat(59)}…`);
  });

  it('migrates the legacy single-chat history', () => {
    localStorage.setItem(LEGACY_CHAT_STORAGE_KEY, JSON.stringify([
      { id: 'u1', role: 'user', content: 'Legacy question' },
      { id: 'a1', role: 'assistant', content: 'Legacy answer' },
    ]));

    const store = loadChatSessionStore();
    expect(store.sessions).toHaveLength(1);
    expect(store.sessions[0].title).toBe('Legacy question');
    expect(store.sessions[0].messages).toHaveLength(2);

    saveChatSessionStore(store);
    expect(localStorage.getItem(CHAT_SESSIONS_STORAGE_KEY)).not.toBeNull();
    expect(localStorage.getItem(LEGACY_CHAT_STORAGE_KEY)).toBeNull();
  });

  it('restores active session and filters newest chats first', () => {
    const sessions: ChatSession[] = [
      { id: 'older', title: 'Polymer comparison', createdAt: 1, updatedAt: 10, messages: [] },
      { id: 'newer', title: 'Polymer synthesis', createdAt: 2, updatedAt: 20, messages: [] },
      { id: 'other', title: 'X-ray scattering', createdAt: 3, updatedAt: 30, messages: [] },
    ];
    saveChatSessionStore({ activeSessionId: 'newer', sessions });

    expect(loadChatSessionStore().activeSessionId).toBe('newer');
    expect(matchingChatSessions(sessions, 'polymer').map(session => session.id))
      .toEqual(['newer', 'older']);
  });

  it('keeps the most recent 80 messages per session', () => {
    const session = createChatSession(1);
    session.messages = Array.from({ length: 85 }, (_, index) => ({
      id: `message-${index}`,
      role: 'user' as const,
      content: `Message ${index}`,
    }));
    saveChatSessionStore({ activeSessionId: session.id, sessions: [session] });

    const restored = loadChatSessionStore().sessions[0];
    expect(restored.messages).toHaveLength(80);
    expect(restored.messages[0].id).toBe('message-5');
  });
});
