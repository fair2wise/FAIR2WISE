import type { ModelsResponse, OllamaListingState } from '../extractionApi';
import { modelListReason } from '../providerErrors';

/** One selectable extraction model: CBORG ids are used as they are, Ollama ones are prefixed so the two never clash. */
export interface ModelChoice {
  /** `<select>` value. */
  value: string;
  /** The model id sent to the server. */
  model: string;
  /** Sent as the job's `backend` only for Ollama; CBORG is the server's default and rides without it. */
  backend: 'ollama' | undefined;
  /** Group label in the dropdown. */
  group: 'CBORG' | 'Ollama';
}

const OLLAMA_PREFIX = 'ollama:';

/** The models the Extract tab offers: the free CBORG list (when CBORG answered) then the Ollama models (when listed). */
export function buildModelChoices(data: ModelsResponse | undefined): ModelChoice[] {
  if (!data) return [];
  const cborg = data.available ? data.models : [];
  const ollama = data.ollama?.state === 'ok' ? data.ollama.models : [];
  return [
    ...cborg.map((model): ModelChoice => ({ value: model, model, backend: undefined, group: 'CBORG' })),
    ...ollama.map((model): ModelChoice => ({ value: `${OLLAMA_PREFIX}${model}`, model, backend: 'ollama', group: 'Ollama' })),
  ];
}

const OLLAMA_TEXT: Record<OllamaListingState, string> = {
  ok: '',
  empty: 'Ollama is reachable but holds no models.',
  unset: 'Ollama is not configured on the server (OLLAMA_URL is unset).',
  unreachable: 'Ollama is configured but did not answer.',
  error: 'Ollama answered, but not with a model list.',
};

/** One sentence about the Ollama listing, or `''` when there is nothing to say (no Ollama block, or it listed models). */
export function ollamaNote(data: ModelsResponse | undefined): string {
  const ollama = data?.ollama;
  if (!ollama || ollama.state === 'ok') return '';
  return OLLAMA_TEXT[ollama.state] ?? '';
}

/** Why CBORG offers no models (the specific refusal when it is the address refusal), or `''` when it does. */
export function cborgUnavailableReason(data: ModelsResponse | undefined): string {
  if (!data || data.available) return '';
  const reason = modelListReason(data);
  return `CBORG model list unavailable${reason ? `: ${reason}` : ''}.`;
}

/** The line under the model dropdown: what each provider offers or why it offers nothing. */
export function modelSourceNote(data: ModelsResponse | undefined): string {
  if (!data) return 'Model list unavailable.';
  const parts: string[] = [];
  if (data.available) {
    parts.push(`${data.models.length} models offered${data.backend ? ` by ${data.backend.toUpperCase()}` : ''}.`);
  } else {
    parts.push(cborgUnavailableReason(data));
  }
  if (data.ollama?.state === 'ok') parts.push(`${data.ollama.models.length} Ollama models.`);
  else parts.push(ollamaNote(data));
  parts.push('Used for this run only; Settings is unchanged.');
  return parts.filter(Boolean).join(' ');
}

/** The alert when nothing can run: every provider's reason. */
export function noBackendNote(data: ModelsResponse | undefined): string {
  const cborg = data && !data.available ? modelListReason(data) : '';
  return ['No model backend is available', cborg ? `: ${cborg}` : '', '.', ollamaNote(data) ? ` ${ollamaNote(data)}` : '',
    ' Configure a backend to start an extraction.'].join('');
}
