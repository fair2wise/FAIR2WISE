import { describe, expect, it, beforeEach } from 'vitest';
import {
  DEFAULT_AGENT_SETTINGS,
  DEFAULT_CBORG_MODEL,
  DEFAULT_JSON_GRAPH_PATHS,
  DEFAULT_OLLAMA_MODEL,
  KG_QUERY_HOPS_PRESETS,
  MAX_KG_QUERY_HOPS,
  clampKgQueryHops,
  defaultModelForBackend,
  graphSourceFromApi,
  graphSourceToApi,
  loadAgentSettings,
  loadCachedGraphList,
  normalizeCborgModel,
  saveAgentSettings,
  saveCachedGraphList,
  settingsEqual,
  settingsFromApiResponse,
  settingsToApiPayload,
  type AgentSettings,
} from './agentSettings';

const baseResponse = {
  backend: 'cborg' as const,
  model: DEFAULT_CBORG_MODEL,
  graph_source: 'splash' as const,
  workflow_mode: 'deterministic' as const,
  extraction_mode: 'targeted' as const,
  targeted_max_pages: 6,
  json_graph_path: null,
  json_graph_paths: [] as string[],
  kg_query_max_nodes: 100,
  kg_query_hops: 1,
  available_json_graphs: [] as string[],
  available_cborg_models: [DEFAULT_CBORG_MODEL, 'google/gemini-flash'],
  default_ollama_model: DEFAULT_OLLAMA_MODEL,
};

