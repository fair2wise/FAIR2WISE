import { describe, expect, it, beforeEach } from 'vitest';
import {
  DEFAULT_AGENT_SETTINGS,
  DEFAULT_CBORG_MODEL,
  DEFAULT_OLLAMA_MODEL,
  defaultModelForBackend,
  graphSourceFromApi,
  graphSourceToApi,
  loadAgentSettings,
  normalizeCborgModel,
  saveAgentSettings,
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
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'cborg',
      model: DEFAULT_CBORG_MODEL,
      graph_source: 'splash',
      workflow_mode: 'deterministic',
      extraction_mode: 'full',
      targeted_max_pages: 6,
      json_graph_path: null,
    });
  });

  it('builds json payload with selected graph path', () => {
    const settings: AgentSettings = {
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graphSource: 'json',
      workflowMode: 'agentic',
      extractionMode: 'targeted',
      targetedMaxPages: 4,
      jsonGraphPath: 'storage/kg/alpha.json',
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'ollama',
      model: 'qwen3.5:9b',
      graph_source: 'json',
      workflow_mode: 'agentic',
      extraction_mode: 'targeted',
      targeted_max_pages: 4,
      json_graph_path: 'storage/kg/alpha.json',
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
    });
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
    };
    expect(settingsEqual(base, { ...base })).toBe(true);
    expect(settingsEqual(base, { ...base, backend: 'ollama' })).toBe(false);
    expect(settingsEqual(base, { ...base, model: 'google/gemini-flash' })).toBe(false);
    expect(settingsEqual(base, { ...base, graphSource: 'json' })).toBe(false);
    expect(settingsEqual(base, { ...base, workflowMode: 'agentic' })).toBe(false);
    expect(settingsEqual(base, { ...base, extractionMode: 'targeted' })).toBe(false);
    expect(settingsEqual(base, { ...base, targetedMaxPages: 4 })).toBe(false);
  });

  it('picks backend-specific default models', () => {
    expect(defaultModelForBackend('cborg', baseResponse)).toBe(DEFAULT_CBORG_MODEL);
    expect(defaultModelForBackend('ollama', baseResponse)).toBe(DEFAULT_OLLAMA_MODEL);
  });
});
