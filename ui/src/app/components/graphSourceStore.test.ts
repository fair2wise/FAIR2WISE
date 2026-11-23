import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  GRAPH_SOURCE_ID_RE,
  applyDefaultGraphSource,
  clearGraphSourceChoice,
  getGraphSourceState,
  getSelectedGraphSource,
  graphSourceFromPath,
  resetGraphSourceStore,
  setGraphSourceStatus,
  setSelectedGraphSource,
  subscribeGraphSource,
} from './graphSourceStore';

const KEY = 'fair2wise-graph-source-v1';

beforeEach(() => {
  window.localStorage.clear();
  resetGraphSourceStore();
});
afterEach(() => vi.restoreAllMocks());

describe('graph source store', () => {
  it('starts on the default graph and sends no source', () => {
    expect(getSelectedGraphSource()).toBeNull();
  });

  it('remembers the choice per viewer in localStorage, an explicit default choice as __settings__', () => {
    setSelectedGraphSource('tiled:e2e_demo');
    expect(getSelectedGraphSource()).toBe('tiled:e2e_demo');
    expect(window.localStorage.getItem(KEY)).toBe('tiled:e2e_demo');
    setSelectedGraphSource(null);
    expect(getSelectedGraphSource()).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBe('__settings__');
  });

  it('never stores or sends an id the server would not accept (paths, URLs)', () => {
    for (const bad of ['../etc/passwd', '/etc/passwd', 'http://evil.example', 'json:../x', 'json:', 'x', 'tiled:a b']) {
      expect(GRAPH_SOURCE_ID_RE.test(bad)).toBe(false);
      setSelectedGraphSource(bad);
      expect(getSelectedGraphSource()).toBeNull();
      expect(window.localStorage.getItem(KEY)).toBe('__settings__');
    }
    for (const good of ['json:rsoxs_v1', 'json:synthetic/v1', 'tiled:e2e_demo']) {
      expect(GRAPH_SOURCE_ID_RE.test(good)).toBe(true);
    }
  });

  it('still works when storage throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(() => setSelectedGraphSource('json:rsoxs_v1')).not.toThrow();
    expect(getSelectedGraphSource()).toBe('json:rsoxs_v1');
    expect(() => setSelectedGraphSource(null)).not.toThrow();
    expect(getSelectedGraphSource()).toBeNull();
  });

  it('notifies subscribers, resets the error on a new choice and ignores no-op status updates', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeGraphSource(listener);
    setSelectedGraphSource('tiled:e2e_demo');
    setGraphSourceStatus({ loading: false, error: 'Tiled graph is unavailable' });
    expect(getGraphSourceState().error).toBe('Tiled graph is unavailable');
    const calls = listener.mock.calls.length;
    setGraphSourceStatus({ loading: false, error: 'Tiled graph is unavailable' });
    expect(listener.mock.calls.length).toBe(calls);
    setSelectedGraphSource('json:rsoxs_v1');
    expect(getGraphSourceState()).toEqual({ selected: 'json:rsoxs_v1', loading: false, error: null });
    unsubscribe();
    setSelectedGraphSource(null);
    expect(listener.mock.calls.length).toBe(calls + 1);
  });
});

describe('graphSourceFromPath', () => {
  it('reads the source id of a source payload and ignores every other source_path', () => {
    expect(graphSourceFromPath('source:tiled:e2e_demo')).toBe('tiled:e2e_demo');
    expect(graphSourceFromPath('source:json:rsoxs_v1')).toBe('json:rsoxs_v1');
    for (const other of ['storage/kg/matkg_rsoxs_v1.json', 'tiled://graphql', 'query:abc', 'source:../x', '', undefined, null]) {
      expect(graphSourceFromPath(other)).toBeNull();
    }
  });

  it('applies the server default only while the user has made no choice, and never stores it', () => {
    expect(applyDefaultGraphSource('../x')).toBe(false);
    expect(applyDefaultGraphSource('json:rsoxs/v4')).toBe(true);
    expect(getSelectedGraphSource()).toBe('json:rsoxs/v4');
    expect(window.localStorage.getItem(KEY)).toBeNull();
    setSelectedGraphSource(null);
    expect(applyDefaultGraphSource('json:rsoxs/v4')).toBe(false);
    expect(getSelectedGraphSource()).toBeNull();
    clearGraphSourceChoice();
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(applyDefaultGraphSource('json:rsoxs/v4')).toBe(true);
  });
});
