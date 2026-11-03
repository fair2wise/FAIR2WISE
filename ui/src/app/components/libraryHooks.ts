/**
 * TanStack Query hooks for the Library tab (A8, T40), plus the probe-and-hide hook that decides
 * whether the Library and Extract tabs exist at all.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchLibraryPapers,
  fetchLibraryStatus,
  indexLibraryPapers,
  uploadLibraryPaper,
  verifyAllLibraryPapers,
  verifyLibraryPaper,
  type LibraryStatus,
} from './libraryApi';

/** Query keys for library data. */
export const libraryKeys = {
  all: ['library'] as const,
  status: ['library', 'status'] as const,
  papers: ['library', 'papers'] as const,
};

/** Result of `useLibraryProbe`. */
export interface LibraryProbe {
  /** True once the server answered and exposes `/v1/library` (tabs show). False while loading. */
  available: boolean;
  /** True while the first probe is in flight. */
  isLoading: boolean;
  /** The status payload, or `null` when hidden or not yet known. */
  status: LibraryStatus | null;
}

/**
 * Probe `GET /v1/library` once and hide the Library/Extract tabs when it is 404 (ALS), 401/403/405
 * or unreachable. Tabs stay hidden while the probe is loading to avoid a flash. A mounted-less
 * `:local` server still reports `available` (the tab shows its "not mounted" state).
 */
export function useLibraryProbe(): LibraryProbe {
  const query = useQuery({
    queryKey: libraryKeys.status,
    queryFn: () => fetchLibraryStatus(),
    retry: false,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
  const status = query.data ?? null;
  return { available: status !== null, isLoading: query.isPending, status };
}

/**
 * The paper list; enabled only when the library is available.
 *
 * @param enabled - Fetch only when true.
 * @param pollMs - Refetch interval while a job runs (it also polls every 3 s while `pending_metadata` > 0); `false` (default) disables polling.
 */
export function useLibraryPapers(enabled = true, pollMs: number | false = false) {
  return useQuery({
    queryKey: libraryKeys.papers,
    queryFn: () => fetchLibraryPapers(),
    enabled,
    refetchInterval: query => pollMs || ((query.state.data?.pending_metadata ?? 0) > 0 ? 3000 : false),
  });
}

/** Upload one PDF; refreshes the list and status on success. */
export function useUploadPaper() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, folder }: { file: File; folder?: string }) => uploadLibraryPaper(file, { folder }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKeys.all }),
  });
}

/** Verify one paper by id; refreshes the list. */
export function useVerifyPaper() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (paperId: string) => verifyLibraryPaper(paperId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKeys.all }),
  });
}

/** Bulk Verify (`library_verify` job). */
export function useVerifyAllPapers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (nonce?: string) => verifyAllLibraryPapers(nonce),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKeys.all }),
  });
}

/** Index for QA (`library_index` job). */
export function useIndexLibrary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (nonce?: string) => indexLibraryPapers(nonce),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKeys.all }),
  });
}
