import { describe, expect, it } from 'vitest';
import {
  capNeighborhood,
  clampOverviewStep,
  effectiveLayoutMode,
  FOCUS_NEIGHBORHOOD_MAX_NODES,
  forceLayoutAllowed,
  isLargeGraph,
  LARGE_GRAPH_NODE_THRESHOLD,
  nextOverviewStep,
  OVERVIEW_DEFAULT_NODES,
  OVERVIEW_STEPS,
  overviewOptions,
  overviewSelection,
  overviewStepLabel,
  SLOW_LAYOUT_NODE_LIMIT,
} from './largeGraphPolicy';
import type { GraphPayload } from './data/liveAgent';

/** Star-ish graph: node i links to node (i % hubs), so low indices are hubs with falling degree. Every 4th node is Unknown. */
function graphOf(count: number, hubs = 20): GraphPayload {
  const nodes = Array.from({ length: count }, (_, i) => ({
    id: `n${i}`,
    label: `Node ${String(i).padStart(5, '0')}`,
    type: i >= hubs && i % 4 === 0 ? 'Unknown' : 'Material',
    description: '',
  }));
  const edges = [];
  for (let i = hubs; i < count; i += 1) {
    edges.push({ source: `n${i}`, target: `n${i % hubs}`, predicate: 'rel:related_to' });
  }
  return { nodes, edges, source_path: 'source:json:test' };
}

describe('large-graph thresholds', () => {
  it('names the constants the owner asked for', () => {
    expect(LARGE_GRAPH_NODE_THRESHOLD).toBe(1_500);
    expect(OVERVIEW_DEFAULT_NODES).toBe(300);
    expect(OVERVIEW_STEPS.slice(0, 3)).toEqual([300, 1_000, 2_000]);
    expect(SLOW_LAYOUT_NODE_LIMIT).toBe(2_000);
    expect(FOCUS_NEIGHBORHOOD_MAX_NODES).toBe(500);
  });

  it('treats only graphs above the threshold as large (the 268-421 node demo graphs are not)', () => {
    for (const count of [0, 268, 421, 1_143, 1_500]) expect(isLargeGraph(count)).toBe(false);
    for (const count of [1_501, 15_815, 22_012]) expect(isLargeGraph(count)).toBe(true);
  });

  it('allows force-directed layout up to 2,000 displayed nodes only', () => {
    expect(forceLayoutAllowed(300)).toBe(true);
    expect(forceLayoutAllowed(2_000)).toBe(true);
    expect(forceLayoutAllowed(2_001)).toBe(false);
  });
});

describe('overviewSelection', () => {
  const graph = graphOf(3_000);

  it('keeps the top-N nodes by degree, ties by label', () => {
    const { graph: view, shown } = overviewSelection(graph, 5);
    expect(shown).toBe(5);
    // hubs n0..n19 have far higher degree than any leaf (degree 1)
    expect(view.nodes.every(node => Number(node.id.slice(1)) < 20)).toBe(true);
    const degree = (id: string) => graph.edges.filter(edge => edge.target === id && graph.nodes[Number(edge.source.slice(1))].type !== 'Unknown').length;
    const picked = view.nodes.map(node => degree(node.id));
    expect(picked).toEqual([...picked].sort((left, right) => right - left));
    expect(Math.min(...picked)).toBeGreaterThanOrEqual(
      Math.max(...graph.nodes.slice(0, 20).filter(node => !view.nodes.includes(node)).map(node => degree(node.id))),
    );
  });

  it('orders by degree descending', () => {
    const g: GraphPayload = {
      nodes: ['a', 'b', 'c', 'd'].map(id => ({ id, label: id, type: 'Material', description: '' })),
      edges: [
        { source: 'a', target: 'c', predicate: 'p' }, { source: 'b', target: 'c', predicate: 'p' },
        { source: 'd', target: 'c', predicate: 'p' }, { source: 'a', target: 'b', predicate: 'p' },
      ],
      source_path: 's',
    };
    expect(overviewSelection(g, 4).graph.nodes.map(node => node.id)).toEqual(['c', 'a', 'b', 'd']);
  });

  it('shows only edges between the kept nodes', () => {
    const { graph: view } = overviewSelection(graph, 300);
    const ids = new Set(view.nodes.map(node => node.id));
    expect(view.edges.length).toBeGreaterThan(0);
    expect(view.edges.every(edge => ids.has(edge.source) && ids.has(edge.target))).toBe(true);
  });

  it('hides Unknown placeholders by default and can include them', () => {
    const hidden = overviewSelection(graph, 3_000);
    expect(hidden.graph.nodes.some(node => node.type === 'Unknown')).toBe(false);
    expect(hidden.unknownTotal).toBeGreaterThan(0);
    expect(hidden.unknownHidden).toBe(hidden.unknownTotal);
    expect(hidden.eligible).toBe(3_000 - hidden.unknownTotal);
    expect(hidden.total).toBe(3_000);
    const shown = overviewSelection(graph, 3_000, { includeUnknown: true });
    expect(shown.shown).toBe(3_000);
    expect(shown.unknownHidden).toBe(0);
  });

  it('ranks by degree among visible nodes, so hubs whose links all go to Unknown stay out', () => {
    const g: GraphPayload = {
      nodes: [
        { id: 'hub', label: 'Hub', type: 'Material', description: '' },
        { id: 'real1', label: 'R1', type: 'Material', description: '' },
        { id: 'real2', label: 'R2', type: 'Material', description: '' },
        ...Array.from({ length: 5 }, (_, i) => ({ id: `u${i}`, label: `U${i}`, type: 'Unknown', description: '' })),
      ],
      edges: [
        ...Array.from({ length: 5 }, (_, i) => ({ source: 'hub', target: `u${i}`, predicate: 'p' })),
        { source: 'real1', target: 'real2', predicate: 'p' },
      ],
      source_path: 's',
    };
    expect(overviewSelection(g, 1).graph.nodes[0].id).toBe('real1');
    expect(overviewSelection(g, 1, { includeUnknown: true }).graph.nodes[0].id).toBe('hub');
  });

  it('ignores self loops and duplicate edges when counting degree', () => {
    const g: GraphPayload = {
      nodes: ['a', 'b', 'c'].map(id => ({ id, label: id, type: 'Material', description: '' })),
      edges: [
        { source: 'a', target: 'a', predicate: 'p' }, { source: 'a', target: 'a', predicate: 'p' },
        { source: 'b', target: 'c', predicate: 'p' }, { source: 'c', target: 'b', predicate: 'q' },
      ],
      source_path: 's',
    };
    expect(overviewSelection(g, 3).graph.nodes.map(node => node.id)).toEqual(['b', 'c', 'a']);
  });
});

