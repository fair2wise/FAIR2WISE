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
