import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { JobInfo, JobLogEntry } from '../../extractionApi';
import { HarvestRunView, type HarvestRunViewProps } from './HarvestRunView';

const job = (over: Partial<JobInfo> = {}): JobInfo => ({
  id: 'j1', kind: 'library_harvest', state: 'running', progress: { done: 2, total: 8 }, error: null,
  created_at: '', updated_at: '', ...over,
});
const line = (seq: number, over: Partial<JobLogEntry> = {}): JobLogEntry => ({
  seq, at: '2026-11-03T10:11:12Z', level: 'info', stage: 'search', message: `line ${seq}`, ...over,
});

function setup(over: Partial<HarvestRunViewProps> = {}) {
  const props: HarvestRunViewProps = {
    job: job(), topic: 'rsoxs', lines: [], streaming: true, cancelling: false,
    onCancel: vi.fn(), onClose: vi.fn(), ...over,
  };
  render(<HarvestRunView {...props} />);
  return props;
}

describe('HarvestRunView', () => {
  it('shows the topic, state, progress and an empty log', () => {
    setup();
    expect(screen.getByRole('heading', { name: /rsoxs/ })).toBeInTheDocument();
    expect(screen.getByTestId('harvest-state')).toHaveTextContent('running');
    expect(screen.getByTestId('harvest-progress-text')).toHaveTextContent('2 of 8 (25%)');
    expect(screen.getByRole('progressbar', { name: 'Harvest progress' })).toBeInTheDocument();
    expect(screen.getByText('No log lines yet.')).toBeInTheDocument();
  });

  it('says searching while progress is unknown and Harvest without a topic', () => {
    setup({ job: job({ progress: {} }), topic: null });
    expect(screen.getByTestId('harvest-progress-text')).toHaveTextContent('Searching…');
    expect(screen.getByRole('heading', { name: 'Harvest' })).toBeInTheDocument();
  });

  it('renders log lines with their level and notes polling when not streaming', () => {
    setup({ lines: [line(1), line(2, { level: 'error', message: 'boom' })], streaming: false });
    expect(screen.getByRole('log', { name: 'Harvest log' })).toHaveTextContent('line 1');
    expect(screen.getByText(/boom/)).toHaveClass('text-destructive');
    expect(screen.getByText(/polling/)).toBeInTheDocument();
  });

  it('offers Cancel while running and disables it while cancelling', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(props.onCancel).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: 'Back to the form' })).not.toBeInTheDocument();
  });

  it('disables Cancel while cancelling', () => {
    setup({ cancelling: true });
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });

  it.each(['succeeded', 'failed', 'cancelled'] as const)('offers Back to the form once %s', async state => {
    const props = setup({ job: job({ state, error: state === 'failed' ? 'bad' : null }) });
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Back to the form' }));
    expect(props.onClose).toHaveBeenCalledOnce();
    if (state === 'failed') expect(screen.getByRole('alert')).toHaveTextContent('bad');
  });
});
