export interface LinkedCodeSnippet {
  id: string;
  label?: string;
  function_name?: string | null;
  code_language?: string | null;
  code_snippet: string;
  publications?: PublicationInfo[];
  source_type?: string | null;
  repo_url?: string | null;
  repo_owner?: string | null;
  repo_name?: string | null;
  repo_commit_sha?: string | null;
  source_file_path?: string | null;
  source_file_url?: string | null;
  source_start_line?: number | null;
  source_end_line?: number | null;
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
  graph_id?: string | null;
  graph_label?: string | null;
  formula?: string | null;
  source_papers?: string[];
  properties?: Array<Record<string, unknown>>;
  extra_fields?: Record<string, unknown>;
  source_type?: string | null;
  repo_url?: string | null;
  repo_owner?: string | null;
  repo_name?: string | null;
  repo_commit_sha?: string | null;
  source_file_path?: string | null;
  source_file_url?: string | null;
  source_start_line?: number | null;
  source_end_line?: number | null;
  repository_license?: string | null;
}

export interface LiveGraphEdge {
  source: string;
  target: string;
  predicate: string;
}

export type GraphRelationshipAction = 'add' | 'remove';

export interface GraphRelationshipUpdate {
  action: GraphRelationshipAction;
  source: string;
  predicate: string;
  target: string;
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

export interface GraphNodeSearchResult {
  node: LiveGraphNode;
  score: number;
}

export interface GraphNodeSearchResponse {
  query: string;
  retrieval_backend: 'semantic' | 'lexical' | string;
  results: GraphNodeSearchResult[];
}

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

export interface PendingCandidate {
  index?: number;
  recommended?: boolean;
  unavailable?: boolean;
  title: string;
  doi?: string | null;
  publication_year?: number | null;
  abstract?: string;
  source_paper?: string | null;
  score?: number;
  repository?: string | null;
}

export type PendingActionKind = 'download' | 'extraction';

export interface PendingAction {
  kind: PendingActionKind;
  prompt?: string;
  reason?: string;
  round?: number;
  papers?: PendingCandidate[];
  candidate?: PendingCandidate | null;
  alternatives?: PendingCandidate[];
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
  pending?: PendingAction | null;
  turn_id?: string | null;
  orchestration?: {
    action: string;
    agent: string;
    reason: string;
    state: string;
  } | null;
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

export interface AgentSessionResetResponse {
  status: 'reset' | string;
  session_memory: string;
  session_memory_has_context: boolean;
}

export interface ChatProgressEvent {
  phase: string;
  message: string;
  turn_id?: string;
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
  action?: string;
  agent?: string;
  state?: string;
  mode?: 'full' | 'targeted' | string;
  max_pages?: number | null;
  skipped?: number;
  failed?: number;
  term_count?: number;
  processed_files?: number;
  processed_pages_total?: number;
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

export const AGENT_API_BASE = (import.meta.env.VITE_F2W_AGENT_API_URL || 'http://127.0.0.1:8090').replace(/\/$/, '');

export interface AgentSettingsApiResponse {
  backend: 'cborg' | 'ollama';
  model: string;
  graph_source: 'splash' | 'json';
  workflow_mode: 'deterministic' | 'agentic';
  extraction_mode: 'full' | 'targeted';
  targeted_max_pages: number;
  json_graph_path: string | null;
  json_graph_paths?: string[];
  kg_query_max_nodes?: number;
  kg_query_hops?: number;
  source_rag?: boolean;
  use_live_tiled?: boolean;
  tiled_uri?: string | null;
  tiled_api_key_set?: boolean;
  tiled_status?: string;
  tiled_error?: string | null;
  available_json_graphs: string[];
  /** Richer per-graph metadata (path, graph_id, label). Added by multi-kg changes. */
  available_graphs?: { path: string; graph_id: string; label: string }[];
  /** IDs of currently selected graphs. */
  selected_graph_ids?: string[];
  available_cborg_models: string[];
  default_ollama_model: string;
}

export interface AgentSettingsApiUpdate {
  backend?: 'cborg' | 'ollama';
  model?: string;
  graph_source?: 'splash' | 'json';
  workflow_mode?: 'deterministic' | 'agentic';
  extraction_mode?: 'full' | 'targeted';
  targeted_max_pages?: number;
  json_graph_path?: string | null;
  json_graph_paths?: string[];
  kg_query_max_nodes?: number;
  kg_query_hops?: number;
  source_rag?: boolean;
  use_live_tiled?: boolean;
  tiled_uri?: string | null;
}

import { agentNetworkErrorMessage, settingsApiErrorMessage } from '../agentApiErrors';
import { loadAgentSettings } from '../agentSettings';

export function newClientTurnId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function uiSettingsSnapshot() {
  const settings = loadAgentSettings();
  return {
    backend: settings.backend,
    model: settings.model,
    graphSource: settings.graphSource,
    workflowMode: settings.workflowMode,
    extractionMode: settings.extractionMode,
    targetedMaxPages: settings.targetedMaxPages,
    jsonGraphPath: settings.jsonGraphPath,
    jsonGraphPaths: settings.jsonGraphPaths,
    selectedGraphIds: settings.selectedGraphIds,
    kgQueryMaxNodes: settings.kgQueryMaxNodes,
    kgQueryHops: settings.kgQueryHops,
    sourceRag: settings.sourceRag,
    useLiveTiled: settings.useLiveTiled,
    tiledUri: settings.tiledUri,
  };
}

function chatRequestBody(
  message: string,
  messages: AgentChatHistoryMessage[] = [],
  sessionId?: string,
  clientTurnId?: string,
) {
  const settings = loadAgentSettings();
  return {
    message,
    messages: nonEmptyHistory(messages),
    session_id: sessionId,
    client_turn_id: clientTurnId,
    ui_settings: uiSettingsSnapshot(),
    source_rag: settings.sourceRag,
    use_live_tiled: settings.useLiveTiled,
  };
}

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

function nonEmptyHistory(messages: AgentChatHistoryMessage[]): AgentChatHistoryMessage[] {
  return messages.filter(item => item.content.trim().length > 0);
}

export async function queryLiveAgentWithHistory(
  message: string,
  signal?: AbortSignal,
  messages: AgentChatHistoryMessage[] = [],
  sessionId?: string,
  clientTurnId?: string,
): Promise<AgentChatResponse> {
  const response = await fetch(`${AGENT_API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(chatRequestBody(message, messages, sessionId, clientTurnId ?? newClientTurnId())),
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

export async function resetAgentSession(
  signal?: AbortSignal,
  sessionId?: string,
): Promise<AgentSessionResetResponse> {
  const response = await fetch(`${AGENT_API_BASE}/session/reset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: sessionId ? JSON.stringify({ session_id: sessionId }) : undefined,
    signal,
  });

