/** TanStack Query hook for `GET /v1/capabilities` (T18D). */
import { useQuery } from '@tanstack/react-query';
import { fetchCapabilities, type Capabilities } from './capabilitiesApi';

/** Query key for the caller's capabilities. */
export const capabilitiesKey = ['capabilities'] as const;

/**
 * The caller's capabilities, or `undefined` while loading or after any failure (404, 401, network).
 * Failures are not retried: the UI simply keeps its default behaviour.
 */
export function useCapabilities(): Capabilities | undefined {
  return useQuery({
    queryKey: capabilitiesKey,
    queryFn: () => fetchCapabilities(),
    retry: false,
    staleTime: 60_000,
  }).data;
}
