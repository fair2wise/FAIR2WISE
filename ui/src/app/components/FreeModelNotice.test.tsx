import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FreeModelNotice } from './FreeModelNotice';
import { freeModelFallback } from './agentSettings';
import { judgeDraft, judgeOptions, normalizeJudgeState } from './judgeModel';

const FREE = ['lbl/cborg-chat', 'lbl/cborg-deepthought', 'lbl/gpt-oss-120b'];

describe('free-model policy in the Settings panel (owner decision 2026-11-09)', () => {
  it('keeps a listed free id and falls back to the default for a paid or unknown one', () => {
    expect(freeModelFallback('lbl/cborg-deepthought', FREE)).toEqual({ model: 'lbl/cborg-deepthought', replaced: false });
    expect(freeModelFallback('openai/gpt-5', FREE)).toEqual({ model: 'lbl/cborg-chat', replaced: true });
    expect(freeModelFallback('anthropic/claude-x', FREE, 'lbl/gpt-oss-120b')).toEqual({ model: 'lbl/gpt-oss-120b', replaced: true });
    // legacy Gemini aliases normalise first and are then refused like any paid id
    expect(freeModelFallback('google/gemini-pro', FREE).replaced).toBe(true);
    // no list known yet: nothing is replaced
    expect(freeModelFallback('openai/gpt-5', [])).toEqual({ model: 'openai/gpt-5', replaced: false });
  });

  it('renders nothing while the picked model is allowed', () => {
    const { container } = render(<FreeModelNotice model="lbl/cborg-chat" allowed={FREE} fallback="lbl/cborg-chat" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a visible notice naming the refused id and the free fallback', () => {
    render(<FreeModelNotice model="openai/gpt-5" allowed={FREE} fallback="lbl/cborg-chat" role="chat model" />);
    const notice = screen.getByTestId('free-model-notice');
    expect(notice).toHaveTextContent('openai/gpt-5');
    expect(notice).toHaveTextContent('lbl/cborg-chat');
    expect(notice).toHaveTextContent('as the chat model');
    expect(notice).toHaveTextContent('Only free models are allowed via CBORG');
  });

  it('shows the notice when the server already refused the id, even without a list', () => {
    render(<FreeModelNotice model="openai/gpt-5" allowed={[]} fallback="lbl/cborg-chat" role="judge" refused />);
    expect(screen.getByTestId('free-model-notice')).toHaveTextContent('as the judge');
  });

  it('judge dropdown: only the free list is offered and a refused saved judge is dropped with a notice', () => {
    const state = normalizeJudgeState({
      judge_model: 'openai/gpt-5', judge_model_available: true, judge_model_allowed: false,
      available_models: FREE, models_source: 'live', backend: 'cborg',
      model_policy: { source: 'CBORG models page', as_of: '2026-11-06', default_model: 'lbl/cborg-chat' },
    });
    expect(state.judge_model_allowed).toBe(false);
    expect(state.model_policy).toEqual({ source: 'CBORG models page', as_of: '2026-11-06', default_model: 'lbl/cborg-chat' });
    expect(judgeOptions(state).map(o => o.value)).toEqual(FREE);
    expect(judgeDraft(state)).toEqual({ draft: '', replaced: true });
    // an allowed judge the provider no longer lists is still shown, as before
    const unlisted = normalizeJudgeState({ ...state, judge_model: 'lbl/cborg-mini', judge_model_allowed: true });
    expect(judgeOptions(unlisted)[0]).toEqual({ value: 'lbl/cborg-mini', label: 'lbl/cborg-mini (not currently listed)' });
    expect(judgeDraft(unlisted)).toEqual({ draft: 'lbl/cborg-mini', replaced: false });
    // a payload from an older server without the policy fields still normalises
    const legacy = normalizeJudgeState({ judge_model: 'lbl/cborg-chat', available_models: FREE });
    expect(legacy.judge_model_allowed).toBeNull();
    expect(legacy.model_policy.default_model).toBe('lbl/cborg-chat');
  });
});
