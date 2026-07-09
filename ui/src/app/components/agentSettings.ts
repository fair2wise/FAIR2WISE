export type AgentBackend = 'cborg' | 'ollama';
export type AgentGraphSource = 'splash_links' | 'json';
export type AgentWorkflowMode = 'deterministic' | 'agentic';

export interface AgentSettings {
  backend: AgentBackend;
  graphSource: AgentGraphSource;
  workflowMode: AgentWorkflowMode;
  jsonGraphPath: string;
}

export interface AgentSettingsResponse {
  backend: AgentBackend;
  graph_source: 'splash' | 'json';
  workflow_mode: AgentWorkflowMode;
  json_graph_path: string | null;
  available_json_graphs: string[];
}

const STORAGE_KEY = 'fair2wise-agent-settings-v1';

export const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  backend: 'cborg',
  graphSource: 'splash_links',
  workflowMode: 'deterministic',
  jsonGraphPath: 'storage/kg/matkg_xray_papers_cborg_chat.json',
};

export function loadAgentSettings(): AgentSettings {
  if (typeof window === 'undefined') return { ...DEFAULT_AGENT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_AGENT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AgentSettings>;
    return {
      backend: parsed.backend === 'ollama' ? 'ollama' : 'cborg',
      graphSource: parsed.graphSource === 'json' ? 'json' : 'splash_links',
      workflowMode: parsed.workflowMode === 'agentic' ? 'agentic' : 'deterministic',
      jsonGraphPath: parsed.jsonGraphPath || DEFAULT_AGENT_SETTINGS.jsonGraphPath,
    };
  } catch {
    return { ...DEFAULT_AGENT_SETTINGS };
  }
}

export function saveAgentSettings(settings: AgentSettings): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

export function graphSourceToApi(source: AgentGraphSource): 'splash' | 'json' {
  return source === 'json' ? 'json' : 'splash';
}

export function graphSourceFromApi(source: string): AgentGraphSource {
  return source === 'json' ? 'json' : 'splash_links';
}

export function settingsToApiPayload(settings: AgentSettings) {
  return {
    backend: settings.backend,
    graph_source: graphSourceToApi(settings.graphSource),
    workflow_mode: settings.workflowMode,
    json_graph_path: settings.graphSource === 'json' ? settings.jsonGraphPath : null,
  };
}

export function settingsFromApiResponse(response: AgentSettingsResponse): AgentSettings {
  const graphSource = graphSourceFromApi(response.graph_source);
  const available = response.available_json_graphs ?? [];
  const jsonGraphPath = response.json_graph_path
    || available[0]
    || DEFAULT_AGENT_SETTINGS.jsonGraphPath;
  return {
    backend: response.backend === 'ollama' ? 'ollama' : 'cborg',
    graphSource,
    workflowMode: response.workflow_mode === 'agentic' ? 'agentic' : 'deterministic',
    jsonGraphPath,
  };
}

export function settingsEqual(a: AgentSettings, b: AgentSettings): boolean {
  return a.backend === b.backend
    && a.graphSource === b.graphSource
    && a.workflowMode === b.workflowMode
    && a.jsonGraphPath === b.jsonGraphPath;
}
