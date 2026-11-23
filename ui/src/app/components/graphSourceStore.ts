/**
 * Per-viewer choice of the graph source the KG viewer reads (owner instruction 2026-11-06: read both
 * JSON KGs and Tiled KGs). The choice is an opaque server id (`json:<name>` or `tiled:<graph>`) listed by
 * `GET /v1/graph/sources`; `null` means the graph the server's Settings pick (Splash Links or a JSON file), and sends no
 * `source` parameter at all (so the default requests are unchanged).
 *
 * The id is remembered in localStorage per browser; every storage access is wrapped because storage can
 * throw (private window, blocked site data) and the page must render without it.
 */
import { useSyncExternalStore } from 'react';

const STORAGE_KEY = 'fair2wise-graph-source-v1';

/** The spelling the server accepts (mirrors `SOURCE_ID_RE`); anything else is never stored or sent. */
export const GRAPH_SOURCE_ID_RE = /^(json|tiled):[A-Za-z0-9][A-Za-z0-9_.@/-]{0,119}$/;

/** Prefix of `GraphPayload.source_path` for a payload read from a graph source. */
export const GRAPH_SOURCE_PATH_PREFIX = 'source:';

export interface GraphSourceState {
  /** Selected source id, or `null` for the default graph. */
  selected: string | null;
  /** A graph request for the selected source is in flight. */
  loading: boolean;
  /** Last failure of a graph read for the selected source (already human readable), or `null`. */
  error: string | null;
}

/** Stored when the user explicitly picked the "Settings graph" option (e.g. Splash Links): the server default must not override it. */
const EXPLICIT_SETTINGS_GRAPH = '__settings__';

/** The remembered choice: a source id, `null` for the Settings graph, and whether the user made any choice. */
function readStored(): { selected: string | null; chosen: boolean } {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === EXPLICIT_SETTINGS_GRAPH) return { selected: null, chosen: true };
    return raw && GRAPH_SOURCE_ID_RE.test(raw) ? { selected: raw, chosen: true } : { selected: null, chosen: false };
  } catch {
    return { selected: null, chosen: false };
  }
}

function writeStored(id: string | null): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id ?? EXPLICIT_SETTINGS_GRAPH);
  } catch {
    // Storage unavailable: the choice lives for this page only.
  }
}

const initial = readStored();
/** True once the user picked a source (or the Settings graph) in this browser; the default never overrides it. */
let userChose = initial.chosen;
let state: GraphSourceState = { selected: initial.selected, loading: false, error: null };
const listeners = new Set<() => void>();

function publish(next: GraphSourceState): void {
  state = next;
  listeners.forEach(listener => listener());
}

/** Current state (a stable object until something changes). */
export function getGraphSourceState(): GraphSourceState {
  return state;
}

/** The selected source id, or `null` for the default graph. */
export function getSelectedGraphSource(): string | null {
  return state.selected;
}

/** Select a source (`null` = default), remember it and clear any previous error. Invalid ids select the default. */
export function setSelectedGraphSource(id: string | null): void {
  const next = id && GRAPH_SOURCE_ID_RE.test(id) ? id : null;
  userChose = true;
  writeStored(next);
  publish({ selected: next, loading: false, error: null });
}

/**
 * Open on the server's default JSON source when the user has never chosen one (owner bug list 2026-11, task 5).
 * Not remembered: the choice stays "no choice" until the user picks, so a later server default still applies.
 * Returns whether the selection changed.
 */
export function applyDefaultGraphSource(id: string | null | undefined): boolean {
  if (userChose || state.selected !== null || !id || !GRAPH_SOURCE_ID_RE.test(id)) return false;
  publish({ selected: id, loading: false, error: null });
  return true;
}

/** Forget the remembered choice (a remembered id the server no longer lists): the server default applies again. */
export function clearGraphSourceChoice(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable.
  }
  userChose = false;
  publish({ selected: null, loading: false, error: null });
}

/** Record that a graph read started (`loading`), succeeded (`error` null) or failed (`error` text). */
export function setGraphSourceStatus(status: { loading: boolean; error: string | null }): void {
  if (status.loading === state.loading && status.error === state.error) return;
  publish({ ...state, ...status });
}

/** Subscribe to state changes (the `useSyncExternalStore` contract). */
export function subscribeGraphSource(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React hook over the store. */
export function useGraphSourceState(): GraphSourceState {
  return useSyncExternalStore(subscribeGraphSource, getGraphSourceState, getGraphSourceState);
}

/** The source id a payload was read from (`source:<id>` in `source_path`), or `null` for the default graph. */
export function graphSourceFromPath(sourcePath: string | undefined | null): string | null {
  if (!sourcePath || !sourcePath.startsWith(GRAPH_SOURCE_PATH_PREFIX)) return null;
  const id = sourcePath.slice(GRAPH_SOURCE_PATH_PREFIX.length);
  return GRAPH_SOURCE_ID_RE.test(id) ? id : null;
}

/** Reset to the default graph and forget the stored choice (tests). */
export function resetGraphSourceStore(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable.
  }
  userChose = false;
  publish({ selected: null, loading: false, error: null });
}
