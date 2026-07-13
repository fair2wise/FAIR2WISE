export type AgentBackend = 'cborg' | 'ollama';
export type AgentGraphSource = 'splash_links' | 'json';
export type AgentWorkflowMode = 'deterministic' | 'agentic';
export type AgentExtractionMode = 'full' | 'targeted';

export interface AgentSettings {
  backend: AgentBackend;
  model: string;
  graphSource: AgentGraphSource;
  workflowMode: AgentWorkflowMode;
  extractionMode: AgentExtractionMode;
  targetedMaxPages: number;
  jsonGraphPath: string;
}

export interface AgentSettingsResponse {
  backend: AgentBackend;
  model: string;
  graph_source: 'splash' | 'json';
  workflow_mode: AgentWorkflowMode;
  extraction_mode: AgentExtractionMode;
  targeted_max_pages: number;
  json_graph_path: string | null;
  available_json_graphs: string[];
  available_cborg_models: string[];
  default_ollama_model: string;
}

const STORAGE_KEY = 'fair2wise-agent-settings-v1';

const CBORG_MODEL_ALIASES: Record<string, string> = {
  'google/gemini-flash': 'gemini-flash',
  'google/gemini-flash-lite': 'gemini-2.5-flash-lite',
  'google/gemini-pro': 'gemini-pro',
  'google/gemini-flash-high': 'gemini-flash-high',
  'google/gemini-pro-high': 'gemini-pro-high',
  'gemini-flash-lite': 'gemini-2.5-flash-lite',
};

export function normalizeCborgModel(model: string): string {
  const cleaned = model.trim();
  return CBORG_MODEL_ALIASES[cleaned] || cleaned;
}

export const DEFAULT_CBORG_MODEL = 'lbl/cborg-chat';
export const DEFAULT_OLLAMA_MODEL = 'deepseek-r1:70b';

export const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  backend: 'cborg',
  model: DEFAULT_CBORG_MODEL,
  graphSource: 'splash_links',
  workflowMode: 'agentic',
  extractionMode: 'targeted',
  targetedMaxPages: 6,
  jsonGraphPath: 'storage/kg/matkg_xray_papers_cborg_chat.json',
};

function normalizeModel(value: unknown, backend: AgentBackend): string {
  if (typeof value === 'string' && value.trim()) {
    const model = value.trim();
    return backend === 'cborg' ? normalizeCborgModel(model) : model;
  }
  return backend === 'ollama' ? DEFAULT_OLLAMA_MODEL : DEFAULT_CBORG_MODEL;
}

function normalizeExtractionMode(value: unknown): AgentExtractionMode {
  if (value === 'full') return 'full';
  if (value === 'targeted') return 'targeted';
  return DEFAULT_AGENT_SETTINGS.extractionMode;
}

export function loadAgentSettings(): AgentSettings {
  if (typeof window === 'undefined') return { ...DEFAULT_AGENT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_AGENT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AgentSettings>;
    const backend: AgentBackend = parsed.backend === 'ollama' ? 'ollama' : 'cborg';
    return {
      backend,
      model: normalizeModel(parsed.model, backend),
      graphSource: parsed.graphSource === 'json' ? 'json' : 'splash_links',
      workflowMode: parsed.workflowMode === 'deterministic' ? 'deterministic' : DEFAULT_AGENT_SETTINGS.workflowMode,
      extractionMode: normalizeExtractionMode(parsed.extractionMode),
      targetedMaxPages: typeof parsed.targetedMaxPages === 'number' && parsed.targetedMaxPages > 0
        ? parsed.targetedMaxPages
        : DEFAULT_AGENT_SETTINGS.targetedMaxPages,
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
    model: settings.backend === 'cborg'
      ? normalizeCborgModel(settings.model)
      : settings.model,
    graph_source: graphSourceToApi(settings.graphSource),
    workflow_mode: settings.workflowMode,
    extraction_mode: settings.extractionMode,
    targeted_max_pages: settings.targetedMaxPages,
    json_graph_path: settings.graphSource === 'json' ? settings.jsonGraphPath : null,
  };
}

export function settingsFromApiResponse(response: AgentSettingsResponse): AgentSettings {
  const graphSource = graphSourceFromApi(response.graph_source);
  const backend: AgentBackend = response.backend === 'ollama' ? 'ollama' : 'cborg';
  const available = response.available_json_graphs ?? [];
  const jsonGraphPath = response.json_graph_path
    || available[0]
    || DEFAULT_AGENT_SETTINGS.jsonGraphPath;
  return {
    backend,
    model: normalizeModel(response.model, backend),
    graphSource,
    workflowMode: response.workflow_mode === 'deterministic' ? 'deterministic' : DEFAULT_AGENT_SETTINGS.workflowMode,
    extractionMode: normalizeExtractionMode(response.extraction_mode),
    targetedMaxPages: response.targeted_max_pages || DEFAULT_AGENT_SETTINGS.targetedMaxPages,
    jsonGraphPath,
  };
}

export function settingsEqual(a: AgentSettings, b: AgentSettings): boolean {
  return a.backend === b.backend
    && a.model === b.model
    && a.graphSource === b.graphSource
    && a.workflowMode === b.workflowMode
    && a.extractionMode === b.extractionMode
    && a.targetedMaxPages === b.targetedMaxPages
    && a.jsonGraphPath === b.jsonGraphPath;
}

export function defaultModelForBackend(
  backend: AgentBackend,
  options?: Pick<AgentSettingsResponse, 'default_ollama_model' | 'available_cborg_models'>,
): string {
  if (backend === 'ollama') {
    return options?.default_ollama_model?.trim() || DEFAULT_OLLAMA_MODEL;
  }
  return options?.available_cborg_models?.[0]?.trim() || DEFAULT_CBORG_MODEL;
}
