/**
 * Task 4 (owner bug list 2026-11): the node card lists, per paper, the text snippets that support the entity:
 * collapsed, bounded with "Show more", plain text only, and nothing when the node carries none.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { PublicationInfo } from './data/liveAgent';
import { withoutSnippets } from './data/liveAgent';
import { PublicationList, SNIPPETS_COLLAPSED, paperSnippets } from './PublicationList';
import { readBookmarkedPublications, togglePublicationFavorite } from './publicationFavorites';

const passages = (count: number) => Array.from({ length: count }, (_, index) => ({ text: `passage ${index + 1}`, page: index + 1 }));
const paper = (extra: Partial<PublicationInfo> = {}): PublicationInfo => ({
  source_paper: 'doi_10.1000_xyz.pdf', paper_title: 'Edge-on packing in P3HT', doi: '10.1000/xyz', ...extra,
});

afterEach(() => window.localStorage.clear());

describe('per-paper supporting snippets', () => {
  it('is collapsed by default and lists the snippets of that paper on demand', () => {
    render(<PublicationList publications={[paper({ snippets: passages(2), snippet_count: 2 })]} intro={null} showSnippets />);
    expect(screen.queryByText('passage 1')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show supporting text (2)' }));
    expect(screen.getByText('passage 1')).toBeInTheDocument();
    expect(screen.getByText('passage 2')).toBeInTheDocument();
    expect(screen.getByText('(p. 2)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide supporting text (2)' }));
    expect(screen.queryByText('passage 1')).toBeNull();
  });

  it('shows a bounded number and the rest behind "Show more"', () => {
    render(<PublicationList publications={[paper({ snippets: passages(8), snippet_count: 8 })]} intro={null} showSnippets />);
    fireEvent.click(screen.getByRole('button', { name: /supporting text/ }));
    expect(screen.getAllByRole('listitem')).toHaveLength(SNIPPETS_COLLAPSED);
    fireEvent.click(screen.getByRole('button', { name: `Show ${8 - SNIPPETS_COLLAPSED} more` }));
    expect(screen.getAllByRole('listitem')).toHaveLength(8);
  });

  it('keeps each paper\'s snippets under that paper', () => {
    render(<PublicationList
      publications={[
        paper({ paper_title: 'Paper A', source_paper: 'a.pdf', doi: undefined, snippets: [{ text: 'from A' }], snippet_count: 1 }),
        paper({ paper_title: 'Paper B', source_paper: 'b.pdf', doi: undefined }),
      ]}
      intro={null}
      showSnippets
    />);
    expect(screen.getAllByTestId('paper-snippets')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: /supporting text/ }));
    expect(screen.getByText('from A')).toBeInTheDocument();
  });

  it('renders text as plain text, never as HTML', () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b> & <script>alert(1)</script>';
    const { container } = render(<PublicationList publications={[paper({ snippets: [{ text: hostile }], snippet_count: 1 })]} intro={null} showSnippets />);
    fireEvent.click(screen.getByRole('button', { name: /supporting text/ }));
    expect(screen.getByRole('listitem').textContent).toContain(hostile);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('li b')).toBeNull();
  });

  it('draws nothing when the paper has no snippets, and does not invent any when only a count arrived', () => {
    const { rerender } = render(<PublicationList publications={[paper()]} intro={null} showSnippets />);
    expect(screen.queryByTestId('paper-snippets')).toBeNull();
    expect(screen.queryByTestId('paper-snippets-withheld')).toBeNull();
    rerender(<PublicationList publications={[paper({ snippet_count: 3 })]} intro={null} showSnippets snippetsDisclosed />);
    expect(screen.queryByTestId('paper-snippets')).toBeNull();
    expect(screen.getByTestId('paper-snippets-withheld')).toHaveTextContent('3 supporting passages recorded; the text is not provided to this view.');
  });

  it('says showing passages is not enabled when the deployment does not disclose them (or the server is older)', () => {
    const { rerender } = render(<PublicationList publications={[paper({ snippet_count: 1 })]} intro={null} showSnippets />);
    expect(screen.getByTestId('paper-snippets-withheld')).toHaveTextContent('1 supporting passage recorded; showing them is not enabled on this deployment.');
    rerender(<PublicationList publications={[paper({ snippet_count: 4 })]} intro={null} showSnippets snippetsDisclosed={false} />);
    expect(screen.getByTestId('paper-snippets-withheld')).toHaveTextContent('4 supporting passages recorded; showing them is not enabled on this deployment.');
  });

  it('is off unless the caller asks for it, and drops malformed entries', () => {
    render(<PublicationList publications={[paper({ snippets: passages(1), snippet_count: 1 })]} intro={null} />);
    expect(screen.queryByTestId('paper-snippets')).toBeNull();
    const messy = paper({ snippets: [{ text: '  ' }, { text: 5 as unknown as string }, { text: 'ok', page: Number.NaN }] });
    expect(paperSnippets(messy)).toEqual([{ text: 'ok', page: undefined }]);
  });

  it('never copies the passages into bookmarks or a node edit', () => {
    const full = paper({ snippets: passages(2), snippet_count: 2 });
    expect(withoutSnippets(full)).toEqual(paper());
    togglePublicationFavorite(full);
    const stored = readBookmarkedPublications()[0].publication;
    expect(stored.snippets).toBeUndefined();
    expect(stored.snippet_count).toBeUndefined();
    expect(stored.paper_title).toBe('Edge-on packing in P3HT');
  });
});
