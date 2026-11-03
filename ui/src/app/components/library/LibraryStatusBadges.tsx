import { Badge } from '../ui/badge';
import type { LibraryIndexStatus, LibraryVerifyStatus } from '../libraryApi';

const VERIFY_LABELS: Record<LibraryVerifyStatus, string> = {
  unverified: 'Unverified',
  verified: 'Verified',
  not_found: 'Not found',
  quota_open: 'Quota reached',
  error: 'Error',
};

/**
 * Badge for a paper's verify status. `quota_open` (HTTP 200 from the server) reads as "Quota
 * reached" and `detail` becomes the tooltip.
 */
export function VerifyStatusBadge({ status, detail }: { status: LibraryVerifyStatus; detail?: string | null }) {
  const variant = status === 'verified' ? 'default' : status === 'error' ? 'destructive' : 'secondary';
  return (
    <Badge variant={variant} title={detail ?? undefined}>
      {VERIFY_LABELS[status] ?? status}
    </Badge>
  );
}

/** Badge for a paper's QA index status; `null` (not indexed, or backend without T36) renders "Not indexed". */
export function IndexStatusBadge({ status }: { status: LibraryIndexStatus | undefined }) {
  if (status === 'ok') return <Badge variant="default">Indexed</Badge>;
  if (status === 'no_text') return <Badge variant="destructive">No text</Badge>;
  return <Badge variant="outline">Not indexed</Badge>;
}
