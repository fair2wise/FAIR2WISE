/**
 * TanStack Query hooks for the Harvester panel (A8, T46). Server state only: the form, the watched run
 * and the log stream are owned by the panel components.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  dismissHarvestCandidate,
  fetchHarvestCandidates,
  fetchHarvestRuns,
  fileHarvestPdf,
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

/**
 * The running harvest job, if any: after a page reload this re-attaches the panel to the run.
 * `data` is the job or `null` when none is running.
 */
export function useRunningHarvest(enabled = true, pollMs = 5000) {
  return useQuery<JobInfo | null>({
    queryKey: harvestKeys.running,
    queryFn: async () => {
      const { jobs } = await fetchJobs({ state: 'running', kind: HARVEST_JOB_KIND });
      return jobs[0] ?? null;
    },
    enabled,
    refetchInterval: pollMs,
  });
}

/** Start a harvest run; refreshes the running job and the run list. */
export function useStartHarvest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartHarvestRequest) => startHarvest(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvestKeys.running });
      void queryClient.invalidateQueries({ queryKey: harvestKeys.runs });
    },
  });
}

/** File a PDF for a paywalled candidate; refreshes the harvest data and the Papers table. */
export function useFileHarvestPdf() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ candidate, file }: { candidate: HarvestCandidate; file: File }) =>
      fileHarvestPdf(candidate.id, file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
      void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** Dismiss a paywalled candidate; refreshes the harvest data. */
export function useDismissCandidate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (candidate: HarvestCandidate) => dismissHarvestCandidate(candidate.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
    },
  });
}
