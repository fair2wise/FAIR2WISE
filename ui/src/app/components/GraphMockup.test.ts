import { describe, expect, it } from 'vitest';
import {
  connectedGraphSubset,
  normalizeRelationshipPredicate,
  oneHopNodeIds,
} from './GraphMockup';
import type { GraphPayload } from './data/liveAgent';

describe('GraphMockup graph helpers', () => {
  it('builds a selected node one-hop neighborhood in both directions', () => {
    const graph: GraphPayload = {
      source_path: 'kg.json',
      nodes: [
        { id: 'a', label: 'A', type: 'Thing', description: '' },
        { id: 'b', label: 'B', type: 'Thing', description: '' },
        { id: 'c', label: 'C', type: 'Thing', description: '' },
        { id: 'd', label: 'D', type: 'Thing', description: '' },
      ],
      edges: [
        { source: 'a', predicate: 'rel:affects', target: 'b' },
        { source: 'c', predicate: 'rel:part_of', target: 'a' },
        { source: 'b', predicate: 'rel:related_to', target: 'd' },
      ],
    };

    expect(new Set(oneHopNodeIds(graph, 'a'))).toEqual(new Set(['a', 'b', 'c']));
  });

  it('normalizes bare predicates and validates CURIEs', () => {
    expect(normalizeRelationshipPredicate('Used In')).toBe('rel:used_in');
    expect(normalizeRelationshipPredicate('matkg:has_property')).toBe('matkg:has_property');
    expect(normalizeRelationshipPredicate('bad predicate:value')).toBeNull();
    expect(normalizeRelationshipPredicate('')).toBeNull();
  });

  it('builds a deterministic connected-first viewer subset with induced edges', () => {
    const graph: GraphPayload = {
      source_path: 'kg.json',
      nodes: [
        { id: 'a', label: 'Alpha', type: 'Thing', description: '' },
        { id: 'b', label: 'Beta', type: 'Thing', description: '' },
        { id: 'c', label: 'Gamma', type: 'Thing', description: '' },
        { id: 'd', label: 'Delta', type: 'Thing', description: '' },
        { id: 'unknown', label: 'Unknown', type: 'Unknown', description: '' },
      ],
      edges: [
        { source: 'a', predicate: 'rel:related_to', target: 'b' },
        { source: 'a', predicate: 'rel:related_to', target: 'c' },
        { source: 'b', predicate: 'rel:related_to', target: 'c' },
        { source: 'd', predicate: 'rel:related_to', target: 'unknown' },
      ],
    };

    const subset = connectedGraphSubset(graph, 3);

    expect(subset.nodes.map(node => node.id)).toEqual(['a', 'b', 'c']);
    expect(subset.edges).toEqual(graph.edges.slice(0, 3));
    expect(subset.source_path).toBe('kg.json');
  });

  it('continues across disconnected components and can return the full graph', () => {
    const graph: GraphPayload = {
      source_path: 'large.json',
      nodes: Array.from({ length: 105 }, (_, index) => ({
        id: `node-${String(index).padStart(3, '0')}`,
        label: `Node ${String(index).padStart(3, '0')}`,
        type: 'Thing',
        description: '',
      })),
      edges: [],
    };

    expect(connectedGraphSubset(graph, 20).nodes).toHaveLength(20);
    expect(connectedGraphSubset(graph, 500).nodes).toHaveLength(105);
    expect(connectedGraphSubset(graph, 0).nodes).toHaveLength(0);
  });
});
