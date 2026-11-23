import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Label } from './ui/label';
import { fetchOllamaModels, ollamaStateText, type OllamaPickerState } from './ollamaApi';

const SELECT_CLASS =
  'h-9 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50';

/**
 * Settings-panel Ollama model dropdown, filled from the server's Ollama (`GET /v1/ollama/models`, which asks
 * `/api/tags`). It asks again each time the panel opens, each time the dropdown gets focus and on the refresh button, so
 * a model pulled meanwhile shows up. The current setting stays selectable even when the server does not list it
 * (marked as such), so saving other settings never silently changes the model. When the server has no such route the
 * control is the plain text field it was before.
 */
export function OllamaModelSetting({
  open,
  value,
  onChange,
  disabled = false,
  placeholder,
}: {
  /** The settings panel is open (a fresh listing is requested on every open). */
  open: boolean;
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
  placeholder: string;
}) {
  const [picker, setPicker] = useState<OllamaPickerState | null>(null);
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      setPicker(await fetchOllamaModels());
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  if (picker?.state === 'not_offered') {
    return (
      <div className="space-y-2 px-3">
        <Label htmlFor="ollama-model-input" className="text-xs font-medium text-slate-500">Ollama model</Label>
        <input
          id="ollama-model-input"
          type="text"
          value={value}
          disabled={disabled}
          onChange={event => onChange(event.target.value)}
          placeholder={placeholder}
          className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>
    );
  }

  const models = picker?.models ?? [];
  const listed = models.includes(value);
  const text = picker ? ollamaStateText(picker) : '';
  const usable = models.length > 0;

  return (
    <div className="space-y-2 px-3" data-testid="ollama-model-setting">
      <Label htmlFor="ollama-model-select" className="text-xs font-medium text-slate-500">Ollama model</Label>
      <div className="flex items-center gap-2">
        <select
          id="ollama-model-select"
          value={value}
          disabled={disabled || !usable}
          onFocus={() => void load()}
          onChange={event => onChange(event.target.value)}
          className={SELECT_CLASS}
        >
          {value && !listed && (
            <option value={value}>{usable ? `${value} (not on the Ollama server)` : value}</option>
          )}
          {!value && <option value="">{loading ? 'Loading Ollama models…' : 'No model selected'}</option>}
          {models.map(model => (
            <option key={model} value={model}>{model}</option>
          ))}
        </select>
        <button
          type="button"
          aria-label="Refresh Ollama models"
          disabled={disabled || loading}
          onClick={() => void load()}
          className="flex size-9 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={14} aria-hidden="true" className={loading ? 'animate-spin' : undefined} />
        </button>
      </div>
      <p className="text-xs text-slate-500" data-testid="ollama-model-source" role={text ? 'status' : undefined}>
        {loading && !picker
          ? 'Loading Ollama models…'
          : text || `${models.length} model${models.length === 1 ? '' : 's'} on the Ollama server.`}
      </p>
    </div>
  );
}
