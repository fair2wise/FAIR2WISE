import { Loader2 } from 'lucide-react';
import { Badge } from '../../ui/badge';
import type { HarvestStatus } from '../../harvestApi';

const LABELS: Record<HarvestStatus, string> = {
  found: 'Found',
  downloading: 'Downloading',
  downloaded: 'Downloaded',
  in_library: 'In library',
  paywalled: 'Paywalled',
  blocked: 'Blocked by network policy',
  filed: 'Filed',
  failed: 'Failed',
  skipped: 'Skipped',
  dismissed: 'Dismissed',
};

/** Badge for a candidate's outcome; `detail` (why it failed or was skipped) becomes the tooltip. */
export function HarvestStatusBadge({ status, detail }: { status: HarvestStatus; detail?: string | null }) {
  const variant = status === 'downloaded' || status === 'filed' ? 'default'
    : status === 'failed' ? 'destructive'
    : status === 'paywalled' || status === 'blocked' ? 'outline'
    : 'secondary';
  return (
    <Badge variant={variant} title={detail ?? undefined}>
      {status === 'downloading' ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
      {LABELS[status] ?? status}
    </Badge>
  );
}
