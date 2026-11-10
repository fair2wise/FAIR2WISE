/**
 * Default-view policy for large knowledge graphs in the KG viewer (owner decision 2026-11-10, twenty-second set).
 *
 * Measured with headless Chromium (docs/migration/reviews/H7/neighborhood_cap_evaluation.md): drawing the whole
 * 22,012-node RSoXS KG in the SVG view blocks the tab for about 26 s and creates ~455,000 DOM elements; the
 * 15,815-node OPV KG takes about 8 s. So above `LARGE_GRAPH_NODE_THRESHOLD` nodes the viewer opens on an overview
 * (the most connected nodes with the edges between them) instead of "All", search still covers every node, focusing
 * a node shows its (capped) neighbourhood, and "Show more" steps the overview up.
 *
 * Pure functions only; GraphMockup owns the state. Graphs at or below the threshold never go through this module's
 * overview path, so the existing small demo graphs behave exactly as before.
 */
import type { GraphPayload, LiveGraphNode } from './data/liveAgent';
import { isUnknownNodeCategory } from './kgNodeColors';

/** Above this many nodes in the loaded graph the default view is an overview, not "All". */
export const LARGE_GRAPH_NODE_THRESHOLD = 1_500;
/** Label of an overview size: sizes above `SLOW_LAYOUT_NODE_LIMIT` are marked slow before anyone picks them. */
export function overviewStepLabel(count: number): string {
  const text = count.toLocaleString('en-US');
  return count > SLOW_LAYOUT_NODE_LIMIT ? `${text} (slow)` : text;
}

/** Nodes of the first overview. */
export const OVERVIEW_DEFAULT_NODES = 300;
/** "Show more" steps; the last one is the most the viewer will draw (SVG layout is slow beyond 2,000). */
export const OVERVIEW_STEPS: readonly number[] = [OVERVIEW_DEFAULT_NODES, 1_000, 2_000, 5_000];
/** Above this many displayed nodes the layout is slow: warn, and offer the fixed layout only. */
export const SLOW_LAYOUT_NODE_LIMIT = 2_000;
/** Largest focused neighbourhood drawn for a large graph (mirrors the server's `NEIGHBORHOOD_MAX_NODES`). */
export const FOCUS_NEIGHBORHOOD_MAX_NODES = 500;

export function isLargeGraph(nodeCount: number): boolean {
  return nodeCount > LARGE_GRAPH_NODE_THRESHOLD;
}

/** Force-directed layout is offered only up to `SLOW_LAYOUT_NODE_LIMIT` displayed nodes. */
export function forceLayoutAllowed(displayedNodeCount: number): boolean {
  return displayedNodeCount <= SLOW_LAYOUT_NODE_LIMIT;
}

export function overviewIsSlow(displayedNodeCount: number): boolean {
  return displayedNodeCount > SLOW_LAYOUT_NODE_LIMIT;
}

export interface OverviewSelection {
  graph: GraphPayload;
  /** Nodes drawn. */
  shown: number;
  /** Nodes eligible for the overview (all nodes, or all but the Unknown placeholders). */
  eligible: number;
  /** Nodes in the whole graph. */
  total: number;
  /** Unknown placeholder nodes in the whole graph. */
  unknownTotal: number;
  /** Unknown placeholders left out of the overview. */
  unknownHidden: number;
}

/** Degree of every eligible node: distinct neighbours among eligible nodes, self-loops ignored. */
function eligibleDegrees(graph: GraphPayload, eligible: Set<string>): Map<string, number> {
  const neighbours = new Map<string, Set<string>>();
  for (const id of eligible) neighbours.set(id, new Set());
  for (const edge of graph.edges) {
    if (edge.source === edge.target || !eligible.has(edge.source) || !eligible.has(edge.target)) continue;
    neighbours.get(edge.source)!.add(edge.target);
    neighbours.get(edge.target)!.add(edge.source);
  }
  return new Map([...neighbours].map(([id, set]) => [id, set.size]));
}

