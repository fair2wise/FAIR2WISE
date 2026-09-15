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
  jsonGraphPaths: string[];
  kgQueryMaxNodes: number;
  kgQueryHops: number;
}

export interface AgentSettingsResponse {
  backend: AgentBackend;
  model: string;
  graph_source: 'splash' | 'json';
  workflow_mode: AgentWorkflowMode;
  extraction_mode: AgentExtractionMode;
  targeted_max_pages: number;
  json_graph_path: string | null;
  json_graph_paths?: string[];
  kg_query_max_nodes?: number;
  kg_query_hops?: number;
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

export const DEFAULT_JSON_GRAPH_PATHS = [
  'storage/kg/matkg_rsoxs_v1.json',
  'storage/kg/matkg_bl1101_v1.json',
] as const;
export const XRAY_DEMO_GRAPH = 'storage/kg/matkg_xray_papers_cborg_chat.json';
export const KG_QUERY_MAX_NODES_PRESETS = [50, 100, 250, 500, 1000] as const;
export const MAX_KG_QUERY_HOPS = 20;
export const KG_QUERY_HOPS_PRESETS = Array.from(
  { length: MAX_KG_QUERY_HOPS },
  (_, index) => index + 1,
);

export function clampKgQueryHops(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_KG_QUERY_HOPS, Math.max(1, Math.floor(value)));
}

export const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  backend: 'cborg',
  model: DEFAULT_CBORG_MODEL,
  graphSource: 'splash_links',
  workflowMode: 'agentic',
  extractionMode: 'targeted',
  targetedMaxPages: 6,
  jsonGraphPath: DEFAULT_JSON_GRAPH_PATHS[0],
  jsonGraphPaths: [...DEFAULT_JSON_GRAPH_PATHS],
  kgQueryMaxNodes: 100,
  kgQueryHops: 1,
};

function isXrayDemo(path: string): boolean {
  return path.replace(/\\/g, '/').endsWith('matkg_xray_papers_cborg_chat.json');
}

function normalizeJsonGraphPaths(value: unknown, fallbackPath?: string): string[] {
  const fromList = Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : [];
  if (fromList.length) return Array.from(new Set(fromList));
  if (fallbackPath && !isXrayDemo(fallbackPath)) return [fallbackPath];
  return [...DEFAULT_JSON_GRAPH_PATHS];
}

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
    const storedPath = parsed.jsonGraphPath || '';
    const jsonGraphPaths = normalizeJsonGraphPaths(
      parsed.jsonGraphPaths,
      storedPath && !isXrayDemo(storedPath) ? storedPath : undefined,
    );
    const storedJsonPath = jsonGraphPaths.includes(storedPath)
      ? storedPath
      : (jsonGraphPaths[0] || DEFAULT_AGENT_SETTINGS.jsonGraphPath);
    return {
      backend,
      model: normalizeModel(parsed.model, backend),
      graphSource: parsed.graphSource === 'json' ? 'json' : 'splash_links',
      workflowMode: parsed.workflowMode === 'deterministic' ? 'deterministic' : DEFAULT_AGENT_SETTINGS.workflowMode,
      extractionMode: normalizeExtractionMode(parsed.extractionMode),
      targetedMaxPages: typeof parsed.targetedMaxPages === 'number' && parsed.targetedMaxPages > 0
        ? parsed.targetedMaxPages
        : DEFAULT_AGENT_SETTINGS.targetedMaxPages,
      jsonGraphPath: storedJsonPath,
      jsonGraphPaths,
      kgQueryMaxNodes: typeof parsed.kgQueryMaxNodes === 'number'
        ? Math.min(1000, Math.max(10, parsed.kgQueryMaxNodes))
        : DEFAULT_AGENT_SETTINGS.kgQueryMaxNodes,
      kgQueryHops: typeof parsed.kgQueryHops === 'number'
        ? clampKgQueryHops(parsed.kgQueryHops)
        : DEFAULT_AGENT_SETTINGS.kgQueryHops,
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
    json_graph_paths: settings.graphSource === 'json' ? settings.jsonGraphPaths : [],
    kg_query_max_nodes: settings.kgQueryMaxNodes,
    kg_query_hops: settings.kgQueryHops,
  };
}

export function settingsFromApiResponse(response: AgentSettingsResponse): AgentSettings {
  const graphSource = graphSourceFromApi(response.graph_source);
  const backend: AgentBackend = response.backend === 'ollama' ? 'ollama' : 'cborg';
  const available = response.available_json_graphs ?? [];
  const jsonGraphPaths = normalizeJsonGraphPaths(
    response.json_graph_paths,
    response.json_graph_path || undefined,
  );
  const jsonGraphPath = response.json_graph_path
    || jsonGraphPaths[0]
    || available.find(path => !isXrayDemo(path))
    || DEFAULT_AGENT_SETTINGS.jsonGraphPath;
  return {
    backend,
    model: normalizeModel(response.model, backend),
    graphSource,
    workflowMode: response.workflow_mode === 'deterministic' ? 'deterministic' : DEFAULT_AGENT_SETTINGS.workflowMode,
    extractionMode: normalizeExtractionMode(response.extraction_mode),
    targetedMaxPages: response.targeted_max_pages || DEFAULT_AGENT_SETTINGS.targetedMaxPages,
    jsonGraphPath,
    jsonGraphPaths: jsonGraphPaths.includes(jsonGraphPath)
      ? jsonGraphPaths
      : [jsonGraphPath, ...jsonGraphPaths],
    kgQueryMaxNodes: typeof response.kg_query_max_nodes === 'number'
      ? Math.min(1000, Math.max(10, response.kg_query_max_nodes))
      : DEFAULT_AGENT_SETTINGS.kgQueryMaxNodes,
    kgQueryHops: typeof response.kg_query_hops === 'number'
      ? clampKgQueryHops(response.kg_query_hops)
      : DEFAULT_AGENT_SETTINGS.kgQueryHops,
  };
}

export function settingsEqual(a: AgentSettings, b: AgentSettings): boolean {
  return a.backend === b.backend
    && a.model === b.model
    && a.graphSource === b.graphSource
    && a.workflowMode === b.workflowMode
    && a.extractionMode === b.extractionMode
    && a.targetedMaxPages === b.targetedMaxPages
    && a.jsonGraphPath === b.jsonGraphPath
    && a.jsonGraphPaths.join('|') === b.jsonGraphPaths.join('|')
    && a.kgQueryMaxNodes === b.kgQueryMaxNodes
    && a.kgQueryHops === b.kgQueryHops;
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
