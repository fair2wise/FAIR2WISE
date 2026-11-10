/** TanStack Query hooks for the Publish to Tiled action (owner decision 2026-11-10). */
import { useMutation, useQuery } from '@tanstack/react-query';
import { fetchPublishView, previewPublish, publishSettled, startPublish } from './publishApi';

/** Preview of a publish (a mutation: an explicit user step that writes nothing). */
export function usePublishPreview() {
  return useMutation({ mutationFn: (vars: { name: string; again: boolean }) => previewPublish(vars.name, vars.again) });
}

/** Submit the publish job. */
export function useStartPublish() {
  return useMutation({ mutationFn: (vars: { name: string; again: boolean }) => startPublish(vars.name, vars.again) });
}

/** The publish job's view, polled until the job failed or finished with the plan visible. */
export function usePublishView(jobId: string | null, pollMs = 2000) {
  return useQuery({
    queryKey: ['extraction', 'publish', jobId ?? ''],
    queryFn: () => fetchPublishView(jobId as string),
    enabled: jobId !== null,
    refetchInterval: query => (publishSettled(query.state.data) ? false : pollMs),
  });
}
