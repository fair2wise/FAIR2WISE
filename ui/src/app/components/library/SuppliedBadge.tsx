import type { HarvestProvenance } from '../harvestApi';
import { Badge } from '../ui/badge';

const TEXT: Record<HarvestProvenance, { label: string; title: string }> = {
  user_supplied: {
    label: 'Supplied by hand',
    title: 'A person supplied this PDF; it matched the paper strongly (its DOI was in the PDF).',
  },
  user_supplied_confirmed: {
    label: 'Supplied by hand',
    title: 'A person supplied this PDF and confirmed it after a weak match (the PDF could not be fully checked).',
  },
  user_supplied_mismatch_confirmed: {
    label: 'Supplied by hand (mismatch)',
    title: 'A person supplied this PDF and confirmed it although it names a different DOI than the paper '
      + '(for example a preprint or repository copy).',
  },
};

/** True for a provenance value that means a person supplied the file. */
export function isSuppliedProvenance(value: string | null | undefined): value is HarvestProvenance {
  return !!value && Object.prototype.hasOwnProperty.call(TEXT, value);
}

/** A small badge for a paper whose PDF a person supplied by hand; renders nothing for any other provenance. */
export function SuppliedBadge({ provenance }: { provenance: string | null | undefined }) {
  if (!isSuppliedProvenance(provenance)) return null;
  const { label, title } = TEXT[provenance];
  return (
    <Badge variant="outline" title={title} data-testid="supplied-badge" data-provenance={provenance}>
      {label}
    </Badge>
  );
}
