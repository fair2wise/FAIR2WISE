import { describe, expect, it } from 'vitest';
import type { GraphPayload } from './data/liveAgent';
import {
  catalogIdsFromGraph,
  graphIdFromPath,
  hasTiledIdentityEdges,
  honorViewerCatalogs,
  CITE_FOCUS_HOPS,
  isolateNeighborIds,
  looksLikeTiledIdentityRef,
  mergeGraphPayloads,
  neighborhoodNodeIds,
  neighborhoodSubgraph,
  overlayCatalogColor,
  pathContainsEdge,
  resolveViewerNodeId,
  shortestUndirectedPath,
} from './kgCatalog';

describe('kgCatalog', () => {
  it('maps snapshot filenames the same way as the backend graph_id', () => {
    expect(graphIdFromPath('storage/kg/matkg_rsoxs_v1.json')).toBe('rsoxs_v1');
    expect(graphIdFromPath('storage/kg/matkg_bl1101_v1.json')).toBe('bl1101');
    expect(graphIdFromPath('storage/kg/matkg_rsoxs_v3.json')).toBe('rsoxs_v3');
    expect(graphIdFromPath('storage/kg/matkg_bl1101_v2.json')).toBe('bl1101');
    expect(graphIdFromPath('storage/kg/matkg_xray_papers_cborg_chat.json')).toBe('xray_demo');
  });

  it('keeps query overlays tagged by graph_id instead of concatenating dumps', () => {
    const graph: GraphPayload = {
      source_path: 'query:dual',
      nodes: [
        { id: 'a', label: 'P3HT', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'b', label: 'Sample stage', type: 'BeamlineStage', description: '', graph_id: 'bl1101' },
      ],
      edges: [{ source: 'a', predicate: 'rel:related_to', target: 'b' }],
    };

    const honored = honorViewerCatalogs(graph, 'rsoxs_v1');
    expect(honored.nodes.map(node => node.id)).toEqual(['a', 'b']);
    expect(catalogIdsFromGraph(honored)).toEqual(['bl1101', 'rsoxs_v1']);
    expect(overlayCatalogColor('rsoxs_v1')).toBe('#0ea5e9');
    expect(overlayCatalogColor('bl1101')).toBe('#d97706');
  });

  it('filters a mixed full dump down to the Settings primary catalog', () => {
    const graph: GraphPayload = {
      source_path: 'storage/kg/matkg_rsoxs_v1.json',
      nodes: [
        { id: 'lit', label: 'P3HT', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'ops', label: 'Motor', type: 'Motor', description: '', graph_id: 'bl1101' },
      ],
      edges: [
        { source: 'lit', predicate: 'rel:related_to', target: 'ops' },
      ],
    };

    const honored = honorViewerCatalogs(graph, 'rsoxs_v1');
    expect(honored.nodes.map(node => node.id)).toEqual(['lit']);
    expect(honored.edges).toEqual([]);
    expect(catalogIdsFromGraph(honored)).toEqual(['rsoxs_v1']);
  });

  it('resolves viewer nodes by id or label', () => {
    const graph: GraphPayload = {
      source_path: 'kg.json',
      nodes: [
        { id: 'matkg:P3HT', label: 'P3HT', type: 'ConjugatedPolymer', description: '', graph_id: 'rsoxs_v1' },
      ],
      edges: [],
    };
    expect(resolveViewerNodeId(graph, 'matkg:P3HT')).toBe('matkg:P3HT');
    expect(resolveViewerNodeId(graph, 'matkg:p3ht')).toBe('matkg:P3HT');
    expect(resolveViewerNodeId(graph, 'P3HT')).toBe('matkg:P3HT');
    expect(resolveViewerNodeId(graph, 'missing')).toBeNull();
  });

  it('resolves short cite names onto longer typed labels', () => {
    const graph: GraphPayload = {
      source_path: 'kg.json',
      nodes: [
        { id: 'matkg:p3ht', label: 'p3ht', type: 'Unknown', description: '', graph_id: 'rsoxs_v1' },
        {
          id: 'matkg:P3HT ConjugatedPolymer',
          label: 'P3HT ConjugatedPolymer',
          type: 'ConjugatedPolymer',
          description: '',
          graph_id: 'rsoxs_v1',
        },
        {
          id: 'tiled:esaf-42',
          label: 'ESAF 42',
          type: 'Proposal',
          description: '',
          graph_id: 'tiled',
          extra_fields: { esaf: '42' },
        },
      ],
      edges: [],
    };
    expect(resolveViewerNodeId(graph, 'P3HT')).toBe('matkg:P3HT ConjugatedPolymer');
    expect(resolveViewerNodeId(graph, 'ESAF 42')).toBe('tiled:esaf-42');
    expect(resolveViewerNodeId(graph, '42')).toBe('tiled:esaf-42');
  });

  it('resolves hyphenated ESAF cites onto Tiled identity labels', () => {
    const graph: GraphPayload = {
      source_path: 'query:tiled',
      nodes: [
        {
          id: 'beamline:ESAF-2026-00043',
          label: 'ESAF 2026-00043',
          type: 'ESAF',
          description: '',
          graph_id: 'tiled',
          extra_fields: { esaf_number: '2026-00043' },
        },
      ],
      edges: [],
    };
    expect(resolveViewerNodeId(graph, 'ESAF-2026-00043')).toBe('beamline:ESAF-2026-00043');
    expect(resolveViewerNodeId(graph, '2026-00043')).toBe('beamline:ESAF-2026-00043');
  });

  it('walks exactly N hops and stays on the cited node graph_id', () => {
    const graph: GraphPayload = {
      source_path: 'query:dual',
      nodes: [
        { id: 'seed', label: 'P3HT', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'h1', label: 'Hop 1', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'h2', label: 'Hop 2', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'h3', label: 'Hop 3', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'h4', label: 'Hop 4', type: 'Material', description: '', graph_id: 'rsoxs_v1' },
        { id: 'ops', label: 'Motor', type: 'Motor', description: '', graph_id: 'bl1101' },
      ],
      edges: [
        { source: 'seed', predicate: 'rel:related_to', target: 'h1' },
        { source: 'h1', predicate: 'rel:related_to', target: 'h2' },
        { source: 'h2', predicate: 'rel:related_to', target: 'h3' },
        { source: 'h3', predicate: 'rel:related_to', target: 'h4' },
        { source: 'seed', predicate: 'rel:related_to', target: 'ops' },
      ],
    };

    expect(CITE_FOCUS_HOPS).toBe(3);
    expect(new Set(neighborhoodNodeIds(graph, 'SEED', 1))).toEqual(new Set(['seed', 'h1']));
    expect(new Set(neighborhoodNodeIds(graph, 'seed', CITE_FOCUS_HOPS))).toEqual(
      new Set(['seed', 'h1', 'h2', 'h3']),
    );
    const neighborhood = neighborhoodSubgraph(graph, 'seed', CITE_FOCUS_HOPS);
    expect(neighborhood.nodes.map(node => node.id).sort()).toEqual(['h1', 'h2', 'h3', 'seed']);
    expect(neighborhood.nodes.some(node => node.graph_id === 'bl1101')).toBe(false);
    expect(neighborhood.edges.some(edge => edge.target === 'ops' || edge.source === 'ops')).toBe(false);
    const retrievedOnly: GraphPayload = {
      source_path: 'query:tiny',
      nodes: graph.nodes.filter(node => node.id === 'seed' || node.id === 'h1'),
      edges: graph.edges.filter(edge => edge.source === 'seed' && edge.target === 'h1'),
    };
    expect(neighborhoodNodeIds(retrievedOnly, 'seed', CITE_FOCUS_HOPS)).toEqual(['seed', 'h1']);
    expect(neighborhoodNodeIds(graph, 'seed', CITE_FOCUS_HOPS).length).toBe(4);
  });

  it('returns the smaller component when the graph has fewer than 3 hops', () => {
    const graph: GraphPayload = {
      source_path: 'tiny.json',
      nodes: [
        { id: 'a', label: 'A', type: 'Thing', description: '' },
        { id: 'b', label: 'B', type: 'Thing', description: '' },
      ],
      edges: [{ source: 'a', predicate: 'rel:related_to', target: 'b' }],
    };
    expect(new Set(neighborhoodNodeIds(graph, 'a', CITE_FOCUS_HOPS))).toEqual(new Set(['a', 'b']));
  });

  it('merges Tiled GraphQL neighborhoods instead of walking the JSON catalog', () => {
    expect(looksLikeTiledIdentityRef('ESAF-2026-00043')).toBe(true);
    expect(looksLikeTiledIdentityRef('beamline:Proposal-P202600043-01')).toBe(true);
    expect(looksLikeTiledIdentityRef('matkg:P3HT')).toBe(false);
    expect(looksLikeTiledIdentityRef('x', { id: 'x', type: 'BlueskyRun', graph_id: 'tiled' })).toBe(true);

    const catalog: GraphPayload = {
      source_path: 'storage/kg/matkg_rsoxs_v2.json',
      nodes: [
        { id: 'beamline:ESAF-2026-00043', label: 'ESAF 2026-00043', type: 'ESAF', description: '', graph_id: 'bl1101' },
      ],
      edges: [],
    };
    const tiled: GraphPayload = {
      source_path: 'tiled://graphql',
      nodes: [
        { id: 'beamline:ESAF-2026-00043', label: 'ESAF 2026-00043', type: 'ESAF', description: '', graph_id: 'tiled' },
        { id: 'beamline:Proposal-P202600043-01', label: 'Proposal P202600043-01', type: 'Proposal', description: '', graph_id: 'tiled' },
        { id: 'beamline:Sample-S01', label: 'Sample S01', type: 'Sample', description: '', graph_id: 'tiled' },
      ],
      edges: [
        { source: 'beamline:ESAF-2026-00043', predicate: 'rel:hasProposal', target: 'beamline:Proposal-P202600043-01' },
        { source: 'beamline:Proposal-P202600043-01', predicate: 'rel:hasSample', target: 'beamline:Sample-S01' },
      ],
    };

    expect(resolveViewerNodeId(tiled, 'tiled:beamline:ESAF-2026-00043')).toBe('beamline:ESAF-2026-00043');
    expect(hasTiledIdentityEdges(catalog)).toBe(false);
    const merged = mergeGraphPayloads(catalog, tiled);
    expect(hasTiledIdentityEdges(merged)).toBe(true);
    expect(merged.nodes.map(node => node.id).sort()).toEqual([
      'beamline:ESAF-2026-00043',
      'beamline:Proposal-P202600043-01',
      'beamline:Sample-S01',
    ]);
    expect(neighborhoodSubgraph(merged, 'ESAF-2026-00043', CITE_FOCUS_HOPS).edges).toHaveLength(2);
  });

  it('finds isolate neighborhoods and shortest paths', () => {
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'd' },
    ];
    expect([...isolateNeighborIds(edges, 'b')].sort()).toEqual(['a', 'b', 'c']);
    expect(shortestUndirectedPath(edges, 'a', 'd')).toEqual(['a', 'b', 'c', 'd']);
    expect(pathContainsEdge(['a', 'b', 'c'], 'c', 'b')).toBe(true);
    expect(shortestUndirectedPath(edges, 'a', 'missing')).toBeNull();
  });
});
