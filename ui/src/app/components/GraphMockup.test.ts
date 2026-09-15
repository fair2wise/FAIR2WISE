import { describe, expect, it } from 'vitest';
import {
  connectedGraphSubset,
  inducedSubgraph,
  normalizeRelationshipPredicate,
  oneHopNodeIds,
} from './GraphMockup';
import type { GraphPayload, LiveGraphNode } from './data/liveAgent';
import {
  collectNodeSourceLinks,
  githubBlobUrl,
  githubHomepageUrl,
  isLiteraturePublication,
  remainingNodeProperties,
} from './nodeCardDetails';

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

  it('keeps every queried node in the induced retrieve subgraph', () => {
    const graph: GraphPayload = {
      source_path: 'kg.json',
      nodes: Array.from({ length: 150 }, (_, index) => ({
        id: `node-${index}`,
        label: `Node ${index}`,
        type: 'Thing',
        description: '',
        graph_id: index < 80 ? 'rsoxs_v1' : 'bl1101',
      })),
      edges: Array.from({ length: 149 }, (_, index) => ({
        source: `node-${index}`,
        predicate: 'rel:related_to',
        target: `node-${index + 1}`,
      })),
    };
    const queriedIds = graph.nodes.map(node => node.id);
    const queried = inducedSubgraph(graph, queriedIds);

    expect(queried.nodes).toHaveLength(150);
    expect(queried.edges).toHaveLength(149);
    expect(connectedGraphSubset(queried, 100).nodes).toHaveLength(100);
    expect(queried.nodes.some(node => node.graph_id === 'bl1101')).toBe(true);
  });
});

describe('node card source and property mapping', () => {
  it('builds a github blob URL from repo, sha, path, and line range', () => {
    expect(githubBlobUrl({
      repo_owner: 'devoncallan',
      repo_name: 'DopantModeling',
      repo_commit_sha: 'ee93a99d2d0e94ed9763ddf883cd1db8b8fe1ce9',
      source_file_path: 'src/Morphology/Fibril/Fibril.py',
      source_start_line: 10,
      source_end_line: 20,
    })).toBe(
      'https://github.com/devoncallan/DopantModeling/blob/ee93a99d2d0e94ed9763ddf883cd1db8b8fe1ce9/src/Morphology/Fibril/Fibril.py#L10-L20',
    );
  });

  it('uses an existing blob URL and appends missing line anchors', () => {
    expect(githubBlobUrl({
      source_file_url: 'https://github.com/als-computing/bcs2sim-ophyd/blob/abc123/motor.py',
      source_start_line: 34,
      source_end_line: 94,
    })).toBe('https://github.com/als-computing/bcs2sim-ophyd/blob/abc123/motor.py#L34-L94');
  });

  it('constructs als-computing blob links for ops nodes that only stored repo name + path', () => {
    const url = githubBlobUrl({
      id: 'beamline:fn-load-beamline-config',
      graph_id: 'bl1101',
      source_papers: ['bcs2sim-ophyd:_config.py:load_beamline_config'],
      source_file_path: 'sim_ophyd/_config.py',
      repo_commit_sha: '729686c05f6c39208f4ed034abc3229f63d7ac23',
      source_start_line: 358,
      source_end_line: 365,
    });
    expect(url).toBe(
      'https://github.com/als-computing/bcs2sim-ophyd/blob/729686c05f6c39208f4ed034abc3229f63d7ac23/sim_ophyd/_config.py#L358-L365',
    );
  });

  it('falls back to the repo homepage when sha or path is missing', () => {
    expect(githubHomepageUrl({
      repo_url: 'https://github.com/als-computing/ADAxisSXR40',
    })).toBe('https://github.com/als-computing/ADAxisSXR40');
    expect(githubBlobUrl({
      repo_url: 'https://github.com/als-computing/ADAxisSXR40',
      repo_commit_sha: 'cee83cc9f9ad92ee69c2b5c8386dc9aa91d42284',
    })).toBe('https://github.com/als-computing/ADAxisSXR40');
  });

  it('collects ALS, blueprint, OpenAlex, and GitHub source links', () => {
    const node: LiveGraphNode = {
      id: 'beamline:AnalogInput-ai-0',
      label: 'AI 0',
      type: 'AnalogInput',
      description: 'Reserved analog input.',
      graph_id: 'bl1101',
      graph_label: '11.0.1.2 ops',
      source_papers: [
        'als.lbl.gov/beamlines/11-0-1-2',
        'blueprint_bl11012_version09092026.html',
        'https:__openalex.org_W7164235388.pdf',
      ],
      publications: [
        { source_paper: 'als.lbl.gov/beamlines/11-0-1-2' },
        { source_paper: 'blueprint_bl11012_version09092026.html' },
      ],
      extra_fields: { pv: 'SIM11012:ai_0', ophyd_name: 'ai_0' },
    };

    const links = collectNodeSourceLinks(node);
    expect(links).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: 'als', url: 'https://als.lbl.gov/beamlines/11-0-1-2/' }),
      expect.objectContaining({ kind: 'blueprint', label: expect.stringContaining('blueprint_bl11012') }),
      expect.objectContaining({ kind: 'openalex', url: 'https://openalex.org/W7164235388' }),
    ]));
    expect(isLiteraturePublication({ source_paper: 'als.lbl.gov/beamlines/11-0-1-2' })).toBe(false);
    expect(remainingNodeProperties(node).map(row => row.label)).toEqual(
      expect.arrayContaining(['ID', 'PV', 'Ophyd name']),
    );
  });
});
