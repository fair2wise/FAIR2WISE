export interface LinkedCodeSnippet {
  id: string;
  label?: string;
  function_name?: string | null;
  code_language?: string | null;
  code_snippet: string;
  publications?: PublicationInfo[];
}

export interface LiveGraphNode {
  id: string;
  label: string;
  type: string;
  description: string;
  publications?: PublicationInfo[];
  code_snippet?: string | null;
  code_language?: string | null;
  function_name?: string | null;
  linked_code_snippets?: LinkedCodeSnippet[];
}

export interface LiveGraphEdge {
  source: string;
  target: string;
  predicate: string;
}

export interface GraphPayload {
  nodes: LiveGraphNode[];
  edges: LiveGraphEdge[];
  source_path: string;
}

export const EMPTY_GRAPH: GraphPayload = {
  nodes: [],
  edges: [],
  source_path: '',
};

export interface PublicationNodeRef {
  id: string;
  name: string;
  category: string;
}

export interface PublicationInfo {
  source_paper?: string;
  publication_year?: number;
  paper_title?: string;
  authors?: string[];
  institutions?: string[];
  doi?: string;
  journal?: string;
  volume?: string;
  issue?: string;
  pages_range?: string;
  pages?: number[];
  abstract_text?: string;
  keywords?: string[];
  supporting_nodes?: PublicationNodeRef[];
}

export interface AgentChatResponse {
  status: string;
  answer: string;
  sufficient: boolean;
  node_ids: string[];
  publications?: PublicationInfo[];
  confidence: number;
  rounds: Array<Record<string, unknown>>;
  graph: GraphPayload;
  graph_source_requested?: string | null;
  graph_source_used?: string | null;
  workdir: string;
}

export interface AgentChatHistoryMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface PublicationSearchOptions {
  maxResults?: number;
  includeExternal?: boolean;
}

export interface PublicationSearchResponse {
  status: string;
  query: string;
  publications: PublicationInfo[];
  matched_node_ids: string[];
  source: 'kg' | 'kg+openalex' | string;
}

export interface ChatProgressEvent {
  phase: string;
  message: string;
  round?: number;
  missing_topics?: string[];
  pdfs?: string[];
  selected_count?: number;
  direct_evidence_count?: number;
  sufficient?: boolean;
  count?: number;
  titles?: string[];
  candidate_titles?: string[];
  selected_action?: string;
  reason?: string;
  skipped?: number;
  failed?: number;
  term_count?: number;
  processed_files?: number;
  processed_pages_with_terms?: number;
  node_count?: number;
  edge_count?: number;
  status?: string;
  node_ids?: string[];
  graph?: { nodes: LiveGraphNode[]; edges: LiveGraphEdge[] };
}

export interface ThinkingStep {
  id: string;
  phase: string;
  label: string;
  detail?: string;
  round?: number;
  state: 'active' | 'done';
}

const AGENT_API_BASE = (import.meta.env.VITE_F2W_AGENT_API_URL || 'http://127.0.0.1:8090').replace(/\/$/, '');

export { AGENT_API_BASE };

export interface AgentSettingsApiResponse {
  backend: 'cborg' | 'ollama';
  graph_source: 'splash' | 'json';
  workflow_mode: 'deterministic' | 'agentic';
  json_graph_path: string | null;
  available_json_graphs: string[];
}

export interface AgentSettingsApiUpdate {
  backend?: 'cborg' | 'ollama';
  graph_source?: 'splash' | 'json';
  workflow_mode?: 'deterministic' | 'agentic';
  json_graph_path?: string | null;
}

import { agentNetworkErrorMessage, settingsApiErrorMessage } from '../agentApiErrors';

export async function fetchAgentSettings(): Promise<AgentSettingsApiResponse> {
  try {
    const response = await fetch(`${AGENT_API_BASE}/settings`);
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(settingsApiErrorMessage(detail, response.status));
    }
    return response.json();
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('Settings endpoint')) {
      throw error;
    }
    if (error instanceof Error && error.message.startsWith('Agent API returned')) {
      throw error;
    }
    throw new Error(agentNetworkErrorMessage(AGENT_API_BASE, error));
  }
}