function byDegreeThenName(degree: Map<string, number>) {
  return (left: LiveGraphNode, right: LiveGraphNode) =>
    (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0)
    || (left.label || '').localeCompare(right.label || '')
    || left.id.localeCompare(right.id);
}

/**
 * The `limit` most connected nodes (degree ranked among the nodes eligible for the view, ties by label then id) with
 * the edges between them. Unknown placeholders are left out unless `includeUnknown`.
 */
export function overviewSelection(
  graph: GraphPayload,
  limit: number,
  options: { includeUnknown?: boolean } = {},
): OverviewSelection {
  const includeUnknown = Boolean(options.includeUnknown);
  const unknownTotal = graph.nodes.filter(node => isUnknownNodeCategory(node.type)).length;
  const candidates = includeUnknown ? graph.nodes : graph.nodes.filter(node => !isUnknownNodeCategory(node.type));
  const degree = eligibleDegrees(graph, new Set(candidates.map(node => node.id)));
  const keep = Math.max(0, Math.floor(limit));
  const nodes = [...candidates].sort(byDegreeThenName(degree)).slice(0, keep);
  const visible = new Set(nodes.map(node => node.id));
  return {
    graph: {
      nodes,
      edges: graph.edges.filter(edge => visible.has(edge.source) && visible.has(edge.target)),
      source_path: graph.source_path,
    },
    shown: nodes.length,
    eligible: candidates.length,
    total: graph.nodes.length,
    unknownTotal,
    unknownHidden: includeUnknown ? 0 : unknownTotal,
  };
}

/** The step after `current`, or `null` at the last one. */
export function nextOverviewStep(current: number): number | null {
  return OVERVIEW_STEPS.find(step => step > current) ?? null;
}

/**
 * Values of the "Nodes" selector for a large graph of `eligible` drawable nodes: every step below the count, then the
 * count itself (labelled "All") when the whole graph still fits in the last step.
 */
export function overviewOptions(eligible: number): Array<{ value: number; label: string }> {
  const options = OVERVIEW_STEPS.filter(step => step < eligible).map(step => ({
    value: step,
    label: overviewStepLabel(step),
  }));
  const last = OVERVIEW_STEPS[OVERVIEW_STEPS.length - 1];
  if (eligible <= last) options.push({ value: eligible, label: `All (${eligible.toLocaleString('en-US')})` });
  return options;
}

/** Snap a stored step to a value the selector lists (the graph, hence `eligible`, may have changed). */
export function clampOverviewStep(step: number, eligible: number): number {
  const values = overviewOptions(eligible).map(option => option.value);
  if (values.includes(step)) return step;
  return values.find(value => value >= step) ?? values[values.length - 1] ?? OVERVIEW_DEFAULT_NODES;
}

/**
 * Cap a focused neighbourhood at `max` nodes: the seed first, then the most connected others (degree inside the
 * neighbourhood). Edges are the induced edges of what is kept. `total` is the size before the cap.
 */
export function capNeighborhood(
  neighbourhood: GraphPayload,
  seedId: string,
  max: number = FOCUS_NEIGHBORHOOD_MAX_NODES,
): { graph: GraphPayload; truncated: boolean; total: number } {
  const total = neighbourhood.nodes.length;
  if (total <= max) return { graph: neighbourhood, truncated: false, total };
  const degree = eligibleDegrees(neighbourhood, new Set(neighbourhood.nodes.map(node => node.id)));
  const seed = neighbourhood.nodes.find(node => node.id === seedId);
  const others = neighbourhood.nodes.filter(node => node !== seed).sort(byDegreeThenName(degree));
  const nodes = [...(seed ? [seed] : []), ...others].slice(0, max);
  const visible = new Set(nodes.map(node => node.id));
  return {
    graph: {
      nodes,
      edges: neighbourhood.edges.filter(edge => visible.has(edge.source) && visible.has(edge.target)),
      source_path: neighbourhood.source_path,
    },
    truncated: true,
    total,
  };
}