  if (!response.ok) {
    const detail = await response.text();
    if (response.status === 404) {
      throw new Error('Session reset endpoint not found. Restart the FAIR2WISE agent backend so the new API route is loaded.');
    }
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

export async function deleteAgentSession(sessionId: string): Promise<void> {
  const response = await fetch(
    `${AGENT_API_BASE}/session/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }
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
  sessionId?: string,
): Promise<AgentChatResponse> {
  let sawStreamEvent = false;
  const clientTurnId = newClientTurnId();

  try {
    const response = await fetch(`${AGENT_API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(chatRequestBody(message, messages, sessionId, clientTurnId)),
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
      return queryLiveAgentWithHistory(message, signal, messages, sessionId, clientTurnId);
    }
    throw error;
  }
}

export async function queryAgentActionStream(
  decision: 'yes' | 'no',
  kind: PendingActionKind,
  onProgress: (event: ChatProgressEvent) => void,
  signal?: AbortSignal,
  candidateIndex?: number,
  sessionId?: string,
  parentTurnId?: string,
): Promise<AgentChatResponse> {
  let sawStreamEvent = false;
  const clientTurnId = newClientTurnId();
  const payload: Record<string, unknown> = {
    decision,
    kind,
    session_id: sessionId,
    client_turn_id: clientTurnId,
    ui_settings: uiSettingsSnapshot(),
  };
  if (parentTurnId) payload.parent_turn_id = parentTurnId;
  if (candidateIndex !== undefined) {
    payload.candidate_index = candidateIndex;
  }

  const runFallback = async (): Promise<AgentChatResponse> => {
    const response = await fetch(`${AGENT_API_BASE}/chat/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Agent API returned ${response.status}`);
    }
    return response.json();
  };

  try {
    const response = await fetch(`${AGENT_API_BASE}/chat/action/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
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
      return runFallback();
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

export async function searchGraphNodes(
  query: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<GraphNodeSearchResponse> {
  const response = await fetch(`${AGENT_API_BASE}/graph/nodes/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
    signal,
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

export async function fetchGraphNeighborhood(
  nodeId: string,
  hops = 3,
): Promise<GraphPayload> {
  const params = new URLSearchParams();
  if (hops > 0) params.set('hops', String(hops));
  const query = params.toString();
  const response = await fetch(
    `${AGENT_API_BASE}/graph/neighborhood/${encodeURIComponent(nodeId)}${query ? `?${query}` : ''}`,
  );
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

export interface GraphNodeUpdatePayload {
  label?: string;
  type?: string;
  description?: string;
  code_snippet?: string;
  publications?: PublicationInfo[];
  linked_code_snippets?: Array<{
    id?: string;
    label?: string;
    function_name?: string | null;
    code_language?: string | null;
    code_snippet?: string;
    _action?: 'upsert' | 'unlink';
  }>;
  relationship_updates?: GraphRelationshipUpdate[];
}

export async function updateGraphNode(
  nodeId: string,
  update: GraphNodeUpdatePayload,
): Promise<LiveGraphNode> {
  const response = await fetch(
    `${AGENT_API_BASE}/graph/node/${encodeURIComponent(nodeId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    },
  );

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}
