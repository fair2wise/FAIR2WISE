/**
 * The settings panel's Ollama model list: `GET /v1/ollama/models` (owner bug list 2026-11-23, task 4).
 *
 * The browser never calls Ollama. The API asks `{OLLAMA_URL}/api/tags` on every request and answers 200 with a
 * state (`ok`, `empty`, `unset`, `unreachable`, `error`) and never the Ollama URL. A server without the route
 * (older build, caller not allowed) answers 401/403/404/405: that is `not_offered`, and the panel keeps the
 * plain text field it always had. Nothing is cached or hardcoded here.
 */
import type { OllamaListing, OllamaListingState } from './extractionApi';
import { ApiError, HIDDEN_STATUSES, v1Json, v1Url } from './apiHttp';

export const OLLAMA_MODELS_PATH = '/v1/ollama/models';

/** What the picker shows: a server listing, "this server does not offer the list", or a request failure. */
export type OllamaPickerState =
  | { state: OllamaListingState; models: string[]; error: string | null }
  | { state: 'not_offered'; models: []; error: null }
  | { state: 'failed'; models: []; error: string };

const STATES: ReadonlySet<string> = new Set(['ok', 'empty', 'unset', 'unreachable', 'error']);

/** Validate an untrusted body into an `OllamaListing` (anything unexpected is an `error` listing). */
export function normalizeOllamaListing(raw: unknown): OllamaListing {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const state = typeof data.state === 'string' && STATES.has(data.state) ? (data.state as OllamaListingState) : null;
  if (state === null) return { state: 'error', models: [], error: 'The server sent no usable Ollama model list.' };
  const models = Array.isArray(data.models)
    ? [...new Set(data.models.filter((m): m is string => typeof m === 'string' && m.trim().length > 0))]
    : [];
  const error = typeof data.error === 'string' && data.error.trim() ? data.error : null;
  return { state: state === 'ok' && models.length === 0 ? 'empty' : state, models: state === 'ok' ? models : [], error };
}

/** Ask the API for the Ollama models right now. Never throws. */
export async function fetchOllamaModels(fetcher: typeof fetch = fetch): Promise<OllamaPickerState> {
  try {
    return normalizeOllamaListing(await v1Json<unknown>(v1Url(OLLAMA_MODELS_PATH), {}, fetcher));
  } catch (error) {
    if (error instanceof ApiError && HIDDEN_STATUSES.has(error.status)) return { state: 'not_offered', models: [], error: null };
    return { state: 'failed', models: [], error: error instanceof Error ? error.message : 'The request failed.' };
  }
}

/** One sentence for a non-`ok` state (what is wrong and what to do), or `''` for `ok`/`not_offered`. */
export function ollamaStateText(picker: OllamaPickerState): string {
  switch (picker.state) {
    case 'unset':
      return 'Ollama is not configured on the server (OLLAMA_URL is unset), so there are no models to choose from.';
    case 'unreachable':
      return 'The server could not reach Ollama. Check that it is running, then refresh the list.';
    case 'empty':
      return 'Ollama is reachable but holds no models. Pull one on the Ollama server, then refresh the list.';
    case 'error':
      return picker.error ? `${picker.error}. Refresh the list to try again.` : 'Ollama did not return a model list. Refresh to try again.';
    case 'failed':
      return `Could not load the Ollama models: ${picker.error}`;
    default:
      return '';
  }
}
