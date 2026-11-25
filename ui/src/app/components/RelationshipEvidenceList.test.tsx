/**
 * Relationship evidence on the node card: the sentences that name both ends of a relationship, collapsed, bounded with
 * "Show more", plain text only, labelled `located`, and nothing invented when only a count arrived.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import pinned from './__fixtures__/relationship_evidence.json';
import { relationshipEvidenceOf, type LiveGraphNode, type RelationshipEvidence } from './data/liveAgent';
import { remainingNodeProperties } from './nodeCardDetails';
import { RELATION_EVIDENCE_COLLAPSED, RelationshipEvidenceList, relationshipSentences } from './RelationshipEvidenceList';

const item = (over: Partial<RelationshipEvidence> = {}): RelationshipEvidence => ({
  source: 'matkg:A', predicate: 'rel:measured_by', target: 'matkg:B', evidence_count: 2,
  evidence: [{ text: 'A is measured by B.', page: 3, kind: 'located' }, { text: 'An older sentence.', kind: 'unknown' }], ...over,
});
const many = (n: number) => Array.from({ length: n }, (_, i) => ({ text: `sentence ${i + 1}`, page: i + 1, kind: 'located' as const }));

describe('RelationshipEvidenceList', () => {
  it('is collapsed by default and lists the sentences with page and the located label on demand', () => {
    render(<RelationshipEvidenceList item={item()} disclosed />);
    expect(screen.queryByText(/A is measured by B/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show supporting text (2)' }));
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('A is measured by B.(p. 3)[located]');
    expect(screen.getAllByTestId('evidence-kind')).toHaveLength(1);       // an older, unknown-kind sentence carries no label
    fireEvent.click(screen.getByRole('button', { name: 'Hide supporting text (2)' }));
    expect(screen.queryByRole('listitem')).toBeNull();
  });

  it('shows a bounded number and the rest behind "Show more"', () => {
    render(<RelationshipEvidenceList item={item({ evidence_count: 8, evidence: many(8) })} disclosed />);
    fireEvent.click(screen.getByRole('button', { name: /supporting text/ }));
    expect(screen.getAllByRole('listitem')).toHaveLength(RELATION_EVIDENCE_COLLAPSED);
    fireEvent.click(screen.getByRole('button', { name: `Show ${8 - RELATION_EVIDENCE_COLLAPSED} more` }));
    expect(screen.getAllByRole('listitem')).toHaveLength(8);
  });

  it('states the count and the reason when only a count arrived, and invents no text', () => {
    const counts = item({ evidence: undefined, evidence_count: 3 });
    const { rerender } = render(<RelationshipEvidenceList item={counts} disclosed={false} />);
    expect(screen.getByTestId('relationship-evidence-withheld')).toHaveTextContent(
      '3 supporting sentences recorded; showing them is not enabled on this deployment.');
    rerender(<RelationshipEvidenceList item={counts} disclosed />);
    expect(screen.getByTestId('relationship-evidence-withheld')).toHaveTextContent('the text is not provided to this view.');
    rerender(<RelationshipEvidenceList item={counts} disclosed={null} />);
    expect(screen.getByTestId('relationship-evidence-withheld')).toHaveTextContent('3 supporting sentences recorded.');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('draws nothing for a relationship with no evidence', () => {
    const { container } = render(<RelationshipEvidenceList item={item({ evidence_count: 0, evidence: [] })} disclosed />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders text as plain text, never as HTML', () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b> & <script>alert(1)</script>';
    const { container } = render(<RelationshipEvidenceList item={item({ evidence_count: 1, evidence: [{ text: hostile, kind: 'located' }] })} disclosed />);
    fireEvent.click(screen.getByRole('button', { name: /supporting text/ }));
    expect(screen.getByRole('listitem').textContent).toContain(hostile);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('li b')).toBeNull();
  });

  it('drops malformed entries and cuts a very long sentence', () => {
    const messy = item({ evidence: [{ text: '  ' }, { text: 5 as unknown as string }, { text: 'ok', page: Number.NaN, kind: 'quoted' },
      { text: 'x'.repeat(2000) }] });
    const got = relationshipSentences(messy);
    expect(got.map(s => s.text.length)).toEqual([2, 1500]);
    expect(got[0]).toEqual({ text: 'ok', page: undefined, label: 'quoted' });
  });
});

describe('relationshipEvidenceOf', () => {
  const node = (extra: Record<string, unknown>): Pick<LiveGraphNode, 'extra_fields'> => ({ extra_fields: extra });
  it('reads the list from the record and drops malformed entries', () => {
    expect(relationshipEvidenceOf(node({ relationship_evidence: [item(), { source: 1 }, null, 'x'] }))).toEqual([item()]);
    expect(relationshipEvidenceOf(node({}))).toEqual([]);
    expect(relationshipEvidenceOf(null)).toEqual([]);
    expect(relationshipEvidenceOf(node({ relationship_evidence: 'nope' }))).toEqual([]);
  });

  it('is not listed among the node card properties', () => {
    const full = { id: 'a', label: 'A', type: 'Thing', description: '', extra_fields: { relationship_evidence: [item()], other: 'kept' } };
    const rows = remainingNodeProperties(full as LiveGraphNode);
    expect(JSON.stringify(rows)).not.toContain('measured by');
    expect(JSON.stringify(rows)).not.toMatch(/relationship/i);
    expect(rows.some(r => r.value === 'kept')).toBe(true);
  });

  it('reads the record shape the real route answers (fixture checked against it in tests/api_v1)', () => {
    const items = relationshipEvidenceOf({ extra_fields: { relationship_evidence: pinned } });
    expect(items).toHaveLength(2);
    render(<RelationshipEvidenceList item={items[0]} disclosed />);
    fireEvent.click(screen.getByRole('button', { name: 'Show supporting text (2)' }));
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('(p. 3)[located]');
  });
});
