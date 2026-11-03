/**
 * TanStack Query hooks for the Extraction tab (A8, T40). Server state only; the log stream and form
 * state are owned by the T42 components.
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
  fetchKgBundles,
  fetchSchemas,
  startExtraction,
  TERMINAL_JOB_STATES,
  type ContinueKgRequest,
  type EstimateRequest,
  type JobInfo,
  type StartExtractionRequest,
} from './extractionApi';

/** Query keys for extraction data. */
export const extractionKeys = {
  bundles: ['extraction', 'kgs'] as const,
  schemas: ['extraction', 'schemas'] as const,
  models: ['extraction', 'models'] as const,
  running: ['extraction', 'running'] as const,
  job: (jobId: string) => ['extraction', 'job', jobId] as const,
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

/**
 * The running extraction job, if any: after a page reload this re-attaches the tab to the run.
 * Polls while enabled; `data` is the job or `null` when none is running.
 */
export function useRunningExtraction(enabled = true, pollMs = 5000) {
  return useQuery<JobInfo | null>({
    queryKey: extractionKeys.running,
    queryFn: async () => {
      const { jobs } = await fetchJobs({ state: 'running', kind: EXTRACT_JOB_KIND });
      return jobs[0] ?? null;
    },
    enabled,
    refetchInterval: pollMs,
  });
}

/** One job, polled until it reaches a terminal state. */
export function useJob(jobId: string | null, pollMs = 2000) {
  return useQuery({
    queryKey: extractionKeys.job(jobId ?? ''),
    queryFn: () => fetchJob(jobId as string),
    enabled: jobId !== null,
    refetchInterval: query => {
      const state = query.state.data?.state;
      return state && TERMINAL_JOB_STATES.has(state) ? false : pollMs;
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
      void queryClient.invalidateQueries({ queryKey: extractionKeys.running });
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
      void queryClient.invalidateQueries({ queryKey: extractionKeys.running });
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
