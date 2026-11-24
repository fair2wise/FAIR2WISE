import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryPaper } from '../libraryApi';
import { LibraryPapersTable } from './LibraryPapersTable';

const paper = (over: Partial<LibraryPaper> = {}): LibraryPaper => ({
  id: 'p1',
  relpath: 'a.pdf',
  folder: '',
  file_name: 'a.pdf',
  sha256: 'x',
  size_bytes: 1,
  title: 'A Title',
  doi: '10.1/abc',
  verify_status: 'verified',
  verified_at: null,
  index_status: 'ok',
  ...over,
});

describe('LibraryPapersTable supplied-by-hand badge', () => {
  it.each([
    ['user_supplied', 'Supplied by hand'],
    ['user_supplied_confirmed', 'Supplied by hand'],
    ['user_supplied_mismatch_confirmed', 'Supplied by hand (mismatch)'],
  ] as const)('shows a small badge for provenance %s', (provenance, label) => {
    render(<LibraryPapersTable papers={[paper({ provenance })]} verifyingId={null} onVerify={() => undefined} />);
    const badge = screen.getByTestId('supplied-badge');
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveAttribute('data-provenance', provenance);
    expect(badge).toHaveAttribute('title');
  });

  it.each([undefined, null, 'harvest', 'something_else'])('shows no badge for provenance %s', provenance => {
    render(<LibraryPapersTable papers={[paper({ provenance: provenance as LibraryPaper['provenance'] })]}
      verifyingId={null} onVerify={() => undefined} />);
    expect(screen.queryByTestId('supplied-badge')).not.toBeInTheDocument();
  });
});

describe('LibraryPapersTable', () => {
  it('renders file name, title, DOI and status badges, truncating long text', () => {
    render(<LibraryPapersTable papers={[paper()]} verifyingId={null} onVerify={() => undefined} />);
    expect(screen.getByText('a.pdf')).toHaveClass('truncate');
    expect(screen.getByText('A Title')).toBeInTheDocument();
    expect(screen.getByText('10.1/abc')).toBeInTheDocument();
    expect(screen.getByText('Verified')).toBeInTheDocument();
    expect(screen.getByText('Indexed')).toBeInTheDocument();
  });

  it('renders null title, DOI and index status gracefully', () => {
    render(
      <LibraryPapersTable
        papers={[paper({ title: null, doi: null, index_status: null, verify_status: 'quota_open' })]}
        verifyingId={null}
        onVerify={() => undefined}
      />,
    );
    expect(screen.getByText('Untitled')).toBeInTheDocument();
    expect(screen.getByText('No DOI')).toBeInTheDocument();
    expect(screen.getByText('Not indexed')).toBeInTheDocument();
    expect(screen.getByText('Quota reached')).toBeInTheDocument();
  });

  it('shows the no_text index state', () => {
    render(<LibraryPapersTable papers={[paper({ index_status: 'no_text' })]} verifyingId={null} onVerify={() => undefined} />);
    expect(screen.getByText('No text')).toBeInTheDocument();
  });

  it('empty: shows the empty state', () => {
    render(<LibraryPapersTable papers={[]} verifyingId={null} onVerify={() => undefined} />);
    expect(screen.getByText(/no papers yet/i)).toBeInTheDocument();
  });

  it('calls onVerify with the paper id', async () => {
    const onVerify = vi.fn();
    render(<LibraryPapersTable papers={[paper()]} verifyingId={null} onVerify={onVerify} />);
    await userEvent.click(screen.getByRole('button', { name: 'Verify a.pdf' }));
    expect(onVerify).toHaveBeenCalledWith('p1');
  });

  it('verifyDisabled and verifyingId disable the Verify buttons', () => {
    const { rerender } = render(
      <LibraryPapersTable papers={[paper()]} verifyingId={null} verifyDisabled onVerify={() => undefined} />,
    );
    expect(screen.getByRole('button', { name: 'Verify a.pdf' })).toBeDisabled();
    rerender(<LibraryPapersTable papers={[paper()]} verifyingId="p1" onVerify={() => undefined} />);
    expect(screen.getByRole('button', { name: 'Verify a.pdf' })).toBeDisabled();
  });

  it('shows the top-level folder, and Uncategorized for files in the root', () => {
    render(
      <LibraryPapersTable
        papers={[
          paper({ id: 'p1', folder: 'rsoxs', relpath: 'rsoxs/2013/a.pdf' }),
          paper({ id: 'p2', file_name: 'b.pdf', relpath: 'b.pdf' }),
        ]}
        verifyingId={null}
        onVerify={() => undefined}
      />,
    );
    expect(screen.getByText('rsoxs')).toHaveAttribute('title', 'rsoxs/2013/a.pdf');
    expect(screen.getByText('Uncategorized')).toBeInTheDocument();
  });
});

describe('LibraryPapersTable stored metadata', () => {
  it('shows year, the first authors with +N, and an abstract collapsed behind a toggle', async () => {
    render(
      <LibraryPapersTable
        papers={[paper({
          year: 2014, year_source: 'manifest', authors: ['A One', 'B Two', 'C Three', 'D Four', 'E Five'], authors_total: 7,
          abstract: 'We study polymer blends.', metadata_missing: [],
        })]}
        verifyingId={null}
        onVerify={() => undefined}
      />,
    );
    expect(screen.getByText('2014')).toBeInTheDocument();
    expect(screen.getByText(/A One, B Two, C Three/)).toBeInTheDocument();
    expect(screen.getByText('+4')).toBeInTheDocument();
    expect(screen.getByText('+4').closest('span[title]')).toHaveAttribute('title', expect.stringContaining('and 2 more'));
    const toggle = screen.getByRole('button', { name: 'Show abstract of a.pdf' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('We study polymer blends.')).toHaveClass('line-clamp-2');
    await userEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Hide abstract of a.pdf' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('We study polymer blends.')).not.toHaveClass('line-clamp-2');
  });

  it('marks every field the server does not hold as "not stored" rather than leaving it blank', () => {
    render(
      <LibraryPapersTable
        papers={[paper({ year: null, authors: null, abstract: null, metadata_missing: ['year', 'authors', 'abstract'] })]}
        verifyingId={null}
        onVerify={() => undefined}
      />,
    );
    expect(screen.getAllByText('not stored')).toHaveLength(3);
    expect(screen.queryByRole('button', { name: /abstract/ })).not.toBeInTheDocument();
  });

  it('treats an older server that sends none of the fields as not stored', () => {
    render(<LibraryPapersTable papers={[paper()]} verifyingId={null} onVerify={() => undefined} />);
    expect(screen.getAllByText('not stored')).toHaveLength(3);
  });

  it('says when the year was taken from the folder name', () => {
    render(<LibraryPapersTable papers={[paper({ year: 2021, year_source: 'folder' })]} verifyingId={null} onVerify={() => undefined} />);
    expect(screen.getByText('2021')).toHaveAttribute('title', 'Year taken from the year folder');
  });
});
