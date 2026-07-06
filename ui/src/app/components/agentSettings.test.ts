import { describe, expect, it, beforeEach } from 'vitest';
import {
  DEFAULT_AGENT_SETTINGS,
  graphSourceFromApi,
  graphSourceToApi,
  loadAgentSettings,
  saveAgentSettings,
  settingsEqual,
  settingsFromApiResponse,
  settingsToApiPayload,
  type AgentSettings,
} from './agentSettings';

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

  it('builds splash payload without json_graph_path', () => {
    const settings: AgentSettings = {
      backend: 'cborg',
      graphSource: 'splash_links',
      jsonGraphPath: 'storage/kg/ignored.json',
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'cborg',
      graph_source: 'splash',
      json_graph_path: null,
    });
  });

  it('builds json payload with selected graph path', () => {
    const settings: AgentSettings = {
      backend: 'ollama',
      graphSource: 'json',
      jsonGraphPath: 'storage/kg/alpha.json',
    };
    expect(settingsToApiPayload(settings)).toEqual({
      backend: 'ollama',
      graph_source: 'json',
      json_graph_path: 'storage/kg/alpha.json',
    });
  });

  it('hydrates settings from API response', () => {
    const settings = settingsFromApiResponse({
      backend: 'ollama',
      graph_source: 'json',
      json_graph_path: 'storage/kg/alpha.json',
      available_json_graphs: ['storage/kg/alpha.json', 'storage/kg/beta.json'],
    });
    expect(settings).toEqual({
      backend: 'ollama',
      graphSource: 'json',
      jsonGraphPath: 'storage/kg/alpha.json',
    });
  });

  it('persists settings in localStorage', () => {
    const settings: AgentSettings = {
      backend: 'ollama',
      graphSource: 'json',
      jsonGraphPath: 'storage/kg/custom.json',
    };
    saveAgentSettings(settings);
    expect(loadAgentSettings()).toEqual(settings);
  });

  it('falls back to defaults for invalid stored settings', () => {
    localStorage.setItem('fair2wise-agent-settings-v1', '{"backend":"bad"}');
    expect(loadAgentSettings()).toEqual({
      backend: 'cborg',
      graphSource: 'splash_links',
      jsonGraphPath: DEFAULT_AGENT_SETTINGS.jsonGraphPath,
    });
  });

  it('compares settings for unsaved-change detection', () => {
    const base: AgentSettings = {
      backend: 'cborg',
      graphSource: 'splash_links',
      jsonGraphPath: DEFAULT_AGENT_SETTINGS.jsonGraphPath,
    };
    expect(settingsEqual(base, { ...base })).toBe(true);
    expect(settingsEqual(base, { ...base, backend: 'ollama' })).toBe(false);
    expect(settingsEqual(base, { ...base, graphSource: 'json' })).toBe(false);
  });
});
