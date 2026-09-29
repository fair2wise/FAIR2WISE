import '@blueskyproject/finch/style.css';
import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { BrowserRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  FinchConfigProvider,
  HubHeader,
  HubMainContent,
  HubSidebar,
} from '@blueskyproject/finch';
import { Share2 } from 'lucide-react';
import { AppBookmarksButton } from './components/AppBookmarksButton';
import { AppNewChatButton } from './components/AppNewChatButton';
import { AppPaperSearchButton } from './components/AppPaperSearchButton';
import { AppSearchChatsButton } from './components/AppSearchChatsButton';
import { AppDocsButton } from './components/AppDocsButton';
import { AppSettingsButton } from './components/AppSettingsButton';
import { ChatSidebar } from './components/ChatSidebar';
import {
  createChatSession,
  loadChatSessionStore,
  MAX_STORED_MESSAGES,
  NEW_CHAT_TITLE,
  saveChatSessionStore,
  titleFromFirstPrompt,
  type ChatMessage,
} from './components/chatSessions';
import { ExampleQuery } from './components/data/mockupData';
import {
  saveAgentSettings,
  settingsFromApiResponse,
} from './components/agentSettings';
import {
  deleteAgentSession,
  EMPTY_GRAPH,
  fetchAgentSettings,
  fetchLiveGraph,
  GraphPayload,
} from './components/data/liveAgent';
import { installUiTelemetry, recordUiEvent } from './components/uiTelemetry';

const queryClient = new QueryClient();
const EMPTY_QUERY: ExampleQuery = {
  id: 'idle',
  question: '',
  answer: '',
  nodeIds: [],
  confidence: 0,
};

interface AgentKGViewProps {
  graph: GraphPayload;
  activeQuery: ExampleQuery;
  sessionId: string;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  onGraphUpdate: (graph: GraphPayload) => void;
  onSelect: (query: ExampleQuery) => void;
  onLoadCatalog?: () => void;
}

function AgentKGView({
  graph,
  activeQuery,
  sessionId,
  messages,
  setMessages,
  onGraphUpdate,
  onSelect,
  onLoadCatalog,
}: AgentKGViewProps) {
  return (
    <div className="flex h-full min-h-0 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <ChatSidebar
        graph={graph}
        activeQuery={activeQuery}
        sessionId={sessionId}
        messages={messages}
        setMessages={setMessages}
        onGraphUpdate={onGraphUpdate}
        onSelect={onSelect}
        onLoadCatalog={onLoadCatalog}
      />
    </div>
  );
}

export default function App() {
  const [activeQuery, setActiveQuery] = useState<ExampleQuery>(EMPTY_QUERY);
  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [chatStore, setChatStore] = useState(() => loadChatSessionStore());
  const activeSession = chatStore.sessions.find(session => session.id === chatStore.activeSessionId)
    ?? chatStore.sessions[0];

  useEffect(() => {
    saveChatSessionStore(chatStore);
  }, [chatStore]);

  useEffect(() => installUiTelemetry(), []);

  const setActiveSessionMessages = useCallback<Dispatch<SetStateAction<ChatMessage[]>>>((update) => {
    const sessionId = activeSession.id;
    setChatStore(store => ({
      ...store,
      sessions: store.sessions.map(session => {
        if (session.id !== sessionId) return session;
        const nextMessages = typeof update === 'function' ? update(session.messages) : update;
        const messages = nextMessages.slice(-MAX_STORED_MESSAGES);
        const firstUser = messages.find(message => message.role === 'user');
        return {
          ...session,
          title: session.title === NEW_CHAT_TITLE && firstUser
            ? titleFromFirstPrompt(firstUser.content)
            : session.title,
          updatedAt: Date.now(),
          messages,
        };
      }),
    }));
  }, [activeSession.id]);

  function selectSession(sessionId: string) {
    if (sessionId === chatStore.activeSessionId) return;
    setActiveQuery(EMPTY_QUERY);
    setChatStore(store => ({ ...store, activeSessionId: sessionId }));
  }

  function createNewChat() {
    recordUiEvent('new_chat');
    const session = createChatSession();
    setActiveQuery(EMPTY_QUERY);
    setChatStore(store => ({
      activeSessionId: session.id,
      sessions: [...store.sessions, session],
    }));
  }

  function deleteChat(sessionId: string) {
    recordUiEvent('delete_chat', { session_id: sessionId });
    if (sessionId === chatStore.activeSessionId) setActiveQuery(EMPTY_QUERY);
    void deleteAgentSession(sessionId).catch(error => {
      console.warn('Failed to delete backend chat context', error);
      recordUiEvent('console_warning', {
        message: 'Failed to delete backend chat context',
        detail: error instanceof Error ? error.message : String(error),
      });
    });
    setChatStore(store => {
      let sessions = store.sessions.filter(session => session.id !== sessionId);
      if (sessions.length === 0) sessions = [createChatSession()];
      const activeSessionId = store.activeSessionId === sessionId
        ? [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)[0].id
        : store.activeSessionId;
      return { activeSessionId, sessions };
    });
  }

  const reloadGraph = useCallback(async () => {
    const nextGraph = await fetchLiveGraph();
    setGraph(nextGraph);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      try {
        const response = await fetchAgentSettings();
        if (!cancelled) saveAgentSettings(settingsFromApiResponse(response));
      } catch (error) {
        console.warn('Failed to load agent settings', error);
        recordUiEvent('console_warning', {
          message: 'Failed to load agent settings',
          detail: error instanceof Error ? error.message : String(error),
        });
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, []);

  const routes = useMemo(() => [
    {
      path: '/',
      label: 'FAIR2WISE',
      element: (
        <AgentKGView
          graph={graph}
          activeQuery={activeQuery}
          sessionId={activeSession.id}
          messages={activeSession.messages}
          setMessages={setActiveSessionMessages}
          onGraphUpdate={setGraph}
          onSelect={setActiveQuery}
          onLoadCatalog={reloadGraph}
        />
      ),
      icon: <Share2 size={28} />,
      isBackgroundTransparent: true,
    },
  ], [activeQuery, activeSession.id, activeSession.messages, graph, reloadGraph, setActiveSessionMessages]);

  const headerLogoIcon = (
    <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-lg bg-white">
      <img
        src="/wise_owl.svg"
        alt="FAIR2WISE"
        className="h-full w-auto max-w-full object-contain"
      />
    </div>
  );

  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <FinchConfigProvider config={{}}>
          <div className="grid h-screen w-screen grid-cols-[6rem_1fr] grid-rows-[auto_1fr]">
            <HubSidebar routes={routes} />
            <HubHeader
              title="FAIR2WISE"
              logoIcon={headerLogoIcon}
              rightSlot={
                <div className="mr-6 flex items-center gap-2">
                  <AppNewChatButton onClick={createNewChat} />
                  <AppSearchChatsButton
                    sessions={chatStore.sessions}
                    activeSessionId={activeSession.id}
                    onSelect={selectSession}
                    onDelete={deleteChat}
                  />
                  <AppPaperSearchButton />
                  <AppBookmarksButton />
                  <AppDocsButton />
                  <AppSettingsButton onSettingsApplied={reloadGraph} />
                </div>
              }
            />
            <HubMainContent
              routes={routes}
              className="h-[calc(100vh-4rem)] p-6"
            />
          </div>
        </FinchConfigProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
}
