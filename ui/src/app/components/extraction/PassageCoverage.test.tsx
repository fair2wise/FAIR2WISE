import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PassagesBlock } from '../extractionApi';
import { ExtractionCoverage, papersUnderRelationThreshold, papersUnderThreshold, percentOf } from './PassageCoverage';

const counts = (terms: number, withPassage: number, quoted: number, located: number, rejected: number) => ({
  terms, with_passage: withPassage, quoted, located, rejected, uncovered: terms - withPassage,
});
const block: PassagesBlock = {
  total: counts(20, 16, 10, 6, 3),
  papers: { 'good.pdf': counts(10, 10, 8, 2, 0), 'weak.pdf': counts(10, 6, 2, 4, 3) },
};

describe('ExtractionCoverage', () => {
  it('computes percentages and the papers under 90%', () => {
    expect(percentOf(6, 10)).toBe(60);
    expect(percentOf(0, 0)).toBeNull();
    expect(papersUnderThreshold(block)).toEqual([{ name: 'weak.pdf', percent: 60 }]);
    expect(papersUnderThreshold({ total: block.total, papers: { 'edge.pdf': counts(10, 9, 9, 0, 0) } })).toEqual([]);   // exactly 90%
  });

  it('shows terms with text, quoted, located, rejected and uncovered, per paper and in total', () => {
    render(<ExtractionCoverage passages={block} />);
    expect(screen.getByTestId('passages-total')).toHaveTextContent('16 of 20 (80%)');
    const section = screen.getByTestId('run-passages');
    expect(within(section).getAllByText('Quoted')[0].nextSibling).toHaveTextContent('10');
    expect(within(section).getAllByText('Located')[0].nextSibling).toHaveTextContent('6');
    expect(within(section).getAllByText('Sentences rejected')[0].nextSibling).toHaveTextContent('3');
    expect(within(section).getAllByText('Terms without')[0].nextSibling).toHaveTextContent('4');
    const rows = within(screen.getByTestId('passages-papers')).getAllByRole('row');
    expect(rows).toHaveLength(3);   // header and two papers
    expect(rows[2]).toHaveTextContent('weak.pdf');
    expect(rows[2]).toHaveTextContent('6 of 10 (60%)');
  });

  it('warns for a paper under 90% and says what can help', () => {
    render(<ExtractionCoverage passages={block} />);
    expect(screen.getByTestId('passages-warning')).toHaveTextContent('Under 90% of the terms have supporting text for weak.pdf (60%)');
  });

  it('draws no warning when every paper is at or over 90%', () => {
    render(<ExtractionCoverage passages={{ total: counts(10, 10, 10, 0, 0), papers: { 'a.pdf': counts(10, 10, 10, 0, 0) } }} />);
    expect(screen.queryByTestId('passages-warning')).toBeNull();
  });

  it('renders a paper name as plain text', () => {
    const { container } = render(<ExtractionCoverage passages={{ total: block.total, papers: { '<img src=x onerror=1>': counts(1, 0, 0, 0, 0) } }} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByTestId('passages-warning')).toHaveTextContent('<img src=x onerror=1> (0%)');
  });

  it('shows the relation evidence counts per paper and in total, and warns under 50%', () => {
    const rel = (relations: number, withEvidence: number, sentences: number) => ({
      relations, relations_with_evidence: withEvidence, evidence_sentences: sentences, relations_without: relations - withEvidence,
    });
    const withRelations: PassagesBlock = {
      total: { ...counts(20, 20, 20, 0, 0), ...rel(10, 6, 9) },
      papers: { 'good.pdf': { ...counts(10, 10, 10, 0, 0), ...rel(6, 5, 8) }, 'thin.pdf': { ...counts(10, 10, 10, 0, 0), ...rel(4, 1, 1) } },
    };
    expect(papersUnderRelationThreshold(withRelations)).toEqual([{ name: 'thin.pdf', percent: 25 }]);
    render(<ExtractionCoverage passages={withRelations} />);
    expect(screen.getByTestId('relations-total')).toHaveTextContent('6 of 10 (60%)');
    expect(screen.getByTestId('run-relation-evidence')).toHaveTextContent('Relation sentences stored9');
    expect(screen.getByTestId('relations-warning')).toHaveTextContent('Under 50% of the relations have a sentence naming both ends for thin.pdf (25%)');
    expect(within(screen.getByTestId('passages-papers')).getAllByRole('row')[2]).toHaveTextContent('1 of 4 (25%)');
  });

  it('draws no relation block for a run without relations (or an older server)', () => {
    render(<ExtractionCoverage passages={block} />);
    expect(screen.queryByTestId('run-relation-evidence')).toBeNull();
    expect(screen.queryByTestId('relations-warning')).toBeNull();
  });
});
