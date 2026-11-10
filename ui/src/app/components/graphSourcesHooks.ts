/** TanStack Query hook for `GET /v1/graph/sources`. */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { fetchGraphSources, type GraphSourceListing } from './graphSourcesApi';

/** Query key of the source listing. */
export const graphSourcesKey = ['graph-sources'] as const;

/**
 * The listing; not retried (an absent route simply hides the selector), refreshed when the viewer opens.
 * `enabled: false` reads the cached listing without requesting it (the node panel of the default graph).
 */
export function useGraphSources({ enabled = true }: { enabled?: boolean } = {}): UseQueryResult<GraphSourceListing> {
  return useQuery({
    queryKey: graphSourcesKey,
    queryFn: () => fetchGraphSources(),
    retry: false,
    staleTime: 15_000,
    enabled,
  });
}
