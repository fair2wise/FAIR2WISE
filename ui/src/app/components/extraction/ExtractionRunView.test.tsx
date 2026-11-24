import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { JobInfo, JobLogEntry, JobRunSummary } from '../extractionApi';
import { ExtractionRunView, progressPercent, type ExtractionRunViewProps } from './ExtractionRunView';

const job = (over: Partial<JobInfo> = {}): JobInfo => ({
  id: 'j1', kind: 'library_extract', state: 'running', progress: { done: 3, total: 10 }, error: null,
  created_at: '', updated_at: '', ...over,
});
const summary = (over: Partial<JobRunSummary> = {}): JobRunSummary => ({
  outcome: 'running', state: 'running', started_at: '2026-11-23T10:00:00+00:00', finished_at: null, elapsed_seconds: 125,
  stage: { name: 'run', status: 'running', phase: 'extracting' }, current_paper: 'rsoxs-p3ht.pdf',
  papers: { total: 10, done: 3, failed: 0, skipped: 1 },
  growth: { entities_new: 40, entities_merged: 7, relations_new: 12, relations_merged: 2, entities_total: 90, relations_total: 31 },
  counters: { paper_pages_done: 4, paper_pages_total: 12 },
  params: { bundle: 'kg1', model: 'qwen-72b', backend: 'cborg' }, error: null,
  logs: { kept: 5, total: 5, cap: 2000 }, ...over,
});
const line = (seq: number, over: Partial<JobLogEntry> = {}): JobLogEntry => ({
  seq, at: '2026-11-02T10:11:12Z', level: 'info', stage: 'extract', message: `line ${seq}`, ...over,
});

function setup(over: Partial<ExtractionRunViewProps> = {}) {
  const props: ExtractionRunViewProps = {
    job: job(), kgName: 'kg1', lines: [], cancelling: false, exporting: false,
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

  it('shows phase, papers done of total, the current paper, elapsed time and KG growth from the summary', () => {
    setup({ job: job({ summary: summary() }) });
    expect(screen.getByTestId('run-progress-text')).toHaveTextContent('3 of 10 papers done, 1 already in the KG (30%)');
    expect(screen.getByTestId('run-elapsed')).toHaveTextContent('elapsed 2 min 05 s'); // the server's 125 s
    const phases = screen.getByRole('list', { name: 'Stage of the current paper' });
    expect(within(phases).getByText('extracting')).toHaveAttribute('aria-current', 'step');
    expect(within(phases).getByText('parsing')).not.toHaveAttribute('aria-current');
    expect(screen.getByTestId('run-current-paper')).toHaveTextContent('Now: rsoxs-p3ht.pdf (page 4 of 12)');
    const growth = screen.getByTestId('run-growth');
    expect(growth).toHaveTextContent('40 new · 7 merged');
    expect(growth).toHaveTextContent('12 new · 2 merged');
    expect(growth).toHaveTextContent('90 in the KG');
    expect(screen.getByTestId('run-model')).toHaveTextContent('cborg · qwen-72b');
  });

  it('never shows a file system path: only the display name the server sent', () => {
    setup({ job: job({ summary: summary({ current_paper: 'a.pdf' }) }) });
    expect(screen.getByTestId('run-now').textContent).not.toMatch(/\//);
  });

  it('disables Cancel while cancelling', () => {
    setup({ cancelling: true });
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled();
  });

  it('shows Export and New extraction when succeeded', async () => {
    const props = setup({ job: job({ state: 'succeeded', summary: summary({ outcome: 'succeeded', state: 'succeeded', stage: { name: 'run', status: 'completed', phase: null }, current_paper: null }) }) });
    expect(screen.getByTestId('run-state')).toHaveTextContent('Succeeded');
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId('run-now')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(props.onExport).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: /new extraction/i }));
    expect(props.onNew).toHaveBeenCalled();
  });

  it('disables Export when the KG name is unknown', () => {
    setup({ job: job({ state: 'succeeded' }), kgName: null });
    expect(screen.getByRole('button', { name: /export/i })).toBeDisabled();
  });

  it('takes the KG name from the run itself, so Export works after a reload', () => {
    setup({ job: job({ state: 'succeeded', summary: summary({ outcome: 'succeeded', state: 'succeeded' }) }), kgName: null });
    expect(screen.getByRole('button', { name: /export/i })).toBeEnabled();
  });

  it('shows the error when failed', () => {
    setup({ job: job({ state: 'failed', error: 'model refused' }) });
    expect(screen.getByRole('alert')).toHaveTextContent('model refused');
    expect(screen.queryByRole('button', { name: /export/i })).not.toBeInTheDocument();
  });

  it('says partially failed with the failed papers, and still allows Export of what finished', () => {
    const papers = { total: 3, done: 2, failed: 1, skipped: 0, failed_list: [{ name: 'b.pdf', reason: 'the PDF could not be read' }] };
    setup({
      job: job({
        state: 'failed', error: 'incomplete coverage: 1 unit(s) missing',
        summary: summary({ outcome: 'partially_failed', state: 'failed', papers, current_paper: null }),
      }),
    });
    expect(screen.getByTestId('run-state')).toHaveTextContent('Partially failed (1 of 3 papers failed)');
    expect(screen.getByTestId('failed-papers')).toHaveTextContent('b.pdf: the PDF could not be read');
    expect(screen.getByRole('button', { name: /export/i })).toBeEnabled();
  });

  it('shows a cancelled state', () => {
    setup({ job: job({ state: 'cancelled' }) });
    expect(screen.getByTestId('run-state')).toHaveTextContent('cancelled');
  });

  it('renders log lines with errors and warnings marked, and the count', () => {
    setup({
      lines: [line(1), line(2, { level: 'error', message: 'boom' }), line(3, { level: 'warning', message: 'slow' })],
      logTotal: 7,
    });
    expect(screen.getByRole('log', { name: 'Extraction log' })).toHaveTextContent('boom');
    expect(screen.getByText(/line 1/)).toBeInTheDocument();
    expect(screen.getByTestId('log-count')).toHaveTextContent('Showing last 3 of 7 lines');
  });

  it('shows the empty log message', () => {
    setup();
    expect(screen.getByText('No log lines yet.')).toBeInTheDocument();
  });

  it('notes that the log is updating while polled', () => {
    setup({ polling: true });
    expect(screen.getByTestId('log-count')).toHaveTextContent('updating');
  });

  it('shows what Export did, with a Download link, once the KG has been exported', () => {
    setup({ job: job({ state: 'succeeded', summary: summary({ outcome: 'succeeded', state: 'succeeded' }) }),
      exported: { name: 'kg1', file_name: 'matkg_kg1.json', size_bytes: 1024 } });
    expect(screen.getByTestId('export-notice')).toHaveTextContent('Exported matkg_kg1.json (1.0 KB)');
    expect(screen.getByRole('link', { name: /Download matkg_kg1\.json/ })).toBeInTheDocument();
  });

  it('shows no export notice before an export', () => {
    setup();
    expect(screen.queryByTestId('export-notice')).not.toBeInTheDocument();
  });
});
