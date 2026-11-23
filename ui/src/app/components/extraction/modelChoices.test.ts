import { describe, expect, it } from 'vitest';
import type { ModelsResponse } from '../extractionApi';
import { buildModelChoices, modelSourceNote, noBackendNote, ollamaNote } from './modelChoices';

const base: ModelsResponse = { available: true, models: ['m1'], source: 'live', error: null, backend: 'cborg' };

describe('modelChoices', () => {
  it('lists CBORG ids as they are and Ollama ids with a prefix', () => {
    const data: ModelsResponse = { ...base, ollama: { state: 'ok', models: ['llama3:8b'], error: null } };
    expect(buildModelChoices(data).map(c => [c.value, c.model, c.backend, c.group])).toEqual([
      ['m1', 'm1', undefined, 'CBORG'],
      ['ollama:llama3:8b', 'llama3:8b', 'ollama', 'Ollama'],
    ]);
  });

  it('still lists Ollama when CBORG is unavailable, and nothing when neither is', () => {
    const refused: ModelsResponse = {
      ...base, available: false, models: [], error_code: 'cborg_ip_not_authorized', error: 'x',
      ollama: { state: 'ok', models: ['a'], error: null },
    };
    expect(buildModelChoices(refused).map(c => c.value)).toEqual(['ollama:a']);
    expect(buildModelChoices({ ...refused, ollama: { state: 'empty', models: [], error: null } })).toEqual([]);
    expect(buildModelChoices(undefined)).toEqual([]);
  });

  it('says what each Ollama state means', () => {
    const note = (state: 'empty' | 'unset' | 'unreachable' | 'error') =>
      ollamaNote({ ...base, ollama: { state, models: [], error: null } });
    expect(note('unset')).toContain('OLLAMA_URL is unset');
    expect(note('unreachable')).toContain('did not answer');
    expect(note('empty')).toContain('no models');
    expect(note('error')).toContain('not with a model list');
    expect(ollamaNote(base)).toBe('');
  });

  it('puts the specific CBORG address refusal in the source line and the no-backend alert', () => {
    const refused: ModelsResponse = {
      ...base, available: false, models: [], error: 'raw text', error_code: 'cborg_ip_not_authorized',
      ollama: { state: 'unset', models: [], error: null },
    };
    expect(modelSourceNote(refused)).toContain('CBORG refused this network address; connect via LBLnet/VPN');
    expect(modelSourceNote(refused)).not.toContain('raw text');
    expect(noBackendNote(refused)).toContain('CBORG refused this network address');
    expect(noBackendNote(refused)).toContain('OLLAMA_URL is unset');
  });
});
