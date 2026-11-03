import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { JobInfo, JobLogEntry } from '../extractionApi';
import { ExtractionRunView, progressPercent, type ExtractionRunViewProps } from './ExtractionRunView';

const job = (over: Partial<JobInfo> = {}): JobInfo => ({
  id: 'j1', kind: 'library_extract', state: 'running', progress: { done: 3, total: 10 }, error: null,
  created_at: '', updated_at: '', ...over,
});
const line = (seq: number, over: Partial<JobLogEntry> = {}): JobLogEntry => ({
  seq, at: '2026-11-02T10:11:12Z', level: 'info', stage: 'extract', message: `line ${seq}`, ...over,
});

function setup(over: Partial<ExtractionRunViewProps> = {}) {
  const props: ExtractionRunViewProps = {
    job: job(), kgName: 'kg1', lines: [], streaming: true, cancelling: false, exporting: false,
    onCancel: vi.fn(), onExport: vi.fn(), onNew: vi.fn(), ...over,
  };
  render(<ExtractionRunView {...props} />);
  return props;
}

describe('ExtractionRunView', () => {
  it('computes the progress percentage', () => {
    expect(progressPercent({ done: 3, total: 10 })).toBe(30);
    expect(progressPercent({})).toBeNull();
    expect(progressPercent({ done: 1, total: 0 })).toBeNull();
  });

  it('shows progress and Cancel while running', async () => {
    const props = setup();
    expect(screen.getByTestId('run-progress-text')).toHaveTextContent('3 of 10 papers (30%)');
    expect(screen.getByRole('progressbar', { name: 'Extraction progress' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(props.onCancel).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /export/i })).not.toBeInTheDocument();
  });

  it('disables Cancel while cancelling', () => {
    setup({ cancelling: true });
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
  });

  it('shows Export and New extraction when finished', async () => {
    const props = setup({ job: job({ state: 'succeeded' }) });
    expect(screen.getByTestId('run-state')).toHaveTextContent('succeeded');
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(props.onExport).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /new extraction/i }));
    expect(props.onNew).toHaveBeenCalled();
  });

  it('disables Export when the KG name is unknown', () => {
    setup({ job: job({ state: 'succeeded' }), kgName: null });
    expect(screen.getByRole('button', { name: /export/i })).toBeDisabled();
  });

  it('shows the error when failed', () => {
    setup({ job: job({ state: 'failed', error: 'model refused' }) });
    expect(screen.getByRole('alert')).toHaveTextContent('model refused');
    expect(screen.queryByRole('button', { name: /export/i })).not.toBeInTheDocument();
  });

  it('shows a cancelled state', () => {
    setup({ job: job({ state: 'cancelled' }) });
    expect(screen.getByTestId('run-state')).toHaveTextContent('cancelled');
  });

  it('renders log lines, and an empty message without lines', () => {
    setup({ lines: [line(1), line(2, { level: 'error', message: 'boom' })] });
    expect(screen.getByRole('log', { name: 'Extraction log' })).toHaveTextContent('boom');
    expect(screen.getByText(/line 1/)).toBeInTheDocument();
  });

  it('shows the empty log message', () => {
    setup();
    expect(screen.getByText('No log lines yet.')).toBeInTheDocument();
  });

  it('notes polling when not streaming', () => {
    setup({ streaming: false });
    expect(screen.getByText(/polling/)).toBeInTheDocument();
  });

  it('scrolls to the newest line', () => {
    const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
    setup({ lines: [line(1)] });
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
