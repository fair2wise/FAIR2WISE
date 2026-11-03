import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { HarvestRun } from '../../harvestApi';
import { HarvestRunsList, runSummary } from './HarvestRunsList';

const run = (id: string, over: Partial<HarvestRun> = {}): HarvestRun => ({
  id, topic: `topic ${id}`, category: 'rsoxs', schema: null, sources: ['arxiv'], max_downloads: 20,
  created_at: '2026-11-03T10:00:00Z',
  counts: { total: 9, downloaded: 4, in_library: 1, paywalled: 2, filed: 1, failed: 1, skipped: 0 }, ...over,
});

describe('HarvestRunsList', () => {
  it('summarises the outcome counts', () => {
    expect(runSummary(run('a'))).toBe('9 found · 5 filed · 2 paywalled · 1 already in library · 1 failed');
    expect(runSummary(run('b', { counts: { total: 0, downloaded: 0, in_library: 0, paywalled: 0, filed: 0, failed: 0, skipped: 0 } })))
      .toBe('0 found · 0 filed');
  });

  it('shows an empty state', () => {
    render(<HarvestRunsList runs={[]} selectedId={null} onSelect={() => undefined} />);
    expect(screen.getByText('No harvest runs yet.')).toBeInTheDocument();
  });

  it('lists runs, marks the open one with aria-pressed and reports a choice', async () => {
    const onSelect = vi.fn();
    render(<HarvestRunsList runs={[run('a'), run('b')]} selectedId="b" onSelect={onSelect} />);
    expect(screen.getByRole('list', { name: 'Harvest runs' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /topic b/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /topic a/ })).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(screen.getByRole('button', { name: /topic a/ }));
    expect(onSelect).toHaveBeenCalledWith('a');
  });
});