describe('show-more steps', () => {
  it('steps 300 -> 1,000 -> 2,000 -> 5,000 and then stops', () => {
    expect(nextOverviewStep(300)).toBe(1_000);
    expect(nextOverviewStep(1_000)).toBe(2_000);
    expect(nextOverviewStep(2_000)).toBe(5_000);
    expect(nextOverviewStep(5_000)).toBeNull();
  });

  it('lists the steps below the eligible count, plus an All entry when everything fits', () => {
    expect(overviewOptions(22_012).map(option => option.value)).toEqual([300, 1_000, 2_000, 5_000]);
    expect(overviewOptions(1_600).map(option => option.value)).toEqual([300, 1_000, 1_600]);
    expect(overviewOptions(1_600).at(-1)?.label).toBe('All (1,600)');
    expect(overviewOptions(2_000).map(option => option.value)).toEqual([300, 1_000, 2_000]);
  });

  it('marks the sizes above the slow-layout limit', () => {
    expect(overviewStepLabel(2_000)).toBe('2,000');
    expect(overviewStepLabel(5_000)).toBe('5,000 (slow)');
    expect(overviewOptions(22_012).map(option => option.label)).toEqual(['300', '1,000', '2,000', '5,000 (slow)']);
  });

  it('snaps a stored step to an available option after the graph changes', () => {
    expect(clampOverviewStep(300, 22_012)).toBe(300);
    expect(clampOverviewStep(5_000, 1_600)).toBe(1_600);
    expect(clampOverviewStep(2_000, 1_600)).toBe(1_600);
  });
});

describe('capNeighborhood', () => {
  it('leaves a neighbourhood of at most the cap unchanged (same object)', () => {
    const small = graphOf(400, 5);
    const result = capNeighborhood(small, 'n0');
    expect(result.truncated).toBe(false);
    expect(result.graph).toBe(small);
  });

  it('keeps the seed and the most connected others when above the cap', () => {
    const big = graphOf(900, 3);
    const result = capNeighborhood(big, 'n899');
    expect(result.truncated).toBe(true);
    expect(result.total).toBe(900);
    expect(result.graph.nodes).toHaveLength(500);
    expect(result.graph.nodes[0].id).toBe('n899');
    expect(result.graph.nodes.some(node => node.id === 'n0')).toBe(true);
    const ids = new Set(result.graph.nodes.map(node => node.id));
    expect(result.graph.edges.every(edge => ids.has(edge.source) && ids.has(edge.target))).toBe(true);
  });
});

describe('effectiveLayoutMode (default view)', () => {
  it('defaults the KG viewer to force-directed up to the 2,000-node limit and to fixed above it', () => {
    expect(effectiveLayoutMode(null, true, 0)).toBe('force');
    expect(effectiveLayoutMode(null, true, OVERVIEW_DEFAULT_NODES)).toBe('force');
    expect(effectiveLayoutMode(null, true, SLOW_LAYOUT_NODE_LIMIT)).toBe('force');
    expect(effectiveLayoutMode(null, true, SLOW_LAYOUT_NODE_LIMIT + 1)).toBe('existing');
  });

  it('respects the user choice, but never lets force past the limit', () => {
    expect(effectiveLayoutMode('existing', true, 50)).toBe('existing');
    expect(effectiveLayoutMode('force', true, 50)).toBe('force');
    expect(effectiveLayoutMode('force', true, 5_000)).toBe('existing');
  });

  it('keeps the chat sidebar graph on the fixed layout by default', () => {
    expect(effectiveLayoutMode(null, false, 50)).toBe('existing');
    expect(effectiveLayoutMode('force', false, 50)).toBe('force');
  });
});
