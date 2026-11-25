/**
 * TanStack Query hooks for the Extraction tab (A8, T40). Server state only; the log poller and form
 * state are owned by the T42 components. Polling hooks take `live` (tab on screen and page visible) and stop
 * their timers while it is false.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelJob,
  continueKg,
  estimateExtraction,
  EXTRACT_JOB_KIND,
  exportKgBundle,
  fetchExtractionModels,
  fetchJob,
  fetchJobs,
  fetchJobSummary,
  fetchKgBundles,
  fetchSchemas,
  startExtraction,
  startPassages,
  TERMINAL_JOB_STATES,
  type ContinueKgRequest,
  type EstimateRequest,
  type JobInfo,
  type JobRunSummary,
  type StartExtractionRequest,
} from './extractionApi';

/** Query keys for extraction data. */
export const extractionKeys = {
  bundles: ['extraction', 'kgs'] as const,
  schemas: ['extraction', 'schemas'] as const,
  models: ['extraction', 'models'] as const,
  running: ['extraction', 'running'] as const,
  runs: ['extraction', 'runs'] as const,
  job: (jobId: string) => ['extraction', 'job', jobId] as const,
  summary: (jobId: string) => ['extraction', 'summary', jobId] as const,
};

/** KG bundles for the continue-or-new picker. */
export function useKgBundles(enabled = true) {
  return useQuery({ queryKey: extractionKeys.bundles, queryFn: () => fetchKgBundles(), enabled });
}

/** Schema registry choices. */
export function useSchemas(enabled = true) {
  return useQuery({ queryKey: extractionKeys.schemas, queryFn: () => fetchSchemas(), enabled });
}

/** Live model list (never the Settings model). */
export function useExtractionModels(enabled = true) {
  return useQuery({ queryKey: extractionKeys.models, queryFn: () => fetchExtractionModels(), enabled });
}

/** Most runs the list shows (the server keeps `detail=summary` pages to 50). */
export const MAX_RUNS = 20;

/** Poll period of a moving run, a settled list, and a run that waits for a person or after errors. */
export const FAST_POLL_MS = 2000;
export const SLOW_POLL_MS = 10_000;

const isMoving = (state: string | undefined) => state === 'running' || state === 'queued';

/**
 * Recent `library_extract` runs, newest first, each with its live summary (outcome, papers, KG growth): the
 * runs list, and how a reloaded page finds the running job (or the latest one). Polls every 2 s while a run is
 * moving and every 10 s otherwise (also after errors); not at all while the tab or the page is hidden, and it
 * fetches again as soon as it is shown. State lives on the server, not in this component.
 *
 * @param live - Tab on screen and page visible (`useLive`).
 */
export function useExtractionRuns(live: boolean, limit = MAX_RUNS) {
  return useQuery<JobInfo[]>({
    queryKey: [...extractionKeys.runs, limit],
    queryFn: async () => (await fetchJobs({ kind: EXTRACT_JOB_KIND, detail: 'summary', limit })).jobs,
    enabled: live,
    refetchInterval: query => {
      if (query.state.status === 'error') return SLOW_POLL_MS;
      return query.state.data?.some(job => isMoving(job.state)) ? FAST_POLL_MS : SLOW_POLL_MS;
    },
  });
}

/**
 * One job's state, polled every 2 s while it moves, every 10 s while paused or after errors, and never once
 * it is terminal or while `live` is false.
 */
export function useJob(jobId: string | null, pollMs = FAST_POLL_MS, live = true) {
  return useQuery({
    queryKey: extractionKeys.job(jobId ?? ''),
    queryFn: () => fetchJob(jobId as string),
    enabled: jobId !== null && live,
    refetchInterval: query => {
      const state = query.state.data?.state;
      if (state && TERMINAL_JOB_STATES.has(state)) return false;
      if (query.state.status === 'error') return SLOW_POLL_MS;
      return state && !isMoving(state) ? SLOW_POLL_MS : pollMs;
    },
  });
}

/**
 * The live summary of one job (`GET /v1/jobs/{id}/progress`): stage, papers, current paper, KG growth, outcome. Same
 * schedule as {@link useJob}; `null` data from a server that has no summary. A job seen terminal is fetched once more
 * (the poll that saw the state) and then left alone.
 */
export function useJobSummary(jobId: string | null, pollMs = FAST_POLL_MS, live = true) {
  return useQuery<JobRunSummary | null>({
    queryKey: extractionKeys.summary(jobId ?? ''),
    queryFn: () => fetchJobSummary(jobId as string),
    enabled: jobId !== null && live,
    refetchInterval: query => {
      const state = query.state.data?.state;
      if (state && TERMINAL_JOB_STATES.has(state)) return false;
      if (query.state.status === 'error') return SLOW_POLL_MS;
      return state && !isMoving(state) ? SLOW_POLL_MS : pollMs;
    },
  });
}

/** Cost estimate (a mutation: it is an explicit user step, deterministic on the server). */
export function useEstimateExtraction() {
  return useMutation({ mutationFn: (request: EstimateRequest) => estimateExtraction(request) });
}

/** Start an extraction; refreshes the running job and bundle list. */
export function useStartExtraction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartExtractionRequest) => startExtraction(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: extractionKeys.runs });
      void queryClient.invalidateQueries({ queryKey: extractionKeys.bundles });
    },
  });
}

/** Start "Find supporting text" for a bundle; refreshes the bundle list (a finished run changes what it holds). */
export function useStartPassages() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, nonce }: { name: string; nonce: string }) => startPassages(name, nonce),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: extractionKeys.bundles });
    },
  });
}

/** Cancel a job. */
export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => cancelJob(jobId),
    onSuccess: (_job, jobId) => {
      void queryClient.invalidateQueries({ queryKey: extractionKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: extractionKeys.summary(jobId) });
      void queryClient.invalidateQueries({ queryKey: extractionKeys.runs });
    },
  });
}

/** Export a bundle to the release directory. */
export function useExportKg() {
  return useMutation({ mutationFn: (name: string) => exportKgBundle(name) });
}

/** Create a child bundle from a bundle or a plain KG file (continue an existing KG). */
export function useContinueKg() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ContinueKgRequest) => continueKg(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: extractionKeys.bundles });
    },
  });
}