describe('agentSettings', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('maps splash_links to splash for the API', () => {
    expect(graphSourceToApi('splash_links')).toBe('splash');
    expect(graphSourceToApi('json')).toBe('json');
  });

  it('maps splash API values back to splash_links', () => {
    expect(graphSourceFromApi('splash')).toBe('splash_links');
    expect(graphSourceFromApi('json')).toBe('json');
  });

  it('normalizes legacy google/gemini model ids', () => {
    expect(normalizeCborgModel('google/gemini-flash')).toBe('gemini-flash');
    expect(normalizeCborgModel('google/gemini-flash-lite')).toBe('gemini-2.5-flash-lite');
    expect(normalizeCborgModel('google/gemini-pro')).toBe('gemini-pro');
    expect(normalizeCborgModel('gemini-flash-lite')).toBe('gemini-2.5-flash-lite');
  });

  it('builds splash payload without json_graph_path', () => {
    const settings: AgentSettings = {
      backend: 'cborg',
      model: DEFAULT_CBORG_MODEL,
      graphSource: 'splash_links',
      workflowMode: 'deterministic',
      extractionMode: 'full',
      targetedMaxPages: 6,
      jsonGraphPath: 'storage/kg/ignored.json',
      jsonGraphPaths: ['storage/kg/ignored.json'],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: 100,
      kgQueryHops: 1,
      sourceRag: false,
      useLiveTiled: false,
      tiledUri: '',
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'cborg',
      model: DEFAULT_CBORG_MODEL,
      graph_source: 'splash',
      workflow_mode: 'deterministic',
      extraction_mode: 'full',
      targeted_max_pages: 6,
      json_graph_path: null,
      json_graph_paths: [],
      kg_query_max_nodes: 100,
      kg_query_hops: 1,
      source_rag: false,
      use_live_tiled: false,
      tiled_uri: null,
    });
  });

  it('builds json payload with selected graph paths', () => {
    const settings: AgentSettings = {
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graphSource: 'json',
      workflowMode: 'agentic',
      extractionMode: 'targeted',
      targetedMaxPages: 4,
      jsonGraphPath: 'storage/kg/alpha.json',
      jsonGraphPaths: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: 250,
      kgQueryHops: 2,
      sourceRag: true,
      useLiveTiled: false,
      tiledUri: '',
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graph_source: 'json',
      workflow_mode: 'agentic',
      extraction_mode: 'targeted',
      targeted_max_pages: 4,
      json_graph_path: 'storage/kg/alpha.json',
      json_graph_paths: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
      kg_query_max_nodes: 250,
      kg_query_hops: 2,
      source_rag: true,
      use_live_tiled: false,
      tiled_uri: null,
    });
  });

  it('hydrates settings from API response', () => {
    const settings = settingsFromApiResponse({
      ...baseResponse,
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graph_source: 'json',
      workflow_mode: 'agentic',
      extraction_mode: 'targeted',
      targeted_max_pages: 4,
      json_graph_path: 'storage/kg/alpha.json',
      json_graph_paths: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
      kg_query_max_nodes: 250,
      kg_query_hops: 2,
      source_rag: true,
      available_json_graphs: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
    });
    expect(settings).toEqual({
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graphSource: 'json',
      workflowMode: 'agentic',
      extractionMode: 'targeted',
      targetedMaxPages: 4,
      jsonGraphPath: 'storage/kg/alpha.json',
      jsonGraphPaths: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: 250,
      kgQueryHops: 2,
      sourceRag: true,
      useLiveTiled: false,
      tiledUri: '',
    });
  });

  it('defaults API hydration to rsoxs + bl1101, not x-ray', () => {
    const settings = settingsFromApiResponse({
      ...baseResponse,
      graph_source: 'json',
    });
    expect(settings.jsonGraphPaths).toEqual([...DEFAULT_JSON_GRAPH_PATHS]);
    expect(settings.jsonGraphPath).toBe(DEFAULT_JSON_GRAPH_PATHS[0]);
    expect(settings.jsonGraphPaths.join(' ')).not.toContain('matkg_xray_papers_cborg_chat');
    expect(settings.sourceRag).toBe(false);
  });

  it('persists settings in localStorage', () => {
    const settings: AgentSettings = {
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graphSource: 'json',
      workflowMode: 'agentic',
      extractionMode: 'targeted',
      targetedMaxPages: 4,
      jsonGraphPath: 'storage/kg/custom.json',
      jsonGraphPaths: ['storage/kg/custom.json'],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: 50,
      kgQueryHops: 3,
      sourceRag: true,
      useLiveTiled: false,
      tiledUri: '',
    };
    saveAgentSettings(settings);
    expect(loadAgentSettings()).toEqual(settings);
  });

  it('falls back to defaults for invalid stored settings', () => {
    localStorage.setItem('fair2wise-agent-settings-v1', '{"backend":"bad"}');
    expect(loadAgentSettings()).toEqual({
      backend: 'cborg',
      model: DEFAULT_CBORG_MODEL,
      graphSource: 'splash_links',
      workflowMode: 'agentic',
      extractionMode: DEFAULT_AGENT_SETTINGS.extractionMode,
      targetedMaxPages: DEFAULT_AGENT_SETTINGS.targetedMaxPages,
      jsonGraphPath: DEFAULT_AGENT_SETTINGS.jsonGraphPath,
      jsonGraphPaths: [...DEFAULT_JSON_GRAPH_PATHS],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: DEFAULT_AGENT_SETTINGS.kgQueryMaxNodes,
      kgQueryHops: DEFAULT_AGENT_SETTINGS.kgQueryHops,
      sourceRag: false,
      useLiveTiled: false,
      tiledUri: '',
    });
  });

  it('compares settings for unsaved-change detection', () => {
    const base: AgentSettings = {
      backend: 'cborg',
      model: DEFAULT_CBORG_MODEL,
      graphSource: 'splash_links',
      workflowMode: 'deterministic',
      extractionMode: 'full',
      targetedMaxPages: 6,
      jsonGraphPath: DEFAULT_AGENT_SETTINGS.jsonGraphPath,
      jsonGraphPaths: [...DEFAULT_JSON_GRAPH_PATHS],
      availableGraphs: [],
      selectedGraphIds: [],
      kgQueryMaxNodes: 100,
      kgQueryHops: 1,
      sourceRag: false,
      useLiveTiled: false,
      tiledUri: '',
    };
    expect(settingsEqual(base, { ...base })).toBe(true);
    expect(settingsEqual(base, { ...base, sourceRag: true })).toBe(false);
    expect(settingsEqual(base, { ...base, useLiveTiled: true })).toBe(false);
    expect(settingsEqual(base, { ...base, backend: 'ollama' })).toBe(false);
    expect(settingsEqual(base, { ...base, model: 'google/gemini-flash' })).toBe(false);
    expect(settingsEqual(base, { ...base, graphSource: 'json' })).toBe(false);
    expect(settingsEqual(base, { ...base, workflowMode: 'agentic' })).toBe(false);
    expect(settingsEqual(base, { ...base, extractionMode: 'targeted' })).toBe(false);
    expect(settingsEqual(base, { ...base, targetedMaxPages: 4 })).toBe(false);
    expect(settingsEqual(base, { ...base, kgQueryHops: 2 })).toBe(false);
    expect(settingsEqual(base, { ...base, kgQueryMaxNodes: 250 })).toBe(false);
  });

  it('picks backend-specific default models', () => {
    expect(defaultModelForBackend('cborg', baseResponse)).toBe(DEFAULT_CBORG_MODEL);
    expect(defaultModelForBackend('ollama', baseResponse)).toBe(DEFAULT_OLLAMA_MODEL);
  });

  it('exposes hop presets through 20 and hydrates a saved 20-hop setting', () => {
    expect(KG_QUERY_HOPS_PRESETS[0]).toBe(1);
    expect(KG_QUERY_HOPS_PRESETS).toContain(3);
    expect(KG_QUERY_HOPS_PRESETS[KG_QUERY_HOPS_PRESETS.length - 1]).toBe(20);
    expect(KG_QUERY_HOPS_PRESETS).toHaveLength(MAX_KG_QUERY_HOPS);
    expect(clampKgQueryHops(21)).toBe(20);
    expect(clampKgQueryHops(0)).toBe(1);

    const settings = settingsFromApiResponse({
      ...baseResponse,
      kg_query_hops: 20,
    });
    expect(settings.kgQueryHops).toBe(20);
    saveAgentSettings(settings);
    expect(loadAgentSettings().kgQueryHops).toBe(20);
  });

  it('loadCachedGraphList returns [] when nothing is cached', () => {
    expect(loadCachedGraphList()).toEqual([]);
  });

  it('saveCachedGraphList + loadCachedGraphList round-trips a path list', () => {
    const paths = [
      'storage/kg/matkg_rsoxs_v1.json',
      'storage/kg/matkg_bl1101_v1.json',
    ];
    saveCachedGraphList(paths);
    expect(loadCachedGraphList()).toEqual(paths);
  });

  it('loadCachedGraphList filters out non-string and blank entries', () => {
    localStorage.setItem('fair2wise-available-graphs-cache-v1', JSON.stringify([
      'storage/kg/good.json',
      42,
      null,
      '',
      'storage/kg/also-good.json',
    ]));
    expect(loadCachedGraphList()).toEqual([
      'storage/kg/good.json',
      'storage/kg/also-good.json',
    ]);
  });

  it('loadCachedGraphList returns [] for malformed cache', () => {
    localStorage.setItem('fair2wise-available-graphs-cache-v1', 'not-json{{{');
    expect(loadCachedGraphList()).toEqual([]);
  });

  it('saveCachedGraphList overwrites a previous cache', () => {
    saveCachedGraphList(['storage/kg/old.json']);
    saveCachedGraphList(['storage/kg/new.json']);
    expect(loadCachedGraphList()).toEqual(['storage/kg/new.json']);
  });
});
