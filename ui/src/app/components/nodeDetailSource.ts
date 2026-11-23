/**
 * Which graph source a node's full record is read from when the node was opened from the chat (owner bug list
 * 2026-11, task 7).
 *
 * A node clicked in the KG viewer is read from the source the viewer shows. A node cited in the chat answer comes
 * from the chat's own graph set: on `:local` with two or more corpora live the chat answers over each corpus and every
 * node names its corpus (`corpus`, else `graph_id`, which is the corpus name there). The viewer's source (or the
 * default graph) may not hold that node, or may hold another corpus' node with the same id, so the chat path asks
 * the sources listing for the source whose `graph_id` is the node's corpus and reads `GET /graph/node/{id}?source=` from
 * it, the same route and the same record the viewer shows.
 */
import type { LiveGraphNode } from './data/liveAgent';
import type { GraphSourceInfo, GraphSourceListing } from './graphSourcesApi';

/** The corpus a chat node names: `corpus` when the answer carried one, else its `graph_id`. */
export function nodeCorpus(node: Pick<LiveGraphNode, 'corpus' | 'graph_id'>): string | null {
  const value = (node.corpus || node.graph_id || '').trim();
  return value || null;
}

function releaseVersion(source: GraphSourceInfo): number {
  const match = source.id.match(/\/v(\d+)$/);
  return match ? Number(match[1]) : -1;
}

/** Rank of a candidate: the live Tiled record, then the newest adopted release, then a flat JSON file. */
function rank(source: GraphSourceInfo): number {
  if (source.kind === 'tiled') return 3_000_000;
  const version = releaseVersion(source);
  return version >= 0 ? 2_000_000 + version : 1_000_000;
}

/**
 * The source id to read `node` from, or `null` when the chat node names no corpus or the listing has no source for
 * it (the caller then reads the viewer's source or the default graph, as before).
 */
export function sourceForChatNode(
  node: Pick<LiveGraphNode, 'corpus' | 'graph_id'>,
  viewerSource: string | null,
  listing: GraphSourceListing | undefined,
): string | null {
  const corpus = nodeCorpus(node);
  if (!corpus || !listing) return null;
  const candidates = listing.sources.filter(source => source.available && source.graph_id === corpus);
  if (candidates.length === 0) return null;
  const shown = candidates.find(source => source.id === viewerSource);
  if (shown) return shown.id;
  return [...candidates].sort((a, b) => rank(b) - rank(a) || a.id.localeCompare(b.id))[0].id;
}
