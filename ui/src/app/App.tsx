import '@blueskyproject/finch/style.css';
import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
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
  loadAgentSettings,
  saveAgentSettings,
  settingsFromApiResponse,
  settingsToApiPayload,
} from './components/agentSettings';
import {
  deleteAgentSession,
  EMPTY_GRAPH,
  fetchLiveGraph,
  GraphPayload,
  updateAgentSettings,
} from './components/data/liveAgent';

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
}

function AgentKGView({
  graph,
  activeQuery,
  sessionId,
  messages,
  setMessages,
  onGraphUpdate,
  onSelect,
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
    const session = createChatSession();
    setActiveQuery(EMPTY_QUERY);
    setChatStore(store => ({
      activeSessionId: session.id,
      sessions: [...store.sessions, session],
    }));
  }

  function deleteChat(sessionId: string) {
    if (sessionId === chatStore.activeSessionId) setActiveQuery(EMPTY_QUERY);
    void deleteAgentSession(sessionId).catch(error => {
      console.warn('Failed to delete backend chat context', error);
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
        const local = loadAgentSettings();
        const response = await updateAgentSettings(settingsToApiPayload(local));
        const synced = settingsFromApiResponse(response);
        saveAgentSettings({
          backend: local.backend,
          model: local.model,
          graphSource: local.graphSource,
          workflowMode: local.workflowMode,
          extractionMode: local.extractionMode,
          targetedMaxPages: local.targetedMaxPages,
          jsonGraphPath: synced.jsonGraphPath,
        });
        const nextGraph = await fetchLiveGraph();
        if (!cancelled) setGraph(nextGraph);
      } catch (error) {
        console.warn('Failed to load live KG', error);
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, []);

  const routes = [
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
        />
      ),
      icon: <Share2 size={28} />,
      isBackgroundTransparent: true,
    },
  ];

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