export async function updateAgentSettings(
  update: AgentSettingsApiUpdate,
): Promise<AgentSettingsApiResponse> {
  try {
    const response = await fetch(`${AGENT_API_BASE}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(settingsApiErrorMessage(detail, response.status));
    }
    return response.json();
  } catch (error) {
    if (error instanceof Error && (
      error.message.startsWith('Settings endpoint')
      || error.message.startsWith('Agent API returned')
      || error.message.startsWith('Cannot reach the FAIR2WISE agent backend')
    )) {
      throw error;
    }
    throw new Error(agentNetworkErrorMessage(AGENT_API_BASE, error));
  }
}

export async function queryLiveAgent(message: string, signal?: AbortSignal): Promise<AgentChatResponse> {
  return queryLiveAgentWithHistory(message, signal, []);
}

export async function queryLiveAgentWithHistory(
  message: string,
  signal?: AbortSignal,
  messages: AgentChatHistoryMessage[] = [],
): Promise<AgentChatResponse> {
  const response = await fetch(`${AGENT_API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, messages }),
    signal,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

export async function searchPublications(
  query: string,
  options: PublicationSearchOptions = {},
  signal?: AbortSignal,
): Promise<PublicationSearchResponse> {
  const response = await fetch(`${AGENT_API_BASE}/publications/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      max_results: options.maxResults ?? 20,
      include_external: options.includeExternal ?? false,
    }),
    signal,
  });

  if (!response.ok) {
    const detail = await response.text();
    if (response.status === 404) {
      throw new Error('Paper search endpoint not found. Restart the FAIR2WISE agent backend so the new API route is loaded.');
    }
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

function parseSseBlock(block: string): { event: string; data: unknown } | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return null;
  return { event, data: JSON.parse(dataLines.join('\n')) };
}

export async function queryLiveAgentStream(
  message: string,
  onProgress: (event: ChatProgressEvent) => void,
  signal?: AbortSignal,
  messages: AgentChatHistoryMessage[] = [],
): Promise<AgentChatResponse> {
  let sawStreamEvent = false;

  try {
    const response = await fetch(`${AGENT_API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, messages }),
      signal,
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Agent API returned ${response.status}`);
    }
    if (!response.body) {
      throw new Error('Agent API returned no stream body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

      const blocks = buffer.split(/\n\n/);
      buffer = blocks.pop() ?? '';

      for (const block of blocks) {
        const parsed = parseSseBlock(block.trim());
        if (!parsed) continue;
        sawStreamEvent = true;

        if (parsed.event === 'progress') {
          onProgress(parsed.data as ChatProgressEvent);
        } else if (parsed.event === 'complete') {
          return parsed.data as AgentChatResponse;
        } else if (parsed.event === 'error') {
          const data = parsed.data as Partial<AgentChatResponse> & { message?: string };
          throw new Error(data.answer || data.message || data.status || 'Agent stream failed');
        }
      }

      if (done) break;
    }

    throw new Error('Agent stream ended without a complete event');
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }
    if (!sawStreamEvent) {
      return queryLiveAgentWithHistory(message, signal, messages);
    }
    throw error;
  }
}

export async function fetchLiveGraph(): Promise<GraphPayload> {
  const response = await fetch(`${AGENT_API_BASE}/graph`);

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

export async function fetchGraphNodeDetail(
  nodeId: string,
  jsonGraphPath?: string,
): Promise<LiveGraphNode> {
  const params = new URLSearchParams();
  if (jsonGraphPath) {
    params.set('json_graph_path', jsonGraphPath);
  }
  const query = params.toString();
  const response = await fetch(
    `${AGENT_API_BASE}/graph/node/${encodeURIComponent(nodeId)}${query ? `?${query}` : ''}`,
  );

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}
