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

import { agentNetworkErrorMessage, parseAgentApiError, settingsApiErrorMessage } from '../agentApiErrors';
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

/** One parsed server-sent-event block. `id` is the optional `id:` field (for `Last-Event-ID` resume). */
export interface SseBlock {
  event: string;
  data: unknown;
  id?: string;
}

/** Parse one SSE block (text between blank lines); `null` when it carries no `data:` lines. */
export function parseSseBlock(block: string): SseBlock | null {
  let event = 'message';
  let id: string | undefined;
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    } else if (line.startsWith('id:')) {
      id = line.slice(3).trim();
    }
  }

  if (dataLines.length === 0) return null;
  const parsed: SseBlock = { event, data: JSON.parse(dataLines.join('\n')) };
  if (id !== undefined) parsed.id = id;
  return parsed;
}

/**
 * Read an SSE response body to its end, calling `onBlock` for each parsed block.
 * Reusable by any stream consumer (chat uses its own loop; the job stream uses this).
 * Return `true` from `onBlock` to stop early.
 *
 * @throws Error when the response has no body.
 */
export async function readSseStream(
  response: Response,
  onBlock: (block: SseBlock) => boolean | void,
): Promise<void> {
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
    for (const raw of blocks) {
      const parsed = parseSseBlock(raw.trim());
      if (parsed && onBlock(parsed) === true) {
        await reader.cancel();
        return;
      }
    }
    if (done) {
      const parsed = parseSseBlock(buffer.trim());
      if (parsed) onBlock(parsed);
      return;
    }
  }
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

/**
 * Query string carrying the graph source (`json:<name>` / `tiled:<graph>`), or `''` for the default graph.
 * The default graph sends no `source` at all, so default requests are byte-identical to before.
 */
export function graphSourceQuery(source?: string | null): string {
  return source ? `source=${encodeURIComponent(source)}` : '';
}

function withQuery(url: string, ...parts: string[]): string {
  const query = parts.filter(Boolean).join('&');
  return query ? `${url}?${query}` : url;
}

async function graphErrorText(response: Response): Promise<string> {
  return parseAgentApiError(await response.text(), response.status);
}

export async function fetchLiveGraph(source?: string | null): Promise<GraphPayload> {
  const response = await fetch(withQuery(`${AGENT_API_BASE}/graph`, graphSourceQuery(source)));

  if (!response.ok) {
    if (source) throw new Error(await graphErrorText(response));
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

export async function searchGraphNodes(
  query: string,
  limit = 10,
  signal?: AbortSignal,
  source?: string | null,
): Promise<GraphNodeSearchResponse> {
  const response = await fetch(withQuery(`${AGENT_API_BASE}/graph/nodes/search`, graphSourceQuery(source)), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
    signal,
  });

  if (!response.ok) {
    if (source) throw new Error(await graphErrorText(response));
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}

/** Response header the server sets when a graph read was cut by its node cap (additive; the body is unchanged). */
export const GRAPH_TRUNCATED_HEADER = 'X-F2W-Graph-Truncated';

export interface GraphNeighborhoodResult {
  graph: GraphPayload;
  /** True when the server's neighborhood node cap cut the result (`X-F2W-Graph-Truncated: true`). */
  truncated: boolean;
}

export async function fetchGraphNeighborhoodResult(
  nodeId: string,
  hops = 3,
  source?: string | null,
): Promise<GraphNeighborhoodResult> {
  const params = new URLSearchParams();
  if (hops > 0) params.set('hops', String(hops));
  if (source) params.set('source', source);
  const query = params.toString();
  const response = await fetch(
    `${AGENT_API_BASE}/graph/neighborhood/${encodeURIComponent(nodeId)}${query ? `?${query}` : ''}`,
  );
  if (!response.ok) {
    if (source) throw new Error(await graphErrorText(response));
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }
  const truncated = response.headers?.get(GRAPH_TRUNCATED_HEADER)?.toLowerCase() === 'true';
  return { graph: await response.json(), truncated };
}

export async function fetchGraphNeighborhood(
  nodeId: string,
  hops = 3,
  source?: string | null,
): Promise<GraphPayload> {
  return (await fetchGraphNeighborhoodResult(nodeId, hops, source)).graph;
}

export async function fetchGraphNodeDetail(
  nodeId: string,
  jsonGraphPath?: string,
  source?: string | null,
): Promise<LiveGraphNode> {
  const params = new URLSearchParams();
  if (jsonGraphPath) {
    params.set('json_graph_path', jsonGraphPath);
  }
  if (source) {
    params.set('source', source);
  }
  const query = params.toString();
  const response = await fetch(
    `${AGENT_API_BASE}/graph/node/${encodeURIComponent(nodeId)}${query ? `?${query}` : ''}`,
  );

  if (!response.ok) {
    if (source) throw new Error(await graphErrorText(response));
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

/**
 * `PATCH /graph/node/{id}`. `source` is the graph source the viewer shows (`tiled:<corpus>`): the server then
 * edits that corpus (owner decision 2026-11-09: Save edits the corpus shown in the graph view). Without it the
 * active publication of the editable default is edited and no `source` parameter is sent.
 */
export async function updateGraphNode(
  nodeId: string,
  update: GraphNodeUpdatePayload,
  source?: string | null,
): Promise<LiveGraphNode> {
  const response = await fetch(
    withQuery(`${AGENT_API_BASE}/graph/node/${encodeURIComponent(nodeId)}`, graphSourceQuery(source)),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    },
  );

  if (!response.ok) {
    if (source) throw new Error(await graphErrorText(response));
    const detail = await response.text();
    throw new Error(detail || `Agent API returned ${response.status}`);
  }

  return response.json();
}
