/**
 * Validation judge model (owner O-U2): the settings-panel dropdown reads and saves the
 * deployment-wide judge at `/v1/validation/judge-model`. The model list comes from the
 * provider's live listing on the server; nothing here is a hardcoded model list.
 *
 * The control is additive: when the endpoint is absent (legacy app without `/v1`) or the
 * caller is not allowed (ALS non-admin, 401/403), `fetchJudgeModel` returns `null` and the
 * panel shows nothing. It never touches `PUT /settings` or browser storage.
 */
import { AGENT_API_BASE } from './data/liveAgent';

export const JUDGE_MODEL_PATH = '/v1/validation/judge-model';

export interface JudgeModelState {
  judge_model: string | null;
  updated_by: string | null;
  updated_at: string | null;
  scope: string;
  judge_model_available: boolean | null;
  /** Free-model policy (owner decision 2026-11-09): false when the saved judge is not a free LBL-hosted id. */
  judge_model_allowed: boolean | null;
  /** The ids the server offers: the live list limited to free models. */
  available_models: string[];
  models_source: 'live' | 'unavailable' | string;
  models_listed_at: string | null;
  models_error: string | null;
  backend: string;
  /** Where the allowlist came from and the free id a refused setting falls back to. */
  model_policy: { source: string | null; as_of: string | null; default_model: string };
}

/** Statuses that mean "this caller or this server has no judge setting": hide the control. */
const HIDDEN_STATUSES = new Set([401, 403, 404, 405]);

function errorText(body: string, status: number): string {
  try {
    const parsed = JSON.parse(body) as { message?: unknown; detail?: unknown };
    const message = typeof parsed.message === 'string' ? parsed.message : parsed.detail;
    if (typeof message === 'string' && message.trim()) return message;
  } catch {
    // not JSON
  }
  return `Judge model request failed (HTTP ${status}).`;
}

export function normalizeJudgeState(raw: unknown): JudgeModelState {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const models = Array.isArray(data.available_models)
    ? data.available_models.filter((m): m is string => typeof m === 'string' && m.trim().length > 0)
    : [];
  const str = (value: unknown) => (typeof value === 'string' && value.trim() ? value : null);
  return {
    judge_model: str(data.judge_model),
    updated_by: str(data.updated_by),
    updated_at: str(data.updated_at),
    scope: str(data.scope) || 'deployment',
    judge_model_available: typeof data.judge_model_available === 'boolean' ? data.judge_model_available : null,
    judge_model_allowed: typeof data.judge_model_allowed === 'boolean' ? data.judge_model_allowed : null,
    available_models: models,
    models_source: str(data.models_source) || 'unavailable',
    models_listed_at: str(data.models_listed_at),
    models_error: str(data.models_error),
    backend: str(data.backend) || 'cborg',
    model_policy: normalizePolicy(data.model_policy),
  };
}

function normalizePolicy(raw: unknown): JudgeModelState['model_policy'] {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const str = (value: unknown) => (typeof value === 'string' && value.trim() ? value : null);
  return {
    source: str(data.source),
    as_of: str(data.as_of),
    default_model: str(data.default_model) || DEFAULT_FREE_MODEL,
  };
}

/** The free id used when the server names none (matches the packaged policy's default). */
export const DEFAULT_FREE_MODEL = 'lbl/cborg-chat';

/**
 * The judge id the dropdown should show: the saved judge when the policy allows it, else `''`
 * (no judge) so a saved paid id is never re-submitted; `replaced` drives the notice.
 */
export function judgeDraft(state: JudgeModelState): { draft: string; replaced: boolean } {
  if (state.judge_model && state.judge_model_allowed === false) return { draft: '', replaced: true };
  return { draft: state.judge_model ?? '', replaced: false };
}

/** Current judge and live model list, or `null` when this caller/server has no judge setting. */
export async function fetchJudgeModel(
  fetcher: typeof fetch = fetch,
  refresh = false,
): Promise<JudgeModelState | null> {
  let response: Response;
  try {
    response = await fetcher(`${AGENT_API_BASE}${JUDGE_MODEL_PATH}${refresh ? '?refresh=true' : ''}`, {
      credentials: 'same-origin',
    });
  } catch {
    return null;
  }
  if (HIDDEN_STATUSES.has(response.status)) return null;
  if (!response.ok) throw new Error(errorText(await response.text(), response.status));
  return normalizeJudgeState(await response.json());
}

/** Save (or clear with `null`) the deployment judge; the server checks it against the live list. */
export async function saveJudgeModel(
  judgeModel: string | null,
  fetcher: typeof fetch = fetch,
): Promise<JudgeModelState> {
  const response = await fetcher(`${AGENT_API_BASE}${JUDGE_MODEL_PATH}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ judge_model: judgeModel }),
  });
  if (!response.ok) throw new Error(errorText(await response.text(), response.status));
  return normalizeJudgeState(await response.json());
}

/**
 * Dropdown options: the free models of the live list, plus the saved judge if the provider no longer lists
 * it. A saved judge the free-model policy refuses (`judge_model_allowed === false`) is never offered.
 */
export function judgeOptions(state: JudgeModelState): Array<{ value: string; label: string }> {
  const options = state.available_models.map(model => ({ value: model, label: model }));
  if (state.judge_model && state.judge_model_allowed !== false && !state.available_models.includes(state.judge_model)) {
    options.unshift({ value: state.judge_model, label: `${state.judge_model} (not currently listed)` });
  }
  return options;
}
