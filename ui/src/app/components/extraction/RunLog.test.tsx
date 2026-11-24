import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { JobLogEntry } from '../extractionApi';
import { RunLog } from './RunLog';

const line = (seq: number, level: JobLogEntry['level'] = 'info', message = `line ${seq}`): JobLogEntry => ({
  seq, at: '2026-11-02T10:11:12Z', level, stage: 'run', message,
});

const LINES = [
  line(1), line(2, 'warning', 'b.pdf page 3: model call timed out after 120 s (cborg, model m)'),
  line(3), line(4, 'error', 'CBORG refused this machine\'s address; check network egress'),
];

function scrollGeometry(pane: HTMLElement, { height, top, client }: { height: number; top: number; client: number }) {
  Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: height });
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: client });
  pane.scrollTop = top;
}

describe('RunLog', () => {
  it('marks warnings and errors and filters to them', async () => {
    const user = userEvent.setup();
    render(<RunLog lines={LINES} total={4} polling={false} error={null} />);
    const rows = () => Array.from(screen.getByTestId('log-pane').querySelectorAll('[data-level]')).map(el => el.getAttribute('data-level'));
    expect(rows()).toEqual(['info', 'warning', 'info', 'error']);
    expect(screen.getByLabelText('warning')).toBeInTheDocument();
    expect(screen.getByLabelText('error')).toBeInTheDocument();
    const all = screen.getByRole('button', { name: 'All' });
    const problems = screen.getByRole('button', { name: /warnings and errors \(2\)/i });
    expect(all).toHaveAttribute('aria-pressed', 'true');
    await user.click(problems);
    expect(problems).toHaveAttribute('aria-pressed', 'true');
    expect(rows()).toEqual(['warning', 'error']);
    expect(screen.getByTestId('log-count')).toHaveTextContent('2 warnings and errors');
    await user.click(all);
    expect(rows()).toHaveLength(4);
  });

  it('says so when the filter hides everything', async () => {
    const user = userEvent.setup();
    render(<RunLog lines={[line(1)]} total={1} polling={false} error={null} />);
    await user.click(screen.getByRole('button', { name: /warnings and errors/i }));
    expect(screen.getByText('No warnings or errors.')).toBeInTheDocument();
  });

  it('says showing last N of M when the server holds more than the page', () => {
    render(<RunLog lines={LINES} total={2500} polling={false} error={null} />);
    expect(screen.getByTestId('log-count')).toHaveTextContent('Showing last 4 of 2500 lines');
  });

  it('says showing all when the page holds every line', () => {
    render(<RunLog lines={LINES} total={4} polling={false} error={null} />);
    expect(screen.getByTestId('log-count')).toHaveTextContent('Showing all 4 lines');
  });

  it('follows the newest line, pauses when the person scrolls up, and resumes on Jump to newest', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<RunLog lines={LINES} total={4} polling error={null} />);
    const pane = screen.getByTestId('log-pane');
    scrollGeometry(pane, { height: 1000, top: 0, client: 200 });
    // at the bottom: the pane follows
    rerender(<RunLog lines={[...LINES, line(5)]} total={5} polling error={null} />);
    expect(pane.scrollTop).toBe(1000);
    expect(screen.queryByRole('button', { name: /jump to newest/i })).not.toBeInTheDocument();
    // the person scrolls up: following pauses and new lines do not move the pane
    scrollGeometry(pane, { height: 1000, top: 300, client: 200 });
    fireEvent.scroll(pane);
    expect(await screen.findByRole('button', { name: /following paused/i })).toBeInTheDocument();
    rerender(<RunLog lines={[...LINES, line(5), line(6)]} total={6} polling error={null} />);
    expect(pane.scrollTop).toBe(300);
    await user.click(screen.getByRole('button', { name: /jump to newest/i }));
    expect(pane.scrollTop).toBe(1000);
    expect(screen.queryByRole('button', { name: /jump to newest/i })).not.toBeInTheDocument();
    // following again
    rerender(<RunLog lines={[...LINES, line(5), line(6), line(7)]} total={7} polling error={null} />);
    expect(pane.scrollTop).toBe(1000);
  });

  it('shows a refresh problem without dropping the lines', () => {
    render(<RunLog lines={LINES} total={4} polling={false} error="Network error" />);
    expect(screen.getByTestId('log-error')).toHaveTextContent('Could not refresh the log (Network error); retrying.');
    expect(screen.getByText(/line 1/)).toBeInTheDocument();
  });
});
