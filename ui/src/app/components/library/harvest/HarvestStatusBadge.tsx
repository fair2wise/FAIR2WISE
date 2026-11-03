import { Badge } from '../../ui/badge';
import type { HarvestStatus } from '../../harvestApi';

const LABELS: Record<HarvestStatus, string> = {
  downloaded: 'Downloaded',
  in_library: 'In library',
  paywalled: 'Paywalled',
  filed: 'Filed',
  failed: 'Failed',
  skipped: 'Skipped',
  dismissed: 'Dismissed',
};

/** Badge for a candidate's outcome; `detail` (why it failed or was skipped) becomes the tooltip. */
export function HarvestStatusBadge({ status, detail }: { status: HarvestStatus; detail?: string | null }) {
  const variant = status === 'downloaded' || status === 'filed' ? 'default'
    : status === 'failed' ? 'destructive'
    : status === 'paywalled' ? 'outline'
    : 'secondary';
  return (
    <Badge variant={variant} title={detail ?? undefined}>
      {LABELS[status] ?? status}
    </Badge>
  );
}
