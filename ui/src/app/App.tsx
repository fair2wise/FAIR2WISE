import '@blueskyproject/finch/style.css';
import { useCallback, useEffect, useState } from 'react';
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
import { AppSettingsButton } from './components/AppSettingsButton';
import { ChatSidebar } from './components/ChatSidebar';
import { ExampleQuery } from './components/data/mockupData';
import {
  loadAgentSettings,
  saveAgentSettings,
  settingsFromApiResponse,
  settingsToApiPayload,
} from './components/agentSettings';
import { EMPTY_GRAPH, fetchLiveGraph, GraphPayload, updateAgentSettings } from './components/data/liveAgent';

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
  chatResetSignal: number;
  onGraphUpdate: (graph: GraphPayload) => void;
  onSelect: (query: ExampleQuery) => void;
}

function AgentKGView({
  graph,
  activeQuery,
  chatResetSignal,
  onGraphUpdate,
  onSelect,
}: AgentKGViewProps) {
  return (
    <div className="flex h-full min-h-0 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <ChatSidebar
        graph={graph}
        activeQuery={activeQuery}
        chatResetSignal={chatResetSignal}
        onGraphUpdate={onGraphUpdate}
        onSelect={onSelect}
      />
    </div>
  );
}

export default function App() {
  const [activeQuery, setActiveQuery] = useState<ExampleQuery>(EMPTY_QUERY);
  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [chatResetSignal, setChatResetSignal] = useState(0);

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
          graphSource: local.graphSource,
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
          chatResetSignal={chatResetSignal}
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
                  <AppNewChatButton onClick={() => setChatResetSignal(signal => signal + 1)} />
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
