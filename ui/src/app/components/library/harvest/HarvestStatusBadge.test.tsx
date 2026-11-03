import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { HarvestStatus } from '../../harvestApi';
import { HarvestStatusBadge } from './HarvestStatusBadge';

describe('HarvestStatusBadge', () => {
  it.each<[HarvestStatus, string]>([
    ['downloaded', 'Downloaded'], ['in_library', 'In library'], ['paywalled', 'Paywalled'], ['filed', 'Filed'],
    ['failed', 'Failed'], ['skipped', 'Skipped'], ['dismissed', 'Dismissed'],
  ])('labels %s', (status, label) => {
    render(<HarvestStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('shows the detail as a tooltip', () => {
    render(<HarvestStatusBadge status="failed" detail="HTTP 403" />);
    expect(screen.getByText('Failed')).toHaveAttribute('title', 'HTTP 403');
  });
});
