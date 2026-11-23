import { describe, expect, it, vi } from 'vitest';
import { fetchJudgeModel, judgeOptions, normalizeJudgeState, saveJudgeModel } from './judgeModel';

const LIVE = {
  judge_model: 'judge-b',
  updated_by: 'local-operator',
  updated_at: '2026-10-16T00:00:00Z',
  scope: 'deployment',
  judge_model_available: true,
  available_models: ['judge-a', 'judge-b'],
  models_source: 'live',
  models_listed_at: '2026-10-16T00:00:00Z',
  models_error: null,
  models_error_code: null,
  backend: 'cborg',
};

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

describe('judgeModel', () => {
  it('reads the live list from the server, not a built-in list', async () => {
    const fetcher = respond(200, LIVE);
    const state = await fetchJudgeModel(fetcher as unknown as typeof fetch);
    expect(state?.available_models).toEqual(['judge-a', 'judge-b']);
    expect(String(fetcher.mock.calls[0][0])).toContain('/v1/validation/judge-model');
  });

  it('hides the control for non-admins and servers without the route', async () => {
    for (const status of [401, 403, 404]) {
      expect(await fetchJudgeModel(respond(status, {}) as unknown as typeof fetch)).toBeNull();
    }
    const offline = vi.fn(async () => { throw new TypeError('Failed to fetch'); });
    expect(await fetchJudgeModel(offline as unknown as typeof fetch)).toBeNull();
  });

  it('saves with PUT and surfaces the server refusal message', async () => {
    const ok = respond(200, { ...LIVE, judge_model: 'judge-a' });
    expect((await saveJudgeModel('judge-a', ok as unknown as typeof fetch)).judge_model).toBe('judge-a');
    const call = ok.mock.calls[0] as unknown as [string, RequestInit];
    expect(call[1].method).toBe('PUT');
    expect(JSON.parse(String(call[1].body))).toEqual({ judge_model: 'judge-a' });
    const refused = respond(422, { code: 'invalid_input', message: 'judge model is not in the provider list' });
    await expect(saveJudgeModel('x', refused as unknown as typeof fetch)).rejects.toThrow('not in the provider');
  });

  it('keeps a saved judge visible when the provider stopped listing it', () => {
    const state = normalizeJudgeState({ ...LIVE, judge_model: 'retired', available_models: ['judge-a'] });
    expect(judgeOptions(state).map(o => o.value)).toEqual(['retired', 'judge-a']);
    expect(judgeOptions(state)[0].label).toContain('not currently listed');
  });

  it('normalizes a malformed payload to an empty unavailable state', () => {
    const state = normalizeJudgeState({ available_models: [1, '', 'm'] });
    expect(state.available_models).toEqual(['m']);
    expect(state.models_source).toBe('unavailable');
    expect(state.judge_model).toBeNull();
  });
});
