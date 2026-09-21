import type { GraphPayload, LiveGraphEdge, LiveGraphNode } from './data/liveAgent';

export type KgLayoutMode = 'existing' | 'force';

const BL1101_SNAPSHOT_RE = /^matkg_bl1101_v(\d+)\.json$/;
const RSOXS_SNAPSHOT_RE = /^matkg_rsoxs_v(\d+)\.json$/;

const GRAPH_ID_BY_NAME: Record<string, string> = {
  'matkg_rsoxs_v1.json': 'rsoxs_v1',
  'matkg_bl1101_v1.json': 'bl1101',
  'matkg_xray_papers_cborg_chat.json': 'xray_demo',
};

export function graphFilename(path: string): string {
  const normalized = path.replace(/\\/g, '/').trim();
  const parts = normalized.split('/');
  return parts[parts.length - 1] || '';
}

/** Match backend `graph_id_for_path` so the viewer can honor Settings primary. */
export function graphIdFromPath(path: string): string {
  const name = graphFilename(path);
  if (GRAPH_ID_BY_NAME[name]) return GRAPH_ID_BY_NAME[name];
  if (BL1101_SNAPSHOT_RE.test(name)) return 'bl1101';
  if (RSOXS_SNAPSHOT_RE.test(name)) return 'rsoxs_v1';
  const stem = name.replace(/\.json$/i, '') || 'graph';
  if (stem.startsWith('matkg_')) return stem.slice('matkg_'.length);
  return stem;
}

export function catalogIdsFromGraph(graph: GraphPayload): string[] {
  const ids = new Set<string>();
  for (const node of graph.nodes) {
    const gid = (node.graph_id || '').trim();
    if (gid) ids.add(gid);
  }
  return [...ids].sort();
}

export function isQueryOverlayGraph(graph: GraphPayload): boolean {
  return graph.source_path.replace(/\\/g, '/').includes('query:');
}

export function overlayCatalogColor(graphId: string): string {
  const id = graphId.trim().toLowerCase();
  if (id.startsWith('rsoxs')) return '#0ea5e9';
  if (id.startsWith('bl1101')) return '#d97706';
  if (id === 'tiled' || id.startsWith('tiled')) return '#7c3aed';
  if (id.includes('xray')) return '#a855f7';
  return '#64748b';
}

/**
 * Viewer dumps stay one catalog (Settings primary). Query hits may overlay
 * multiple graph_ids. Never concatenate rsoxs + bl1101 full dumps.
 */
export function honorViewerCatalogs(
  graph: GraphPayload,
  primaryGraphId: string,
): GraphPayload {
  const ids = catalogIdsFromGraph(graph);
  if (ids.length <= 1) return graph;
  if (isQueryOverlayGraph(graph)) return graph;

  const primary = primaryGraphId.trim();
  if (!primary) return graph;

  const nodes = graph.nodes.filter(node => {
    const gid = (node.graph_id || '').trim();
    return !gid || gid === primary;
  });
  if (nodes.length === 0 || nodes.length === graph.nodes.length) return graph;

  const visible = new Set(nodes.map(node => node.id));
  return {
    nodes,
    edges: graph.edges.filter(edge => visible.has(edge.source) && visible.has(edge.target)),
    source_path: graph.source_path,
  };
}

export function undirectedAdjacency(
  edges: Array<Pick<LiveGraphEdge, 'source' | 'target'>>,
): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>();
  const add = (from: string, to: string) => {
    if (!from || !to || from === to) return;
    const bucket = adj.get(from) ?? new Set<string>();
    bucket.add(to);
    adj.set(from, bucket);
  };
  for (const edge of edges) {
    add(edge.source, edge.target);
    add(edge.target, edge.source);
  }
  return adj;
}

/** Cite-click shows this many connected hops around the focused node. */
export const CITE_FOCUS_HOPS = 3;

const TILED_IDENTITY_TYPES = new Set(['esaf', 'proposal', 'sample', 'blueskyrun']);
const TILED_ID_RE = /^(tiled:|beamline:esaf|beamline:proposal|beamline:sample|beamline:scan)/i;
const ESAF_FOCUS_RE = /\besaf[-_\s]?\d{4}[-_]\d+/i;

export function looksLikeTiledIdentityRef(
  nodeId: string,
  node?: Pick<LiveGraphNode, 'id' | 'graph_id' | 'type'> | null,
): boolean {
  const gid = (node?.graph_id || '').toLowerCase();
  if (gid === 'tiled' || gid.startsWith('tiled')) return true;
  const type = (node?.type || '').toLowerCase();
  if (TILED_IDENTITY_TYPES.has(type)) return true;
  const id = (node?.id || nodeId || '').trim();
  return TILED_ID_RE.test(id) || ESAF_FOCUS_RE.test(id);
}

const TILED_IDENTITY_PREDICATES = new Set([
  'rel:hasproposal',
  'rel:hassample',
  'rel:hasscan',
  'prov:used',
]);

