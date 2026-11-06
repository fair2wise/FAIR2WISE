/** TanStack Query hook for `GET /v1/graph/sources`. */
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { fetchGraphSources, type GraphSourceListing } from './graphSourcesApi';

/** Query key of the source listing. */
export const graphSourcesKey = ['graph-sources'] as const;

/** The listing; not retried (an absent route simply hides the selector), refreshed when the viewer opens. */
export function useGraphSources(): UseQueryResult<GraphSourceListing> {
  return useQuery({
    queryKey: graphSourcesKey,
    queryFn: () => fetchGraphSources(),
    retry: false,
    staleTime: 15_000,
  });
}
