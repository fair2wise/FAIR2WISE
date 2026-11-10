/**
 * Deterministic synthetic knowledge graphs for the large-graph viewer specs and measurements. Same wire shape as
 * `GET /graph` (nodes with id/label/type/description/graph_id, edges with source/target/predicate) but generated
 * locally, so no demo corpus is needed. Preferential attachment gives the heavy-tailed degree distribution of the
 * real RSoXS and OPV graphs (a few hubs, a long tail of leaves).
 */

export interface BigGraphNode {
  id: string;
  label: string;
  type: string;
  description: string;
  graph_id: string;
  graph_label: string;
}
export interface BigGraphEdge { source: string; target: string; predicate: string }
export interface BigGraph { nodes: BigGraphNode[]; edges: BigGraphEdge[]; source_path: string }

export interface BigGraphSpec {
  nodes: number;
  edges: number;
  /** Share of nodes typed `Unknown` (placeholder stubs of paper-derived KGs). */
  unknownShare?: number;
  /** Share of edges that point at an Unknown placeholder. */
  unknownEdgeShare?: number;
  /** Degrees of explicit hubs: node `n0`, `n1`, ... gets that many extra links to typed nodes. */
  hubDegrees?: number[];
  seed?: number;
  graphId?: string;
  sourcePath?: string;
}

const TYPES = ['Material', 'Process', 'Property', 'Measurement', 'Instrument', 'Technique', 'Device'];
const PREDICATES = ['rel:related_to', 'rel:part_of', 'rel:has_property', 'rel:measures', 'rel:processed_by'];

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function syntheticGraph(spec: BigGraphSpec): BigGraph {
  const rand = mulberry32(spec.seed ?? 7);
  const graphId = spec.graphId ?? 'big';
  const unknownShare = spec.unknownShare ?? 0;
  const unknownEdgeShare = spec.unknownEdgeShare ?? 0;
  const nodes: BigGraphNode[] = [];
  const known: number[] = [];
  const unknown: number[] = [];
  for (let index = 0; index < spec.nodes; index += 1) {
    // The first 40 nodes are always typed so hubs are real entities.
    const isUnknown = index >= 40 && rand() < unknownShare;
    nodes.push({
      id: `${graphId}:n${index}`,
      label: isUnknown ? `Unknown ${index}` : `${TYPES[index % TYPES.length]} ${index}`,
      type: isUnknown ? 'Unknown' : TYPES[index % TYPES.length],
      description: isUnknown ? '' : `Synthetic ${TYPES[index % TYPES.length].toLowerCase()} number ${index}.`,
      graph_id: graphId,
      graph_label: graphId,
    });
    (isUnknown ? unknown : known).push(index);
  }
  // Endpoint bags: a node appears once per incident edge plus once (so every node can be picked).
  const knownBag = [...known];
  const unknownBag = [...unknown];
  const seen = new Set<string>();
  const edges: BigGraphEdge[] = [];
  const pick = (bag: number[]) => bag[Math.floor(rand() * bag.length)];
  (spec.hubDegrees ?? []).forEach((degree, hub) => {
    for (let target = 40 + hub * 1_000, linked = 0; linked < degree && target < spec.nodes; target += 1) {
      if (target === hub || nodes[target].type === 'Unknown') continue;
      seen.add(`${hub}>${target}`);
      edges.push({ source: nodes[hub].id, target: nodes[target].id, predicate: 'rel:related_to' });
      linked += 1;
    }
  });
  let guard = 0;
  while (edges.length < spec.edges && guard < spec.edges * 20) {
    guard += 1;
    const toUnknown = unknown.length > 0 && rand() < unknownEdgeShare;
    const from = pick(knownBag);
    const to = toUnknown ? pick(unknownBag) : pick(knownBag);
    if (from === to) continue;
    const key = `${from}>${to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({
      source: nodes[from].id,
      target: nodes[to].id,
      predicate: PREDICATES[edges.length % PREDICATES.length],
    });
    knownBag.push(from);
    (toUnknown ? unknownBag : knownBag).push(to);
  }
  return { nodes, edges, source_path: spec.sourcePath ?? `source:json:${graphId}` };
}

/** Sizes of the two demo graphs (owner decision 2026-11-10) plus the 2k and 10k stress sizes. */
export const BIG_GRAPH_SPECS = {
  n2k: { nodes: 2_000, edges: 5_000, unknownShare: 0.1, unknownEdgeShare: 0.1, graphId: 'big2k' },
  n10k: { nodes: 10_000, edges: 26_000, unknownShare: 0.1, unknownEdgeShare: 0.1, graphId: 'big10k' },
  rsoxs22k: { nodes: 22_012, edges: 57_575, unknownShare: 0.12, unknownEdgeShare: 0.15, graphId: 'rsoxs_v4', hubDegrees: [670, 396, 300] },
  opv15k: { nodes: 15_815, edges: 20_096, unknownShare: 0.55, unknownEdgeShare: 0.7, graphId: 'qwen3_235b_580papers' },
} satisfies Record<string, BigGraphSpec>;
