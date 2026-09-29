import { describe, expect, it } from 'vitest';
import { collectAnswerCitations, citationBibliographyLabel, citationInlineLabel, parseInlineCite, parseKgCitationNodeIds, resolveInlineCiteNodeId, splitAnswerCitationSegments, splitAnswerHighlightSegments } from './kgCitations';
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
  it('resolves graph_id-prefixed KG citations without concatenating catalogs', () => {
    const dual: LiveGraphNode[] = [
      { id: 'matkg:p3ht', label: 'P3HT', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
      { id: 'beamline:scan-301', label: 'scan 301 scan', type: 'BlueskyPlan', description: '', graph_id: 'bl1101' },
    ];
    expect(parseKgCitationNodeIds('Ops [KG:bl1101: scan 301 scan] and P3HT [KG:rsoxs_v1: P3HT].', dual)).toEqual([
      'beamline:scan-301',
      'matkg:p3ht',
    ]);
    expect(resolveInlineCiteNodeId('[KG:bl1101: scan 301 scan]', dual)).toBe('beamline:scan-301');
    expect(parseInlineCite('[PDF: paper.pdf p.12]')?.kind).toBe('pdf');
    expect(parseInlineCite('[OPS: blueprint.html §Motors]')?.kind).toBe('ops');
    expect(parseInlineCite('[MP: mp-149 Si]')?.kind).toBe('mp');
  });

  it('numbers distinct citations and keeps source kinds separate', () => {
    const dual: LiveGraphNode[] = [
      { id: 'matkg:p3ht', label: 'P3HT', type: 'Material', description: 'Donor polymer.', graph_id: 'rsoxs_v1' },
      {
        id: 'beamline:scan-301',
        label: 'scan 301',
        type: 'BlueskyPlan',
        description: 'Azimuth scan.',
        graph_id: 'bl1101',
        extra_fields: { esaf: 'P202600045-01', proposal: 'S01', sample: 'S01', scan: '301' },
      },
    ];
    const answer = 'Literature [KG: P3HT] and again [KG:rsoxs_v1: P3HT]. Ops [KG:bl1101: scan 301]. PDF [PDF: paper.pdf p.12]. Doc [OPS: blueprint.html §Motors].';
    const citations = collectAnswerCitations(answer, dual, [{
      source_paper: 'paper.pdf',
      paper_title: 'A paper about P3HT',
      authors: ['Ada'],
      journal: 'Nature',
      publication_year: 2020,
    }]);

    expect(citations.map(item => [item.n, item.sourceKind, item.name])).toEqual([
      [1, 'kg', 'P3HT'],
      [2, 'ops', 'scan 301'],
      [3, 'rag', 'paper.pdf'],
      [4, 'ops', 'blueprint.html §Motors'],
    ]);
    expect(citations[0].graphId).toBe('rsoxs_v1');
    expect(citations[1].esaf).toBe('P202600045-01');
    expect(citations[2].title).toBe('A paper about P3HT');
    expect(citations[2].page).toBe('12');
    expect(splitAnswerCitationSegments(answer, citations).filter(item => item.type === 'cite')).toHaveLength(5);
  });

  it('keeps concept names when a KG tag is used as the noun', () => {
    const citations = collectAnswerCitations(
      'RSoXS measures [KG: phase separation], [KG: molecular orientation], and morphology [KG: morphology].',
      [
        { id: 'a', label: 'phase separation', type: 'Structure', description: '', graph_id: 'rsoxs_v3' },
        { id: 'b', label: 'molecular orientation', type: 'Structure', description: '', graph_id: 'rsoxs_v3' },
        { id: 'c', label: 'morphology', type: 'Structure', description: '', graph_id: 'rsoxs_v3' },
      ],
    );
    expect(citationInlineLabel(citations[0], 'RSoXS measures ')).toBe('phase separation');
    expect(citationInlineLabel(citations[1], ', ')).toBe('molecular orientation');
    expect(citationInlineLabel(citations[2], 'and morphology ')).toBe('');
  });

  it('still numbers a cite when the agent payload has no metadata', () => {
    const citations = collectAnswerCitations('See [KG: mystery node].', []);
    expect(citations).toEqual([
      expect.objectContaining({ n: 1, name: 'mystery node', nodeId: null, sourceKind: 'kg' }),
    ]);
  });

  it('does not hang when a citation needle is empty', () => {
    const citations = collectAnswerCitations('See [KG: mystery node].', []);
    citations[0].raw = '';
    citations[0].aliases = [''];
    const segments = splitAnswerCitationSegments('See [KG: mystery node].', citations);
    expect(segments.some(segment => segment.type === 'text' && segment.text.includes('[KG: mystery node]'))).toBe(true);
  });

  it('resolves P3HT from a large node list without scanning every publication', () => {
    const many: LiveGraphNode[] = Array.from({ length: 500 }, (_, index) => ({
      id: `matkg:n${index}`,
      label: `Node ${index}`,
      type: 'Material',
      description: '',
      publications: [{ source_paper: `paper-${index}.pdf`, paper_title: `Title ${index}` }],
    }));
    many[250] = { id: 'matkg:p3ht', label: 'p3ht', type: 'Unknown', description: 'Donor polymer.', graph_id: 'rsoxs_v1' };
    const citations = collectAnswerCitations('Cited [KG: P3HT].', many);
    expect(citations).toEqual([
      expect.objectContaining({ n: 1, nodeId: 'matkg:p3ht', sourceKind: 'kg' }),
    ]);
  });

  it('prefers a typed P3HT ConjugatedPolymer over an Unknown p3ht node', () => {
    const nodes: LiveGraphNode[] = [
      { id: 'matkg:p3ht', label: 'p3ht', type: 'Unknown', description: '', graph_id: 'rsoxs_v1' },
      {
        id: 'matkg:P3HT ConjugatedPolymer',
        label: 'P3HT ConjugatedPolymer',
        type: 'ConjugatedPolymer',
        description: '',
        graph_id: 'rsoxs_v1',
      },
    ];
    const citations = collectAnswerCitations('Cited [KG: P3HT].', nodes);
    expect(citations[0]?.nodeId).toBe('matkg:P3HT ConjugatedPolymer');
  });

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

  it('labels Tiled ESAF citations as ESAF 2026-00043 instead of Entity stubs', () => {
    const esaf: LiveGraphNode = {
      id: 'beamline:ESAF-2026-00043',
      label: 'ESAF 2026-00043',
      type: 'ESAF',
      description: 'PVD glasses; scientist Cheng Wang',
      graph_id: 'tiled',
      extra_fields: { esaf_number: '2026-00043', scientist: 'Cheng Wang', entityType: 'ESAF' },
      properties: [
        { property: 'esaf_number', value: '2026-00043' },
        { property: 'scientist', value: 'Cheng Wang' },
      ],
    };
    const citations = collectAnswerCitations(
      'See [KG:tiled: ESAF-2026-00043] and [KG:tiled: PageESAFs].',
      [esaf],
    );
    expect(citations.map(item => [item.n, item.name, item.type])).toEqual([
      [1, 'ESAF 2026-00043', 'ESAF'],
    ]);
    expect(citations[0].nodeId).toBe('beamline:ESAF-2026-00043');
    expect(citations[0].esaf).toBe('2026-00043');
    expect(citationBibliographyLabel(citations[0])).toBe('ESAF 2026-00043');
  });
});