export function hasTiledIdentityEdges(graph: GraphPayload | null | undefined): boolean {
  if (!graph) return false;
  return graph.edges.some(edge => TILED_IDENTITY_PREDICATES.has((edge.predicate || '').toLowerCase()));
}

export function mergeGraphPayloads(base: GraphPayload, extra: GraphPayload): GraphPayload {
  const nodes = [...base.nodes];
  const seenNodes = new Set(base.nodes.map(node => node.id));
  for (const node of extra.nodes) {
    if (seenNodes.has(node.id)) continue;
    seenNodes.add(node.id);
    nodes.push(node);
  }
  const edges = [...base.edges];
  const seenEdges = new Set(base.edges.map(edge => `${edge.source}\0${edge.predicate}\0${edge.target}`));
  for (const edge of extra.edges) {
    const key = `${edge.source}\0${edge.predicate}\0${edge.target}`;
    if (seenEdges.has(key)) continue;
    seenEdges.add(key);
    edges.push(edge);
  }
  return {
    nodes,
    edges,
    source_path: extra.source_path || base.source_path,
  };
}

function tokenBoundaryMatch(haystack: string, needle: string): boolean {
  if (!needle || needle.length < 3) return false;
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?:^|[^a-z0-9])${escaped}(?:$|[^a-z0-9])`).test(haystack);
}

export function overlayNodeIdAliases(nodeId: string): string[] {
  const raw = nodeId.trim();
  if (!raw) return [];
  const aliases = [raw];
  if (/^tiled:/i.test(raw)) {
    const rest = raw.replace(/^tiled:/i, '');
    if (rest) aliases.push(rest);
  }
  const parts = raw.split(':').filter(Boolean);
  if (parts.length >= 2) aliases.push(parts.slice(-2).join(':'));
  if (parts.length >= 1) aliases.push(parts[parts.length - 1]);
  return [...new Set(aliases.filter(Boolean))];
}

export function resolveViewerNodeId(graph: GraphPayload, nodeId: string): string | null {
  const raw = nodeId.trim();
  if (!raw) return null;
  const aliases = overlayNodeIdAliases(raw);
  const lowerAliases = new Set(aliases.map(item => item.toLowerCase()));
  const lower = raw.toLowerCase();
  const label = raw.replace(/^tiled:/i, '').replace(/^matkg:/i, '').replace(/[_-]+/g, ' ').trim().toLowerCase();

function extraText(node: LiveGraphNode) {
    const extra = (node.extra_fields || {}) as Record<string, unknown>;
    const keys = [
      'esaf', 'esaf_id', 'esaf_number',
      'sample', 'sample_id', 'sample_code',
      'scan', 'scan_id', 'uid',
      'proposal', 'proposal_id', 'proposal_code',
    ];
    const values = keys.map(key => String(extra[key] ?? '').trim().toLowerCase()).filter(Boolean);
    for (const entry of node.properties || []) {
      if (!entry || typeof entry !== 'object') continue;
      const record = entry as Record<string, unknown>;
      const label = String(record.property || record.name || '').trim().toLowerCase();
      if (!keys.includes(label)) continue;
      const value = String(record.value ?? '').trim().toLowerCase();
      if (value) values.push(value);
    }
    return values;
  }

  const scored: Array<{ id: string; rank: number }> = [];
  for (const node of graph.nodes) {
    const nodeLabel = node.label.replace(/[_-]+/g, ' ').trim().toLowerCase();
    const nodeIdNorm = node.id.replace(/[_-]+/g, ' ').trim().toLowerCase();
    const typedBonus = (node.type || '').toLowerCase() === 'unknown' ? 0 : 25;
    if (node.id === raw || aliases.includes(node.id)) {
      scored.push({ id: node.id, rank: 100 + typedBonus });
      continue;
    }
    if (node.id.toLowerCase() === lower || lowerAliases.has(node.id.toLowerCase())) {
      scored.push({ id: node.id, rank: 90 + typedBonus });
      continue;
    }
    if (nodeLabel === label || node.label.trim().toLowerCase() === label) {
      scored.push({ id: node.id, rank: 80 + typedBonus });
      continue;
    }
    const extras = extraText(node);
    if (extras.some(value => value === lower || value === label)) {
      scored.push({ id: node.id, rank: 70 + typedBonus });
      continue;
    }
    if (label.length >= 3 && (nodeLabel.startsWith(`${label} `) || nodeIdNorm.startsWith(`matkg:${label} `))) {
      scored.push({ id: node.id, rank: 65 + typedBonus });
      continue;
    }
    if (tokenBoundaryMatch(nodeLabel, label) || tokenBoundaryMatch(nodeIdNorm, label)) {
      scored.push({ id: node.id, rank: 60 + typedBonus });
      continue;
    }
    if (label.length >= 6 && extras.some(value => value.includes(label) || label.includes(value))) {
      scored.push({ id: node.id, rank: 50 + typedBonus });
    }
  }
  if (scored.length === 0) return null;
  scored.sort((a, b) => b.rank - a.rank);
  return scored[0].id;
}

function resolveGraphNodeId(graph: GraphPayload, nodeId: string): string | null {
  return resolveViewerNodeId(graph, nodeId);
}

function nodeByIdMap(graph: GraphPayload): Map<string, LiveGraphNode> {
  return new Map(graph.nodes.map(node => [node.id, node]));
}

function isTiledGraphId(graphId: string | undefined): boolean {
  const id = (graphId || '').trim().toLowerCase();
  return id === 'tiled' || id.startsWith('tiled');
}

function sameCatalog(seed: LiveGraphNode, candidate: LiveGraphNode | undefined): boolean {
  if (!candidate) return false;
  const seedId = (seed.graph_id || '').trim();
  const candidateId = (candidate.graph_id || '').trim();
  // JSON dumps tag ESAF/Proposal nodes as bl1101/rsoxs and have no Tiled
  // edges. Allow BFS to follow GraphQL identity neighbors anyway.
  if (isTiledGraphId(candidateId) && (isTiledGraphId(seedId) || looksLikeTiledIdentityRef(seed.id, seed))) {
    return true;
  }
  if (!seedId) return true;
  return !candidateId || candidateId === seedId;
}

/**
 * Undirected BFS from `nodeId` for `hops` steps. Stays on the seed node's
 * graph_id so rsoxs + bl1101 dumps are never concatenated.
 */
export function neighborhoodNodeIds(
  graph: GraphPayload,
  nodeId: string,
  hops: number,
): string[] {
  const seedId = resolveGraphNodeId(graph, nodeId);
  if (!seedId) return [];
  const nodes = nodeByIdMap(graph);
  const seed = nodes.get(seedId);
  if (!seed) return [];

  const depth = Math.max(0, Math.floor(hops));
  const adj = undirectedAdjacency(graph.edges);
  const seen = new Set<string>([seedId]);
  let frontier = [seedId];

  for (let hop = 0; hop < depth && frontier.length > 0; hop += 1) {
    const next: string[] = [];
    for (const current of frontier) {
      for (const neighbor of adj.get(current) ?? []) {
        if (seen.has(neighbor)) continue;
        if (!sameCatalog(seed, nodes.get(neighbor))) continue;
        seen.add(neighbor);
        next.push(neighbor);
      }
    }
    frontier = next;
  }
  return [...seen];
}

export function neighborhoodSubgraph(
  graph: GraphPayload,
  nodeId: string,
  hops: number = CITE_FOCUS_HOPS,
): GraphPayload {
  const ids = neighborhoodNodeIds(graph, nodeId, hops);
  if (ids.length === 0) {
    return { nodes: [], edges: [], source_path: graph.source_path };
  }
  const wanted = new Set(ids);
  return {
    nodes: graph.nodes.filter(node => wanted.has(node.id)),
    edges: graph.edges.filter(edge => wanted.has(edge.source) && wanted.has(edge.target)),
    source_path: graph.source_path,
  };
}

export function isolateNeighborIds(
  edges: Array<Pick<LiveGraphEdge, 'source' | 'target'>>,
  centerId: string,
): Set<string> {
  const neighbors = new Set<string>([centerId]);
  for (const edge of edges) {
    if (edge.source === centerId) neighbors.add(edge.target);
    if (edge.target === centerId) neighbors.add(edge.source);
  }
  return neighbors;
}

export function shortestUndirectedPath(
  edges: Array<Pick<LiveGraphEdge, 'source' | 'target'>>,
  sourceId: string,
  targetId: string,
): string[] | null {
  if (!sourceId || !targetId) return null;
  if (sourceId === targetId) return [sourceId];

  const adj = undirectedAdjacency(edges);
  const queue: string[][] = [[sourceId]];
  const visited = new Set<string>([sourceId]);

  while (queue.length > 0) {
    const path = queue.shift()!;
    const current = path[path.length - 1];
    for (const next of adj.get(current) ?? []) {
      if (visited.has(next)) continue;
      const nextPath = [...path, next];
      if (next === targetId) return nextPath;
      visited.add(next);
      queue.push(nextPath);
    }
  }
  return null;
}

export function nodeDegreeMap(
  nodes: Array<Pick<LiveGraphNode, 'id'>>,
  edges: Array<Pick<LiveGraphEdge, 'source' | 'target'>>,
): Map<string, number> {
  const degrees = new Map<string, number>();
  for (const node of nodes) degrees.set(node.id, 0);
  for (const edge of edges) {
    degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1);
    degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1);
  }
  return degrees;
}

export function pathContainsEdge(path: string[] | null, source: string, target: string): boolean {
  if (!path || path.length < 2) return false;
  for (let index = 0; index < path.length - 1; index += 1) {
    const a = path[index];
    const b = path[index + 1];
    if ((a === source && b === target) || (a === target && b === source)) return true;
  }
  return false;
}
