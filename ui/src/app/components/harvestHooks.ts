/**
 * TanStack Query hooks for the Harvester panel (A8, T46). Server state only: the form, the watched run
 * and the log stream are owned by the panel components.
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import {
  dismissHarvestCandidate,
  fetchHarvestCandidates,
  fetchHarvestRuns,
  fileHarvestPdf,
  undoHarvestPdf,
  type FileHarvestPdfOptions,
  HARVEST_JOB_KIND,
  startHarvest,
  type HarvestCandidate,
  type StartHarvestRequest,
} from './harvestApi';
import { fetchJobs, type JobInfo } from './extractionApi';
import { libraryKeys } from './libraryHooks';

/** Query keys for harvest data. */
export const harvestKeys = {
  all: ['harvest'] as const,
  runs: ['harvest', 'runs'] as const,
  running: ['harvest', 'running'] as const,
  candidates: (runId: string) => ['harvest', 'candidates', runId] as const,
};

/**
 * Past harvest runs, newest first.
 *
 * @param enabled - Fetch only when true.
 * @param pollMs - Refetch interval while a run is active; `false` (default) disables polling.
 */
export function useHarvestRuns(enabled = true, pollMs: number | false = false) {
  return useQuery({
    queryKey: harvestKeys.runs,
    queryFn: () => fetchHarvestRuns(),
    enabled,
    refetchInterval: pollMs,
  });
}

/**
 * The candidates of one run.
 *
 * @param runId - Run (job) id, or null for none.
 * @param pollMs - Refetch interval while the run is active; `false` (default) disables polling.
 */
export function useHarvestCandidates(runId: string | null, pollMs: number | false = false) {
  return useQuery({
    queryKey: harvestKeys.candidates(runId ?? ''),
    queryFn: () => fetchHarvestCandidates(runId as string),
    enabled: runId !== null,
    refetchInterval: pollMs,
  });
}

/** Poll interval while a harvest runs (or was just started). */
export const HARVEST_POLL_FAST_MS = 5000;
/** Poll interval while none runs: the tab stays cheap on an idle deployment. */
export const HARVEST_POLL_IDLE_MS = 30_000;
/** After the user starts a run the poll stays fast this long even if the job list does not show it yet. */
const FAST_AFTER_START_MS = 60_000;

const startedAt = new WeakMap<QueryClient, number>();

/**
 * The running harvest job, if any: after a page reload this re-attaches the panel to the run.
 * `data` is the job or `null` when none is running.
 *
 * Polls every `pollMs` (5 s) while a run is active or was just started, and every `idlePollMs` (30 s)
 * when none is; a start from this tab returns to the fast interval immediately.
 */
export function useRunningHarvest(enabled = true, pollMs = HARVEST_POLL_FAST_MS, idlePollMs = HARVEST_POLL_IDLE_MS) {
  const queryClient = useQueryClient();
  return useQuery<JobInfo | null>({
    queryKey: harvestKeys.running,
    queryFn: async () => {
      const { jobs } = await fetchJobs({ state: 'running', kind: HARVEST_JOB_KIND });
      return jobs[0] ?? null;
    },
    enabled,
    refetchInterval: query => {
      if (query.state.data) return pollMs;
      const started = startedAt.get(queryClient);
      return started !== undefined && Date.now() - started < FAST_AFTER_START_MS ? pollMs : idlePollMs;
    },
  });
}

/** Start a harvest run; refreshes the running job and the run list and returns the running poll to its fast interval. */
export function useStartHarvest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartHarvestRequest) => startHarvest(request),
    onSuccess: () => {
      startedAt.set(queryClient, Date.now());
      void queryClient.invalidateQueries({ queryKey: harvestKeys.running });
      void queryClient.invalidateQueries({ queryKey: harvestKeys.runs });
    },
  });
}

/** Offer a PDF for a candidate that needs one by hand; a filing refreshes the harvest data and the Papers table. */
export function useFileHarvestPdf() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ candidate, file, ...options }: { candidate: HarvestCandidate; file: File } & FileHarvestPdfOptions) =>
      fileHarvestPdf(candidate.id, file, options),
    onSuccess: result => {
      if (!result.filed) return;   // nothing was stored: the user is asked first
      void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
      void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** Undo a filing made from a supplied PDF; refreshes the harvest data and the Papers table. */
export function useUndoHarvestPdf() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (candidateId: string) => undoHarvestPdf(candidateId),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
      void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** Dismiss a candidate that needs a PDF; refreshes the harvest data. */
export function useDismissCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (candidate: HarvestCandidate) => dismissHarvestCandidate(candidate.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
    },
  });
}
