import { useEffect, useState } from 'react';
import { RefreshCw, Save } from 'lucide-react';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { FreeModelNotice } from './FreeModelNotice';
import {
  fetchJudgeModel,
  judgeDraft,
  judgeOptions,
  saveJudgeModel,
  type JudgeModelState,
} from './judgeModel';
import { Label } from './ui/label';

/**
 * Settings-panel section for the validation judge model (owner O-U2).
 *
 * Deployment-wide and admin-only on ALS: it renders nothing for callers or servers without
 * the `/v1/validation/judge-model` route. It has its own Save button and never changes the
 * agent settings saved by "Save Preferences".
 */
export function JudgeModelSetting({ open, disabled = false }: { open: boolean; disabled?: boolean }) {
  const [state, setState] = useState<JudgeModelState | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function load(refresh = false) {
    setLoading(true);
    setError('');
    try {
      const next = await fetchJudgeModel(fetch, refresh);
      setState(next);
      setDraft(next ? judgeDraft(next).draft : '');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    setMessage('');
    void load();
  }, [open]);

  if (!state && !error) return null;

  async function handleSave() {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const next = await saveJudgeModel(draft || null);
      setState(next);
      setDraft(next.judge_model ?? '');
      setMessage(next.judge_model ? `Judge model saved: ${next.judge_model}` : 'Judge model cleared.');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const options = state ? judgeOptions(state) : [];
  const listUnavailable = state?.models_source !== 'live';
  const busy = disabled || loading || saving;
  const changed = (state?.judge_model ?? '') !== draft;

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3" data-testid="judge-model-setting">
      <Label htmlFor="judge-model-select" className="text-xs font-medium text-slate-500">
        Validation judge model
      </Label>
      <p className="text-xs text-slate-500">
        Judges semantic checks in validation runs (binary suite). Deployment-wide; on ALS only admins can change it.
        It must differ from the labelling model and the model under test. Only free LBL-hosted CBORG models are
        offered (owner decision 2026-11-09).
      </p>
      {state?.judge_model && state.judge_model_allowed === false && (
        <FreeModelNotice
          model={state.judge_model}
          allowed={state.available_models}
          fallback={state.model_policy.default_model}
          role="judge"
          refused
        />
      )}
      {state && (
        <div className="flex items-center gap-2">
          <select
            id="judge-model-select"
            value={draft}
            disabled={busy || (listUnavailable && options.length === 0)}
            onChange={event => setDraft(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="">No judge selected</option>
            {options.map(option => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <button
            type="button"
            aria-label="Refresh available models"
            disabled={busy}
            onClick={() => void load(true)}
            className="flex size-9 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      )}
      {state && (
        <p className="text-xs text-slate-500" data-testid="judge-model-source">
          {listUnavailable
            ? `Model list unavailable${state.models_error ? `: ${state.models_error}` : ''}.`
            : `${state.available_models.length} models currently offered by ${state.backend.toUpperCase()}`
              + (state.models_listed_at ? ` (listed ${state.models_listed_at})` : '')
              + '.'}
          {state.judge_model && state.updated_by ? ` Saved by ${state.updated_by}.` : ''}
        </p>
      )}
      {state && (
        <ButtonWithIcon
          text={saving ? 'Saving…' : 'Save judge model'}
          icon={<Save size={16} strokeWidth={2} aria-hidden="true" />}
          isSecondary
          size="small"
          aria-label="Save judge model"
          disabled={busy || !changed || (Boolean(draft) && listUnavailable)}
          onClick={() => void handleSave()}
        />
      )}
      {message && <p className="text-xs text-emerald-700" role="status">{message}</p>}
      {error && <p className="text-xs text-red-700" role="alert">{error}</p>}
    </div>
  );
}
