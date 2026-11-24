/**
 * "The UI applies its own knowledge graph source on load" (owner decision 2026-11).
 *
 * The server runtime resets to its static `--kg-mode` on every restart and the UI only told it on Save, so a
 * UI showing JSON could talk to a server in splash mode. These helpers push the UI's own source with a partial
 * `PUT /settings` ({graph_source} only: every other field stays as the server has it) on load and on window
 * focus. Failures are logged and ignored (for example the 400 when `storage/kg` holds no JSON file); nothing
 * here blocks rendering or raises a user-facing error.
 *
 * ALS rule: the apply runs on `:local` regardless, but on an ALS profile (or when the profile is unknown)
 * only if the user chose the source explicitly (`graphSourceChosen`), so the unchosen default never overrides
 * a user's saved personal setting. The profile comes from `GET /v1/capabilities` (`profile` is `local`,
 * `als-dev` or `als-prod`), the same cache `useCapabilities` reads.
 *
 * Several tabs share one server runtime on `:local`: the last PUT wins. Each tab keeps its own stored source
 * and restores it on its next focus or load.
 */
import type { QueryClient } from '@tanstack/react-query';
import { capabilitiesKey } from './capabilitiesHooks';
import { fetchCapabilities } from './capabilitiesApi';
import {
  graphSourceToApi,
  isGraphSourceChosen,
  loadAgentSettings,
  saveAgentSettings,
  settingsFromApiResponse,
} from './agentSettings';
import { fetchAgentSettings, updateAgentSettings, type AgentSettingsApiResponse } from './data/liveAgent';
import { recordUiEvent } from './uiTelemetry';

/** At most one focus re-apply per this many milliseconds. */
export const FOCUS_REAPPLY_MIN_INTERVAL_MS = 15_000;

function logIgnored(message: string, error: unknown): void {
  console.warn(message, error);
  recordUiEvent('console_warning', {
    message,
    detail: error instanceof Error ? error.message : String(error),
  });
}

export interface GraphSourceApplier {
  /** Load: GET /settings, apply the UI's own source, then store the server's other settings (fire and forget). */
  boot(isCancelled?: () => boolean): Promise<void>;
  /** Window focus / tab visible: GET /settings and PUT {graph_source} only if it differs. Debounced. */
  focus(): Promise<void>;
}

export function createGraphSourceApplier(
  queryClient: QueryClient,
  options: { now?: () => number } = {},
): GraphSourceApplier {
  const now = options.now ?? Date.now;
  let inFlight = false;
  let lastStart = -Infinity;

  async function mayApply(chosen: boolean): Promise<boolean> {
    if (chosen) return true;
    try {
      const caps = await queryClient.fetchQuery({
        queryKey: capabilitiesKey,
        queryFn: () => fetchCapabilities(),
        staleTime: 60_000,
      });
      return caps.profile === 'local';
    } catch {
      return false; // unknown profile: treated as ALS
    }
  }

  /** PUT {graph_source} when the server differs; returns the newest server response. */
  async function apply(response: AgentSettingsApiResponse): Promise<AgentSettingsApiResponse> {
    const own = loadAgentSettings().graphSource;
    const wanted = graphSourceToApi(own);
    if (wanted === response.graph_source) return response;
    if (!(await mayApply(isGraphSourceChosen()))) return response;
    try {
      const updated = await updateAgentSettings({ graph_source: wanted });
      void queryClient.invalidateQueries({ queryKey: capabilitiesKey });
      return updated;
    } catch (error) {
      logIgnored('Could not apply the knowledge graph source to the server', error);
      return response;
    }
  }

  return {
    async boot(isCancelled = () => false) {
      inFlight = true;
      lastStart = now();
      try {
        const response = await fetchAgentSettings();
        const latest = await apply(response);
        if (!isCancelled()) {
          // The UI's own source is read here, before the save, never taken from the server's answer.
          saveAgentSettings({ ...settingsFromApiResponse(latest), graphSource: loadAgentSettings().graphSource });
        }
      } catch (error) {
        logIgnored('Failed to load agent settings', error);
      } finally {
        inFlight = false;
      }
    },

    async focus() {
      if (inFlight || now() - lastStart < FOCUS_REAPPLY_MIN_INTERVAL_MS) return;
      inFlight = true;
      lastStart = now();
      try {
        await apply(await fetchAgentSettings());
      } catch (error) {
        logIgnored('Failed to re-apply the knowledge graph source', error);
      } finally {
        inFlight = false;
      }
    },
  };
}
