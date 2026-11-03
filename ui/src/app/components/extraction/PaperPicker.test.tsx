import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { LibraryPaper } from '../libraryApi';
import { PaperPicker, paperMatches } from './PaperPicker';

const paper = (id: string, over: Partial<LibraryPaper> = {}): LibraryPaper => ({
  id, relpath: `${id}.pdf`, file_name: `${id}.pdf`, sha256: id, size_bytes: 1, title: `Title ${id}`, doi: null,
  verify_status: 'unverified', verified_at: null, index_status: null, ...over,
});
const PAPERS = [paper('alpha'), paper('beta', { doi: '10.1/xyz' }), paper('gamma')];

function Harness({ disabled = false }: { disabled?: boolean }) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  return <PaperPicker papers={PAPERS} selected={sel} onChange={setSel} disabled={disabled} />;
}

describe('PaperPicker', () => {
  it('matches by file name, title and DOI', () => {
    expect(paperMatches(PAPERS[1], '10.1')).toBe(true);
    expect(paperMatches(PAPERS[0], 'TITLE ALPHA')).toBe(true);
    expect(paperMatches(PAPERS[0], 'zzz')).toBe(false);
  });

  it('counts the selection', async () => {
    render(<Harness />);
    expect(screen.getByTestId('paper-count')).toHaveTextContent('0 of 3 selected');
    await userEvent.click(screen.getByRole('checkbox', { name: /Title alpha/ }));
    expect(screen.getByTestId('paper-count')).toHaveTextContent('1 of 3 selected');
  });

  it('filters the list', async () => {
    render(<Harness />);
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter papers' }), 'beta');
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
  });

  it('selects all shown papers only', async () => {
    render(<Harness />);
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter papers' }), 'a');
    await userEvent.click(screen.getByRole('checkbox', { name: /Select all shown/ }));
    expect(screen.getByTestId('paper-count')).toHaveTextContent('3 of 3 selected');
    await userEvent.clear(screen.getByRole('searchbox', { name: 'Filter papers' }));
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter papers' }), 'beta');
    await userEvent.click(screen.getByRole('checkbox', { name: /Select all shown/ }));
    expect(screen.getByTestId('paper-count')).toHaveTextContent('2 of 3 selected');
  });

  it('shows a no-match message', async () => {
    render(<Harness />);
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter papers' }), 'qqq');
    expect(screen.getByText('No papers match the filter.')).toBeInTheDocument();
  });

  it('disables everything when disabled', () => {
    render(<Harness disabled />);
    expect(screen.getByRole('searchbox')).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /Title alpha/ })).toBeDisabled();
  });
});
