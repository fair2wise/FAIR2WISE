import { describe, expect, it } from 'vitest';
import { parseKgCitationNodeIds, splitAnswerHighlightSegments } from './kgCitations';
import type { LiveGraphNode } from './data/liveAgent';

const nodes: LiveGraphNode[] = [
  { id: 'matkg:p3ht', label: 'P3HT', type: 'Material', description: '' },
  { id: 'matkg:pce', label: 'Power Conversion Efficiency', type: 'Property', description: '' },
  { id: 'matkg:opv', label: 'Organic Photovoltaic Device', type: 'Application', description: '' },
];

const findScatteringPeaksNode: LiveGraphNode = {
  id: 'matkg:snippetfindscatteringpeaks',
  label: 'find_scattering_peaks snippet',
  type: 'matkg:CodeSnippet',
  description: 'Find peaks in 1D scattering curves.',
  function_name: 'find_scattering_peaks',
  code_snippet: `import numpy as np
from scipy.signal import find_peaks
def find_scattering_peaks(q, intensity):
    y = np.asarray(intensity, dtype=float)
    peaks, props = find_peaks(y)
    return peaks, props`,
};

describe('splitAnswerHighlightSegments', () => {
  it('bolds KG citations and PDF filenames', () => {
    const segments = splitAnswerHighlightSegments(
      'P3HT [KG: P3HT] is discussed in XRAY1.pdf.',
    );
    expect(segments).toEqual([
      { text: 'P3HT ', bold: false },
      { text: '[KG: P3HT]', bold: true },
      { text: ' is discussed in ', bold: false },
      { text: 'XRAY1.pdf', bold: true },
      { text: '.', bold: false },
    ]);
  });

  it('preserves markdown bold segments', () => {
    const segments = splitAnswerHighlightSegments('**Important** note.');
    expect(segments).toEqual([
      { text: 'Important', bold: true },
      { text: ' note.', bold: false },
    ]);
  });
});

describe('kgCitations', () => {
  it('extracts cited node ids in answer order', () => {
    const answer =
      'P3HT is a donor [KG: P3HT]. OPV devices [KG: Organic Photovoltaic Device] reach high PCE [KG: Power Conversion Efficiency].';
    expect(parseKgCitationNodeIds(answer, nodes)).toEqual([
      'matkg:p3ht',
      'matkg:opv',
      'matkg:pce',
    ]);
  });

  it('deduplicates repeated citations while preserving first appearance', () => {
    const answer = 'Used twice [KG: P3HT] and again [KG: P3HT].';
    expect(parseKgCitationNodeIds(answer, nodes)).toEqual(['matkg:p3ht']);
  });

  it('matches citations case-insensitively', () => {
    const answer = 'Cited [KG: power conversion efficiency].';
    expect(parseKgCitationNodeIds(answer, nodes)).toEqual(['matkg:pce']);
  });

  it('matches code snippet node labels without the snippet suffix', () => {
    const snippetNodes: LiveGraphNode[] = [
      { id: 'matkg:wavelet', label: 'wavelet_peak_candidates snippet', type: 'CodeSnippet', description: '' },
    ];
    const answer = 'Use peak finding [KG: wavelet_peak_candidates].';
    expect(parseKgCitationNodeIds(answer, snippetNodes)).toEqual(['matkg:wavelet']);
  });

  it('matches code snippet nodes from fenced source code blocks', () => {
    const answer = `Here is the implementation:

\`\`\`python
import numpy as np
from scipy.signal import find_peaks
def find_scattering_peaks(q, intensity):
    y = np.asarray(intensity, dtype=float)
    peaks, props = find_peaks(y)
    return peaks, props
\`\`\``;

    expect(parseKgCitationNodeIds(answer, [findScatteringPeaksNode])).toEqual([
      'matkg:snippetfindscatteringpeaks',
    ]);
  });

  it('orders kg citations before code snippet blocks', () => {
    const answer = `Context [KG: P3HT].

\`\`\`python
def find_scattering_peaks(q, intensity):
    return q, intensity
\`\`\``;

    expect(parseKgCitationNodeIds(answer, [nodes[0], findScatteringPeaksNode])).toEqual([
      'matkg:p3ht',
      'matkg:snippetfindscatteringpeaks',
    ]);
  });

  it('matches snippet function defs outside complete code fences during streaming', () => {
    const answer = `\`\`\`python
def find_scattering_peaks(q, intensity):
    y = np.asarray(intensity, dtype=float)`;

    expect(parseKgCitationNodeIds(answer, [findScatteringPeaksNode])).toEqual([
      'matkg:snippetfindscatteringpeaks',
    ]);
  });

  it('matches nodes linked to pdf filenames mentioned in the answer', () => {
    const softMatterNode: LiveGraphNode = {
      id: 'matkg:softmattersystems',
      label: 'soft matter systems',
      type: 'Material',
      description: 'Materials that are easily deformed by thermal stresses or fluctuations.',
      publications: [{
        source_paper: 'XRAY1.pdf',
        paper_title: 'Machine Learning-Assisted Analysis of Small Angle X-ray Scattering',
        doi: 'arXiv:2111.08645v1',
      }],
    };

    const answer = 'Evidence from XRAY1.pdf supports analysis of soft matter systems.';
    expect(parseKgCitationNodeIds(answer, [softMatterNode])).toEqual(['matkg:softmattersystems']);
  });

  it('matches nodes from response publications even when the pdf is not named in the answer', () => {
    const softMatterNode: LiveGraphNode = {
      id: 'matkg:softmattersystems',
      label: 'soft matter systems',
      type: 'Material',
      description: 'Materials that are easily deformed by thermal stresses or fluctuations.',
      publications: [{
        source_paper: 'XRAY1.pdf',
        paper_title: 'Machine Learning-Assisted Analysis of Small Angle X-ray Scattering',
      }],
    };

    const answer = 'Soft matter systems deform easily under thermal fluctuations.';
    const responsePublications = [{
      source_paper: 'XRAY1.pdf',
      paper_title: 'Machine Learning-Assisted Analysis of Small Angle X-ray Scattering',
    }];

    expect(parseKgCitationNodeIds(answer, [softMatterNode], responsePublications)).toEqual([
      'matkg:softmattersystems',
    ]);
  });

  it('matches nodes when the answer cites a paper title attached to the node', () => {
    const softMatterNode: LiveGraphNode = {
      id: 'matkg:softmattersystems',
      label: 'soft matter systems',
      type: 'Material',
      description: 'Materials that are easily deformed by thermal stresses or fluctuations.',
      publications: [{
        source_paper: 'XRAY1.pdf',
        paper_title: 'Machine Learning-Assisted Analysis of Small Angle X-ray Scattering',
      }],
    };

    const answer = 'As described in Machine Learning-Assisted Analysis of Small Angle X-ray Scattering, soft matter systems are common in SAXS.';
    expect(parseKgCitationNodeIds(answer, [softMatterNode])).toEqual(['matkg:softmattersystems']);
  });
});
