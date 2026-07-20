import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Crosshair, Loader2, Maximize2, Pencil, Plus, Search, Trash2, X, ZoomIn, ZoomOut } from 'lucide-react';
import { AsciiOrb } from './AsciiOrb';
import { CodeBlock } from './CodeBlock';
import { KGHoverPopup, KGHoverTarget } from './KGInfoPanel';
import { PublicationList } from './PublicationList';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from './ui/resizable';
import { loadAgentSettings } from './agentSettings';
import {
  fetchGraphNodeDetail,
  fetchLiveGraph,
  GraphPayload,
  GraphRelationshipUpdate,
  LinkedCodeSnippet,
  LiveGraphNode,
  PublicationInfo,
  searchGraphNodes,
  updateGraphNode,
} from './data/liveAgent';
import { getNodeColor, isUnknownNodeCategory, SCHEMA_NODE_CLASSES } from './kgNodeColors';

const W = 900;
const H = 640;
const R = 16;
const ARROW_SIZE = 13;
const MIN_VIEW = 180;
const WORLD_PADDING = 96;

interface LayoutNode extends LiveGraphNode {
  color: string;
  x: number;
  y: number;
}

interface LayoutResult {
  nodes: LayoutNode[];
  width: number;
  height: number;
}

interface ViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphMockupProps {
  graph: GraphPayload;
  highlightedNodeIds: string[];
  /** Node IDs cited in the answer via [KG: ...], in citation order. */
  citedNodeIds?: string[];
  /** Changes when a new assistant answer drives citations; restarts pulse animations. */
  citationAnimationKey?: string;
  onNodeUpdated?: (node: LiveGraphNode, graph?: GraphPayload) => void;
  isKgViewer?: boolean;
  kgViewerNodeLimit?: number | 'all';
  onToggleKgViewer?: () => void;
  onKgViewerNodeLimitChange?: (limit: number | 'all') => void;
}

export const SCHEMA_RELATIONSHIP_PREDICATES = [
  'rel:related_to',
  'rel:part_of',
  'rel:has_property',
  'rel:processed_by',
  'rel:used_in',
  'rel:causes',
  'rel:affects',
  'rel:measures',
  'rel:occurs_in',
  'rel:contains',
  'rel:applied_to',
  'rel:composed_of',
  'rel:belongs_to',
  'rel:forms_on',
  'rel:provides_site_for',
  'rel:has_code_snippet',
] as const;

export function normalizeRelationshipPredicate(value: string): string | null {
  const cleaned = value.trim();
  if (!cleaned) return null;
  if (cleaned.includes(':')) {
    return /^[A-Za-z][A-Za-z0-9_.-]*:[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(cleaned)
      ? cleaned
      : null;
  }
  const localName = cleaned.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();
  return localName ? `rel:${localName}` : null;
}

export function oneHopNodeIds(graph: GraphPayload, nodeId: string): string[] {
  const ids = new Set([nodeId]);
  for (const edge of graph.edges) {
    if (edge.source === nodeId) ids.add(edge.target);
    if (edge.target === nodeId) ids.add(edge.source);
  }
  return Array.from(ids);
}

export function connectedGraphSubset(graph: GraphPayload, requestedLimit: number): GraphPayload {
  const limit = Math.max(0, Math.floor(requestedLimit));
  const candidates = graph.nodes.filter(node => !isUnknownNodeCategory(node.type));
  if (limit === 0 || candidates.length === 0) {
    return { nodes: [], edges: [], source_path: graph.source_path };
  }

  const nodeById = new Map(candidates.map(node => [node.id, node]));
  const neighbors = new Map(candidates.map(node => [node.id, new Set<string>()]));
  for (const edge of graph.edges) {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target) || edge.source === edge.target) continue;
    neighbors.get(edge.source)?.add(edge.target);
    neighbors.get(edge.target)?.add(edge.source);
  }

  const compareNodeIds = (leftId: string, rightId: string) => {
    const degreeDifference = (neighbors.get(rightId)?.size ?? 0) - (neighbors.get(leftId)?.size ?? 0);
    if (degreeDifference !== 0) return degreeDifference;
    const left = nodeById.get(leftId)!;
    const right = nodeById.get(rightId)!;
    return left.label.localeCompare(right.label) || left.id.localeCompare(right.id);
  };
  const rankedIds = candidates.map(node => node.id).sort(compareNodeIds);
  const selectedIds: string[] = [];
  const selected = new Set<string>();

  for (const seedId of rankedIds) {
    if (selected.size >= limit) break;
    if (selected.has(seedId)) continue;
    const queue = [seedId];
    selected.add(seedId);

    while (queue.length > 0 && selectedIds.length < limit) {
      const nodeId = queue.shift()!;
      selectedIds.push(nodeId);
      if (selectedIds.length >= limit) break;
      const nextIds = Array.from(neighbors.get(nodeId) ?? []).sort(compareNodeIds);
      for (const nextId of nextIds) {
        if (selected.size >= limit) break;
        if (selected.has(nextId)) continue;
        selected.add(nextId);
        queue.push(nextId);
      }
    }
  }

  const visibleIds = new Set(selectedIds);
  return {
    nodes: selectedIds.map(nodeId => nodeById.get(nodeId)!),
    edges: graph.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    source_path: graph.source_path,
  };
}

function hexToRgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

function clamp(value: number, min: number, max: number) {
  if (max < min) return min;
  return Math.max(min, Math.min(max, value));
}

function fullViewBox(width: number, height: number): ViewBox {
  return { x: 0, y: 0, width, height };
}

function nodeSetKey(nodes: LayoutNode[]): string {
  let hash = 2166136261;
  for (const node of nodes) {
    for (let index = 0; index < node.id.length; index += 1) {
      hash ^= node.id.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
  }
  return `${nodes.length}:${hash >>> 0}`;
}

function nodeIsInView(node: LayoutNode, viewBox: ViewBox, padding = 72): boolean {
  return node.x >= viewBox.x - padding
    && node.x <= viewBox.x + viewBox.width + padding
    && node.y >= viewBox.y - padding
    && node.y <= viewBox.y + viewBox.height + padding;
}

function edgeIsInView(source: LayoutNode, target: LayoutNode, viewBox: ViewBox, padding = 72): boolean {
  const left = viewBox.x - padding;
  const right = viewBox.x + viewBox.width + padding;
  const top = viewBox.y - padding;
  const bottom = viewBox.y + viewBox.height + padding;
  return Math.max(source.x, target.x) >= left
    && Math.min(source.x, target.x) <= right
    && Math.max(source.y, target.y) >= top
    && Math.min(source.y, target.y) <= bottom;
}

function clampViewBox(viewBox: ViewBox, worldWidth: number, worldHeight: number): ViewBox {
  const width = clamp(viewBox.width, MIN_VIEW, worldWidth);
  const height = clamp(viewBox.height, MIN_VIEW * (H / W), worldHeight);
  return {
    x: clamp(viewBox.x, 0, Math.max(0, worldWidth - width)),
    y: clamp(viewBox.y, 0, Math.max(0, worldHeight - height)),
    width,
    height,
  };
}

function layoutGraph(graph: GraphPayload): LayoutResult {
  const count = graph.nodes.length;
  if (count === 0) return { nodes: [], width: W, height: H };
  const ordered = count > 300
    ? [...graph.nodes]
    : [...graph.nodes].sort((a, b) => a.label.localeCompare(b.label));

  const aspect = W / H;
  const spacing = count > 240 ? 70 : count > 120 ? 82 : count > 50 ? 96 : 116;
  const area = Math.max(W * H, count * spacing * spacing);
  const width = Math.max(W, Math.ceil(Math.sqrt(area * aspect)) + WORLD_PADDING * 2);
  const height = Math.max(H, Math.ceil(width / aspect));
  const cx = width / 2;
  const cy = height / 2;
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const radiusStep = Math.min(width, height) / (2.4 * Math.sqrt(count));

  const nodes = ordered.map((node, index) => {
    const radius = radiusStep * Math.sqrt(index + 0.5);
    const angle = index * goldenAngle;
    return {
      ...node,
      color: getNodeColor(node.type),
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    };
  });

  // Full KGs can contain thousands of nodes. Golden-angle placement is stable and
  // linear; skip the quadratic force pass that is only useful for smaller subsets.
  if (count > 300) return { nodes, width, height };

  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const links = graph.edges
    .map(edge => [indexById.get(edge.source), indexById.get(edge.target)] as const)
    .filter((edge): edge is readonly [number, number] => edge[0] !== undefined && edge[1] !== undefined && edge[0] !== edge[1]);
  const vx = new Array(count).fill(0);
  const vy = new Array(count).fill(0);
  const iterations = count > 180 ? 150 : 190;
  const linkDistance = count > 160 ? 92 : 112;
  const minDistance = count > 220 ? 48 : count > 90 ? 56 : 66;
  const charge = count > 180 ? 1450 : 2200;

  for (let tick = 0; tick < iterations; tick += 1) {
    const alpha = 1 - tick / iterations;

    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let dx = nodes[j].x - nodes[i].x;
        let dy = nodes[j].y - nodes[i].y;
        let distSq = dx * dx + dy * dy;
        if (distSq < 0.01) {
          dx = (j - i) * 0.01;
          dy = (i + j) * 0.01;
          distSq = dx * dx + dy * dy;
        }
        const dist = Math.sqrt(distSq);
        const repel = Math.min(5.2, charge / distSq) * alpha;
        const nx = dx / dist;
        const ny = dy / dist;
        vx[i] -= nx * repel;
        vy[i] -= ny * repel;
        vx[j] += nx * repel;
        vy[j] += ny * repel;

        if (dist < minDistance) {
          const push = (minDistance - dist) * 0.14 * alpha;
          vx[i] -= nx * push;
          vy[i] -= ny * push;
          vx[j] += nx * push;
          vy[j] += ny * push;
        }
      }
    }

    for (const [sourceIndex, targetIndex] of links) {
      const source = nodes[sourceIndex];
      const target = nodes[targetIndex];
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const pull = (dist - linkDistance) * 0.028 * alpha;
      const fx = (dx / dist) * pull;
      const fy = (dy / dist) * pull;
      vx[sourceIndex] += fx;
      vy[sourceIndex] += fy;
      vx[targetIndex] -= fx;
      vy[targetIndex] -= fy;
    }

    for (let i = 0; i < count; i += 1) {
      vx[i] += (cx - nodes[i].x) * 0.0025 * alpha;
      vy[i] += (cy - nodes[i].y) * 0.0025 * alpha;
      nodes[i].x = clamp(nodes[i].x + vx[i], WORLD_PADDING, width - WORLD_PADDING);
      nodes[i].y = clamp(nodes[i].y + vy[i], WORLD_PADDING, height - WORLD_PADDING);
      vx[i] *= 0.72;
      vy[i] *= 0.72;
    }
  }

  return { nodes, width, height };
}

function splitLabel(label: string) {
  const clean = label.length > 28 ? `${label.slice(0, 25)}...` : label;
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [clean];
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')];
}

function directedEdgeGeometry(
  src: { x: number; y: number },
  tgt: { x: number; y: number },
  nodeRadius = R,
) {
  const dx = tgt.x - src.x;
  const dy = tgt.y - src.y;
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist;
  const uy = dy / dist;
  const startPad = nodeRadius + 1;
  const endPad = nodeRadius + ARROW_SIZE * 0.7;
  const x1 = src.x + ux * startPad;
  const y1 = src.y + uy * startPad;
  const x2 = tgt.x - ux * endPad;
  const y2 = tgt.y - uy * endPad;
  const tipX = tgt.x - ux * (nodeRadius + 1);
  const tipY = tgt.y - uy * (nodeRadius + 1);
  const baseX = tipX - ux * ARROW_SIZE;
  const baseY = tipY - uy * ARROW_SIZE;
  const halfWidth = ARROW_SIZE * 0.42;
  const leftX = baseX - uy * halfWidth;
  const leftY = baseY + ux * halfWidth;
  const rightX = baseX + uy * halfWidth;
  const rightY = baseY - ux * halfWidth;

  return {
    x1,
    y1,
    x2,
    y2,
    length: Math.max(Math.hypot(x2 - x1, y2 - y1), 1),
    arrowPoints: `${tipX},${tipY} ${leftX},${leftY} ${rightX},${rightY}`,
  };
}

function fitNodesViewBox(nodes: LayoutNode[], worldWidth: number, worldHeight: number): ViewBox {
  const minX = Math.min(...nodes.map(node => node.x));
  const maxX = Math.max(...nodes.map(node => node.x));
  const minY = Math.min(...nodes.map(node => node.y));
  const maxY = Math.max(...nodes.map(node => node.y));
  const padding = 90;
  const aspect = W / H;
  let width = Math.max(MIN_VIEW, maxX - minX + padding * 2);
  let height = Math.max(MIN_VIEW * (H / W), maxY - minY + padding * 2);

  if (width / height > aspect) {
    height = width / aspect;
  } else {
    width = height * aspect;
  }

  return clampViewBox(
    {
      x: minX + (maxX - minX) / 2 - width / 2,
      y: minY + (maxY - minY) / 2 - height / 2,
      width,
      height,
    },
    worldWidth,
    worldHeight,
  );
}

function uploadedGraphQueryParam(sourcePath: string): string | undefined {
  if (!sourcePath) return undefined;
  const normalized = sourcePath.replace(/\\/g, '/');
  return normalized.includes('/uploads/') ? sourcePath : undefined;
}

function emptyPublication(): PublicationInfo {
  return {
    paper_title: '',
    authors: [],
    publication_year: undefined,
    journal: '',
    doi: '',
    source_paper: '',
  };
}

function emptyLinkedSnippet(): LinkedCodeSnippet & { id: string } {
  return {
    id: `temp:${crypto.randomUUID()}`,
    label: '',
    function_name: '',
    code_language: 'python',
    code_snippet: '',
    publications: [],
  };
}

function isCodeSnippetNode(node: Pick<LiveGraphNode, 'type' | 'code_snippet'>): boolean {
  return String(node.type || '').toLowerCase() === 'codesnippet' || Boolean(node.code_snippet?.trim());
}

function NodeSearchControl({
  activeNodeId,
  onSelect,
}: {
  activeNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Array<{ node: LiveGraphNode; score: number }>>([]);
  const [retrievalBackend, setRetrievalBackend] = useState('');
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (!value || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await searchGraphNodes(value, 10);
      setResults(response.results);
      setRetrievalBackend(response.retrieval_backend);
      setSearched(true);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Node search failed');
      setResults([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Search nodes"
        title="Search Nodes"
        onClick={() => setOpen(value => !value)}
        className={`inline-flex h-9 items-center gap-2 rounded-md border px-4 text-sm font-medium ${
          activeNodeId
            ? 'border-sky-300 bg-sky-50 text-sky-700'
            : 'border-slate-200 bg-white text-slate-500 hover:text-slate-700'
        }`}
      >
        <Search size={13} />
        Search Nodes
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 w-96 rounded-xl border border-slate-200 bg-white p-4 text-left shadow-xl">
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="text-xs font-semibold text-slate-700">Search the current KG</span>
            <button
              type="button"
              aria-label="Close node search"
              onClick={() => setOpen(false)}
              className="text-slate-400 hover:text-slate-600"
            >
              <X size={14} />
            </button>
          </div>
          <form onSubmit={submitSearch} className="flex gap-3">
            <input
              autoFocus
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Describe a node…"
              className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none focus:border-sky-400"
            />
            <button
              type="submit"
              disabled={!query.trim() || loading}
              className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-sky-500 text-white hover:bg-sky-600 disabled:opacity-40"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            </button>
          </form>
          {error && <p className="mt-3 text-xs text-rose-600">{error}</p>}
          {!error && searched && results.length === 0 && (
            <p className="mt-3 text-xs text-slate-400">No matching nodes found.</p>
          )}
          {results.length > 0 && (
            <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-400">
                {retrievalBackend} ranking
              </div>
              {results.map(result => (
                <button
                  type="button"
                  key={result.node.id}
                  onClick={() => {
                    onSelect(result.node.id);
                    setOpen(false);
                  }}
                  className="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-slate-50"
                >
                  <span
                    className="mt-1 h-2 w-2 shrink-0 rounded-full"
                    style={{ background: getNodeColor(result.node.type) }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-slate-700">{result.node.label}</span>
                    <span className="block truncate text-[10px] text-slate-400">{result.node.type}</span>
                  </span>
                  <span className="text-[10px] tabular-nums text-slate-400">
                    {Math.round(result.score * 100)}%
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NodeDetailPanel({
  node,
  graph,
  graphSourcePath,
  onClose,
  onNodeUpdated,
}: {
  node: LayoutNode;
  graph: GraphPayload;
  graphSourcePath: string;
  onClose: () => void;
  onNodeUpdated?: (node: LiveGraphNode, graph?: GraphPayload) => void;
}) {
  const [detail, setDetail] = useState<LiveGraphNode | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState('');
  const [draftType, setDraftType] = useState('');
  const [draftDescription, setDraftDescription] = useState('');
  const [draftCode, setDraftCode] = useState('');
  const [draftPublications, setDraftPublications] = useState<PublicationInfo[]>([]);
  const [draftSnippets, setDraftSnippets] = useState<LinkedCodeSnippet[]>([]);
  const [relationshipUpdates, setRelationshipUpdates] = useState<GraphRelationshipUpdate[]>([]);
  const [addingRelationship, setAddingRelationship] = useState(false);
  const [relationshipDirection, setRelationshipDirection] = useState<'outgoing' | 'incoming'>('outgoing');
  const [relationshipPredicate, setRelationshipPredicate] = useState('rel:related_to');
  const [relationshipTarget, setRelationshipTarget] = useState<LiveGraphNode | null>(null);
  const [targetQuery, setTargetQuery] = useState('');
  const [targetResults, setTargetResults] = useState<LiveGraphNode[]>([]);
  const [targetSearching, setTargetSearching] = useState(false);
  const [targetSearchError, setTargetSearchError] = useState<string | null>(null);
  const color = node.color;
  const canEdit = loadAgentSettings().graphSource !== 'json';

  useEffect(() => {
    let cancelled = false;
    setDetail(node);
    setEditing(false);
    setSaveError(null);

    fetchGraphNodeDetail(node.id, uploadedGraphQueryParam(graphSourcePath))
      .then(nextDetail => {
        if (!cancelled) setDetail(nextDetail);
      })
      .catch(() => {
        // Keep inline node data on failure.
      });

    return () => {
      cancelled = true;
    };
  }, [node.id, graphSourcePath]);

  const display = detail ?? node;
  const publications = display.publications ?? [];
  const linkedSnippets = display.linked_code_snippets ?? [];
  const nodeById = useMemo(() => new Map(graph.nodes.map(item => [item.id, item])), [graph.nodes]);
  const incidentRelationships = useMemo(
    () => graph.edges.filter(edge => edge.source === node.id || edge.target === node.id),
    [graph.edges, node.id],
  );
  const relationshipKey = (relationship: Pick<GraphRelationshipUpdate, 'source' | 'predicate' | 'target'>) =>
    `${relationship.source}\u0000${relationship.predicate}\u0000${relationship.target}`;
  const removedRelationshipKeys = new Set(
    relationshipUpdates.filter(update => update.action === 'remove').map(relationshipKey),
  );
  const visibleRelationships = [
    ...incidentRelationships
      .filter(edge => !removedRelationshipKeys.has(relationshipKey(edge)))
      .map(edge => ({ ...edge, staged: false })),
    ...relationshipUpdates
      .filter(update => update.action === 'add')
      .map(update => ({ ...update, staged: true })),
  ];
  const editType = editing ? draftType : display.type;
  const showOwnCode = isCodeSnippetNode({ type: editType, code_snippet: editing ? draftCode : display.code_snippet });
  const typeOptions = Array.from(
    new Set([
      ...SCHEMA_NODE_CLASSES,
      ...(display.type && !(SCHEMA_NODE_CLASSES as readonly string[]).includes(display.type)
        ? [display.type]
        : []),
    ]),
  );

  function beginEdit() {
    setDraftLabel(display.label || '');
    setDraftType(display.type || 'Thing');
    setDraftDescription(display.description || '');
    setDraftCode(display.code_snippet || '');
    setDraftPublications(
      (display.publications ?? []).map(pub => ({
        ...pub,
        authors: Array.isArray(pub.authors) ? [...pub.authors] : [],
      })),
    );
    setDraftSnippets(
      (display.linked_code_snippets ?? []).map(snippet => ({
        ...snippet,
        id: snippet.id,
        label: snippet.label || '',
        function_name: snippet.function_name || '',
        code_language: snippet.code_language || '',
        code_snippet: snippet.code_snippet || '',
      })),
    );
    setRelationshipUpdates([]);
    setAddingRelationship(false);
    setRelationshipDirection('outgoing');
    setRelationshipPredicate('rel:related_to');
    setRelationshipTarget(null);
    setTargetQuery('');
    setTargetResults([]);
    setTargetSearchError(null);
    setSaveError(null);
    setEditing(true);
  }

  async function saveEdit() {
    setSaving(true);
    setSaveError(null);
    try {
      const nextType = draftType.trim() || display.type;
      const payload = {
        label: draftLabel.trim() || display.label,
        type: nextType,
        description: draftDescription,
        ...(isCodeSnippetNode({ type: nextType, code_snippet: draftCode })
          ? { code_snippet: draftCode }
          : {}),
        publications: draftPublications.map(pub => ({
          ...pub,
          authors: (pub.authors || []).map(author => String(author).trim()).filter(Boolean),
          paper_title: pub.paper_title?.trim() || undefined,
          journal: pub.journal?.trim() || undefined,
          doi: pub.doi?.trim() || undefined,
          source_paper: pub.source_paper?.trim() || undefined,
          publication_year: pub.publication_year || undefined,
        })),
        linked_code_snippets: draftSnippets.map(snippet => ({
          id: snippet.id?.startsWith('temp:') ? undefined : snippet.id,
          label: (snippet.label || '').trim() || undefined,
          function_name: (snippet.function_name || '').trim() || undefined,
          code_language: (snippet.code_language || '').trim() || undefined,
          code_snippet: snippet.code_snippet || '',
          _action: 'upsert' as const,
        })),
        relationship_updates: relationshipUpdates,
      };
      const updated = await updateGraphNode(node.id, payload);
      const refreshedGraph = await fetchLiveGraph();
      setDetail(updated);
      onNodeUpdated?.(updated, refreshedGraph);
      setEditing(false);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Failed to save properties');
    } finally {
      setSaving(false);
    }
  }

  async function searchRelationshipTargets(event: React.FormEvent) {
    event.preventDefault();
    const query = targetQuery.trim();
    if (!query || targetSearching) return;
    setTargetSearching(true);
    setTargetSearchError(null);
    try {
      const response = await searchGraphNodes(query, 8);
      setTargetResults(
        response.results.map(result => result.node).filter(result => result.id !== node.id),
      );
    } catch (error) {
      setTargetResults([]);
      setTargetSearchError(error instanceof Error ? error.message : 'Node search failed');
    } finally {
      setTargetSearching(false);
    }
  }

  function stageRelationship() {
    if (!relationshipTarget) return;
    const predicate = normalizeRelationshipPredicate(relationshipPredicate);
    if (!predicate) {
      setTargetSearchError('Enter a valid predicate such as rel:related_to');
      return;
    }
    const source = relationshipDirection === 'outgoing' ? node.id : relationshipTarget.id;
    const target = relationshipDirection === 'outgoing' ? relationshipTarget.id : node.id;
    const next: GraphRelationshipUpdate = { action: 'add', source, predicate, target };
    const key = relationshipKey(next);
    const existing = incidentRelationships.some(edge => relationshipKey(edge) === key);
    if (existing && removedRelationshipKeys.has(key)) {
      setRelationshipUpdates(prev => prev.filter(update => relationshipKey(update) !== key));
      setAddingRelationship(false);
      setRelationshipTarget(null);
      setTargetQuery('');
      setTargetResults([]);
      setTargetSearchError(null);
      return;
    }
    if (visibleRelationships.some(edge => relationshipKey(edge) === key)) {
      setTargetSearchError('That relationship already exists.');
      return;
    }
    setRelationshipUpdates(prev => [
      ...prev.filter(update => !(update.action === 'remove' && relationshipKey(update) === key)),
      next,
    ]);
    setAddingRelationship(false);
    setRelationshipTarget(null);
    setTargetQuery('');
    setTargetResults([]);
    setTargetSearchError(null);
  }

  function stageRelationshipRemoval(
    relationship: Pick<GraphRelationshipUpdate, 'source' | 'predicate' | 'target'> & { staged?: boolean },
  ) {
    const key = relationshipKey(relationship);
    if (relationship.staged) {
      setRelationshipUpdates(prev => prev.filter(update => relationshipKey(update) !== key));
      return;
    }
    setRelationshipUpdates(prev => [
      ...prev.filter(update => relationshipKey(update) !== key),
      { action: 'remove', source: relationship.source, predicate: relationship.predicate, target: relationship.target },
    ]);
  }

  const fieldClass =
    'w-full rounded border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-800 outline-none focus:border-sky-400';
  const labelClass = 'mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500';

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-4 py-3">
        <div
          className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
          style={{ background: editing ? getNodeColor(draftType || display.type) : color }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {editing ? (
              <select
                className={`${fieldClass} w-auto min-w-[10rem] text-xs font-medium`}
                value={draftType}
                onChange={event => setDraftType(event.target.value)}
                aria-label="Node type"
              >
                {typeOptions.map(option => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <div className="text-xs font-medium" style={{ color: getNodeColor(display.type || node.type) }}>
                {display.type || node.type || 'Node'}
              </div>
            )}
            <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-600">
              Pinned
            </span>
          </div>
          {editing ? (
            <input
              className={`${fieldClass} mt-1 font-semibold`}
              value={draftLabel}
              onChange={event => setDraftLabel(event.target.value)}
              aria-label="Node label"
            />
          ) : (
            <div className="mt-0.5 text-sm font-semibold text-slate-800">{display.label}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {canEdit && !editing && (
            <button
              type="button"
              aria-label="Edit properties"
              title="Edit Properties"
              onClick={beginEdit}
              className="inline-flex h-9 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-4 text-sm font-medium text-slate-600 hover:text-slate-800"
            >
              <Pencil size={14} />
              Edit
            </button>
          )}
          <button
            type="button"
            aria-label="Close node details"
            title="Close"
            onClick={onClose}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-slate-200 bg-white text-slate-400 hover:text-slate-600"
          >
            <X size={13} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {editing ? (
          <div className="space-y-4">
            <div>
              <label className={labelClass}>Description</label>
              <textarea
                className={`${fieldClass} min-h-[88px] resize-y`}
                value={draftDescription}
                onChange={event => setDraftDescription(event.target.value)}
              />
            </div>
            {showOwnCode && (
              <div>
                <label className={labelClass}>Code snippet</label>
                <textarea
                  className={`${fieldClass} min-h-[120px] resize-y font-mono text-xs`}
                  value={draftCode}
                  onChange={event => setDraftCode(event.target.value)}
                />
              </div>
            )}
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <label className={labelClass + ' mb-0'}>Publications</label>
                <button
                  type="button"
                  onClick={() => setDraftPublications(prev => [...prev, emptyPublication()])}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-600 hover:text-sky-700"
                >
                  <Plus size={12} />
                  Add
                </button>
              </div>
              {draftPublications.length === 0 && (
                <p className="text-xs text-slate-400">No publications</p>
              )}
              {draftPublications.map((pub, index) => (
                <div key={`pub-${index}`} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/80 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[11px] font-medium text-slate-500">Publication {index + 1}</span>
                    <button
                      type="button"
                      aria-label={`Remove publication ${index + 1}`}
                      onClick={() => setDraftPublications(prev => prev.filter((_, i) => i !== index))}
                      className="text-slate-400 hover:text-rose-500"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <input
                    className={fieldClass}
                    placeholder="Title"
                    value={pub.paper_title || ''}
                    onChange={event => {
                      const value = event.target.value;
                      setDraftPublications(prev => prev.map((item, i) => (i === index ? { ...item, paper_title: value } : item)));
                    }}
                  />
                  <input
                    className={fieldClass}
                    placeholder="Authors (comma-separated)"
                    value={(pub.authors || []).join(', ')}
                    onChange={event => {
                      const authors = event.target.value.split(',').map(part => part.trim()).filter(Boolean);
                      setDraftPublications(prev => prev.map((item, i) => (i === index ? { ...item, authors } : item)));
                    }}
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      className={fieldClass}
                      placeholder="Year"
                      value={pub.publication_year ?? ''}
                      onChange={event => {
                        const raw = event.target.value.trim();
                        const year = raw ? Number(raw) : undefined;
                        setDraftPublications(prev => prev.map((item, i) => (
                          i === index ? { ...item, publication_year: Number.isFinite(year) ? year : undefined } : item
                        )));
                      }}
                    />
                    <input
                      className={fieldClass}
                      placeholder="Journal"
                      value={pub.journal || ''}
                      onChange={event => {
                        const value = event.target.value;
                        setDraftPublications(prev => prev.map((item, i) => (i === index ? { ...item, journal: value } : item)));
                      }}
                    />
                  </div>
                  <input
                    className={fieldClass}
                    placeholder="DOI"
                    value={pub.doi || ''}
                    onChange={event => {
                      const value = event.target.value;
                      setDraftPublications(prev => prev.map((item, i) => (i === index ? { ...item, doi: value } : item)));
                    }}
                  />
                  <input
                    className={fieldClass}
                    placeholder="Source paper"
                    value={pub.source_paper || ''}
                    onChange={event => {
                      const value = event.target.value;
                      setDraftPublications(prev => prev.map((item, i) => (i === index ? { ...item, source_paper: value } : item)));
                    }}
                  />
                </div>
              ))}
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <label className={labelClass + ' mb-0'}>Linked code snippets</label>
                <button
                  type="button"
                  onClick={() => setDraftSnippets(prev => [...prev, emptyLinkedSnippet()])}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-600 hover:text-sky-700"
                >
                  <Plus size={12} />
                  Add
                </button>
              </div>
              {draftSnippets.length === 0 && (
                <p className="text-xs text-slate-400">No linked snippets</p>
              )}
              {draftSnippets.map((snippet, index) => (
                <div key={snippet.id || `snippet-${index}`} className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/80 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[11px] font-medium text-slate-500">Snippet {index + 1}</span>
                    <button
                      type="button"
                      aria-label={`Remove linked snippet ${index + 1}`}
                      onClick={() => setDraftSnippets(prev => prev.filter((_, i) => i !== index))}
                      className="text-slate-400 hover:text-rose-500"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <input
                    className={fieldClass}
                    placeholder="Label"
                    value={snippet.label || ''}
                    onChange={event => {
                      const value = event.target.value;
                      setDraftSnippets(prev => prev.map((item, i) => (i === index ? { ...item, label: value } : item)));
                    }}
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      className={fieldClass}
                      placeholder="Function name"
                      value={snippet.function_name || ''}
                      onChange={event => {
                        const value = event.target.value;
                        setDraftSnippets(prev => prev.map((item, i) => (i === index ? { ...item, function_name: value } : item)));
                      }}
                    />
                    <input
                      className={fieldClass}
                      placeholder="Language"
                      value={snippet.code_language || ''}
                      onChange={event => {
                        const value = event.target.value;
                        setDraftSnippets(prev => prev.map((item, i) => (i === index ? { ...item, code_language: value } : item)));
                      }}
                    />
                  </div>
                  <textarea
                    className={`${fieldClass} min-h-[100px] resize-y font-mono text-xs`}
                    placeholder="Code"
                    value={snippet.code_snippet || ''}
                    onChange={event => {
                      const value = event.target.value;
                      setDraftSnippets(prev => prev.map((item, i) => (i === index ? { ...item, code_snippet: value } : item)));
                    }}
                  />
                </div>
              ))}
            </div>
            <div className="space-y-2 border-t border-slate-100 pt-4">
              <div className="flex items-center justify-between gap-2">
                <label className={labelClass + ' mb-0'}>Relationships</label>
                <button
                  type="button"
                  onClick={() => {
                    setAddingRelationship(value => !value);
                    setTargetSearchError(null);
                  }}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-600 hover:text-sky-700"
                >
                  <Plus size={12} />
                  Add
                </button>
              </div>
              {visibleRelationships.length === 0 && (
                <p className="text-xs text-slate-400">No relationships</p>
              )}
              {visibleRelationships.map(relationship => {
                const outgoing = relationship.source === node.id;
                const otherId = outgoing ? relationship.target : relationship.source;
                const other = nodeById.get(otherId);
                return (
                  <div
                    key={`${relationshipKey(relationship)}:${relationship.staged ? 'new' : 'existing'}`}
                    className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2"
                  >
                    <span className="min-w-0 flex-1 text-xs text-slate-600">
                      <span className="font-medium text-slate-700">{outgoing ? 'This node' : other?.label || otherId}</span>
                      <span className="mx-1.5 text-slate-400">→</span>
                      <span className="font-medium text-slate-700">{outgoing ? other?.label || otherId : 'This node'}</span>
                      <span className="mt-0.5 block truncate text-[10px] text-slate-400">{relationship.predicate}</span>
                    </span>
                    {relationship.staged && (
                      <span className="text-[9px] font-medium uppercase tracking-wide text-sky-600">New</span>
                    )}
                    <button
                      type="button"
                      aria-label={`Remove relationship ${relationship.predicate}`}
                      onClick={() => stageRelationshipRemoval(relationship)}
                      className="text-slate-400 hover:text-rose-500"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })}
              {addingRelationship && (
                <div className="space-y-2 rounded-lg border border-sky-200 bg-sky-50/40 p-3">
                  <select
                    value={relationshipDirection}
                    onChange={event => setRelationshipDirection(event.target.value as 'outgoing' | 'incoming')}
                    className={fieldClass}
                    aria-label="Relationship direction"
                  >
                    <option value="outgoing">This node → target</option>
                    <option value="incoming">Target → this node</option>
                  </select>
                  <div>
                    <input
                      list="kg-relationship-predicates"
                      value={relationshipPredicate}
                      onChange={event => setRelationshipPredicate(event.target.value)}
                      placeholder="rel:related_to"
                      className={fieldClass}
                      aria-label="Relationship predicate"
                    />
                    <datalist id="kg-relationship-predicates">
                      {SCHEMA_RELATIONSHIP_PREDICATES.map(predicate => (
                        <option key={predicate} value={predicate} />
                      ))}
                    </datalist>
                  </div>
                  {relationshipTarget ? (
                    <div className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2.5 py-2">
                      <span className="min-w-0 text-xs text-slate-700">
                        <span className="block truncate font-medium">{relationshipTarget.label}</span>
                        <span className="block text-[10px] text-slate-400">{relationshipTarget.type}</span>
                      </span>
                      <button
                        type="button"
                        aria-label="Clear relationship target"
                        onClick={() => setRelationshipTarget(null)}
                        className="text-slate-400 hover:text-slate-600"
                      >
                        <X size={13} />
                      </button>
                    </div>
                  ) : (
                    <>
                      <form onSubmit={searchRelationshipTargets} className="flex gap-2">
                        <input
                          value={targetQuery}
                          onChange={event => setTargetQuery(event.target.value)}
                          placeholder="Search for a target node…"
                          className={fieldClass}
                        />
                        <button
                          type="submit"
                          disabled={!targetQuery.trim() || targetSearching}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded bg-sky-500 text-white disabled:opacity-40"
                        >
                          {targetSearching ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                        </button>
                      </form>
                      {targetResults.length > 0 && (
                        <div className="max-h-40 space-y-1 overflow-y-auto rounded border border-slate-200 bg-white p-1">
                          {targetResults.map(result => (
                            <button
                              type="button"
                              key={result.id}
                              onClick={() => {
                                setRelationshipTarget(result);
                                setTargetSearchError(null);
                              }}
                              className="block w-full rounded px-2 py-1.5 text-left hover:bg-slate-50"
                            >
                              <span className="block truncate text-xs font-medium text-slate-700">{result.label}</span>
                              <span className="block text-[10px] text-slate-400">{result.type}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                  {targetSearchError && <p className="text-xs text-rose-600">{targetSearchError}</p>}
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setAddingRelationship(false)}
                      className="rounded px-2.5 py-1.5 text-xs text-slate-500 hover:text-slate-700"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      disabled={!relationshipTarget}
                      onClick={stageRelationship}
                      className="rounded bg-sky-500 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-40"
                    >
                      Stage relationship
                    </button>
                  </div>
                </div>
              )}
            </div>
            {saveError && (
              <p className="text-xs text-rose-600">{saveError}</p>
            )}
            <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-3">
              <button
                type="button"
                disabled={saving}
                onClick={() => {
                  setEditing(false);
                  setSaveError(null);
                }}
                className="rounded border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:text-slate-800 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() => void saveEdit()}
                className="rounded bg-sky-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        ) : (
          <>
            {(display.description || node.id || publications.length > 0 || display.code_snippet) && (
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
                {(display.description || node.id || display.code_snippet) && (
                  <div className="px-4 py-3.5 text-sm leading-relaxed text-slate-700">
                    {(display.description || node.id) && (
                      <p className="whitespace-pre-wrap font-semibold text-slate-700">
                        {display.description || node.id}
                      </p>
                    )}
                    {display.code_snippet && (
                      <div className={display.description || node.id ? 'mt-4 space-y-2' : 'space-y-2'}>
                        {(display.function_name || display.code_language) && (
                          <div className="text-xs text-slate-500">
                            {[display.function_name, display.code_language].filter(Boolean).join(' · ')}
                          </div>
                        )}
                        <CodeBlock content={display.code_snippet} />
                      </div>
                    )}
                  </div>
                )}
                {publications.length > 0 && (
                  <div className="border-t border-slate-200 bg-white/60 px-4 py-3">
                    <p className="mb-8 text-sm font-bold text-slate-800">
                      Relevant Publications and Sources:
                    </p>
                    <PublicationList
                      publications={publications}
                      intro={null}
                      collapseLimit={3}
                      className="mt-0"
                    />
                  </div>
                )}
              </div>
            )}
            {linkedSnippets.length > 0 && (
              <div className="mt-4 space-y-4">
                <p className="text-xs leading-relaxed text-slate-700">Related code snippets:</p>
                {linkedSnippets.map((snippet: LinkedCodeSnippet) => {
                  const snippetPublications = (snippet.publications ?? []) as PublicationInfo[];
                  return (
                    <div key={snippet.id} className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
                      <div className="space-y-2 px-4 py-3.5">
                        <div className="text-xs font-medium text-slate-800">
                          {snippet.label || snippet.function_name || snippet.id}
                        </div>
                        {(snippet.function_name || snippet.code_language) && (
                          <div className="text-xs text-slate-500">
                            {[snippet.function_name, snippet.code_language].filter(Boolean).join(' · ')}
                          </div>
                        )}
                        <CodeBlock content={snippet.code_snippet} />
                      </div>
                      {snippetPublications.length > 0 && (
                        <div className="border-t border-slate-200 bg-white/60 px-4 py-3">
                          <p className="mb-8 text-sm font-bold text-slate-800">
                            Relevant Publications and Sources:
                          </p>
                          <PublicationList
                            publications={snippetPublications}
                            intro={null}
                            collapseLimit={3}
                            className="mt-0"
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            {incidentRelationships.length > 0 && (
              <div className="mt-4 space-y-2">
                <p className="text-xs font-medium text-slate-700">Relationships</p>
                {incidentRelationships.map((relationship, index) => {
                  const outgoing = relationship.source === node.id;
                  const otherId = outgoing ? relationship.target : relationship.source;
                  const other = nodeById.get(otherId);
                  return (
                    <div
                      key={`${relationshipKey(relationship)}:${index}`}
                      className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-600"
                    >
                      <span className="font-medium text-slate-700">{outgoing ? 'This node' : other?.label || otherId}</span>
                      <span className="mx-1.5 text-slate-400">→</span>
                      <span className="font-medium text-slate-700">{outgoing ? other?.label || otherId : 'This node'}</span>
                      <span className="mt-0.5 block text-[10px] text-slate-400">{relationship.predicate}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function NodeHoverPreview({ node }: { node: LayoutNode }) {
  const color = node.color;
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-dashed border-slate-200 bg-white/90 shadow-sm">
      <div className="flex shrink-0 items-start gap-3 border-b border-slate-100 px-4 py-3">
        <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium" style={{ color }}>
            {node.type || 'Node'}
          </div>
          <div className="mt-0.5 text-sm font-semibold text-slate-800">{node.label}</div>
        </div>
        <span className="shrink-0 text-[10px] text-slate-400">Click to pin</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {(node.description || node.id) && (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
            <div className="px-4 py-3.5 text-sm leading-relaxed text-slate-700">
              <p className="whitespace-pre-wrap font-semibold text-slate-700">
                {node.description || node.id}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

interface HoverPopupState {
  target: Exclude<KGHoverTarget, null>;
  anchorX: number;
  anchorY: number;
  anchorRadius: number;
  edgeKey?: string;
}

function offsetArrowPoints(points: string, dx: number, dy: number): string {
  return points
    .split(/\s+/)
    .map(pair => {
      const [x, y] = pair.split(',').map(Number);
      return `${x - dx},${y - dy}`;
    })
    .join(' ');
}

interface DragState {
  pointerId: number;
  mouseX: number;
  mouseY: number;
  viewBox: ViewBox;
}

function mouseToContainer(
  e: { clientX: number; clientY: number },
  container: DOMRect,
): { x: number; y: number } {
  return { x: e.clientX - container.left, y: e.clientY - container.top };
}

function nodeToContainer(
  node: LayoutNode,
  viewBox: ViewBox,
  svgRect: DOMRect,
  containerRect: DOMRect,
): { x: number; y: number; radius: number } {
  const x =
    svgRect.left - containerRect.left + ((node.x - viewBox.x) / viewBox.width) * svgRect.width;
  const y =
    svgRect.top - containerRect.top + ((node.y - viewBox.y) / viewBox.height) * svgRect.height;
  const scale = svgRect.width / viewBox.width;
  return { x, y, radius: (R + 8) * scale };
}

function choosePopupPosition(
  anchorX: number,
  anchorY: number,
  anchorRadius: number,
  popupWidth: number,
  popupHeight: number,
  containerWidth: number,
  containerHeight: number,
  nodeScreens: { x: number; y: number; radius: number }[],
): { x: number; y: number } {
  const gap = 10;
  const pad = 8;
  const candidates = [
    { x: anchorX - popupWidth / 2, y: anchorY - anchorRadius - gap - popupHeight },
    { x: anchorX - popupWidth / 2, y: anchorY + anchorRadius + gap },
    { x: anchorX + anchorRadius + gap, y: anchorY - popupHeight / 2 },
    { x: anchorX - popupWidth - anchorRadius - gap, y: anchorY - popupHeight / 2 },
    { x: anchorX + gap, y: anchorY + gap },
    { x: anchorX - popupWidth - gap, y: anchorY - popupHeight - gap },
  ];

  function score(x: number, y: number) {
    let penalty = 0;
    const left = x;
    const top = y;
    const right = x + popupWidth;
    const bottom = y + popupHeight;

    if (left < pad) penalty += (pad - left) * 20;
    if (top < pad) penalty += (pad - top) * 20;
    if (right > containerWidth - pad) penalty += (right - containerWidth + pad) * 20;
    if (bottom > containerHeight - pad) penalty += (bottom - containerHeight + pad) * 20;

    for (const node of nodeScreens) {
      const nearestX = clamp(node.x, left, right);
      const nearestY = clamp(node.y, top, bottom);
      const dist = Math.hypot(nearestX - node.x, nearestY - node.y);
      if (dist < node.radius) {
        penalty += (node.radius - dist) * 120;
      }
    }

    return penalty;
  }

  let best = candidates[0];
  let bestScore = Infinity;
  for (const candidate of candidates) {
    const nextScore = score(candidate.x, candidate.y);
    if (nextScore < bestScore) {
      bestScore = nextScore;
      best = candidate;
    }
  }

  return {
    x: clamp(best.x, pad, Math.max(pad, containerWidth - popupWidth - pad)),
    y: clamp(best.y, pad, Math.max(pad, containerHeight - popupHeight - pad)),
  };
}

const CITED_PULSE_MS = 2200;
const CITED_SCALE = 1.35;

export function GraphMockup({
  graph,
  highlightedNodeIds,
  citedNodeIds = [],
  citationAnimationKey = '',
  onNodeUpdated,
  isKgViewer = false,
  kgViewerNodeLimit = 100,
  onToggleKgViewer,
  onKgViewerNodeLimitChange,
}: GraphMockupProps) {
  const [hoverPopup, setHoverPopup] = useState<HoverPopupState | null>(null);
  const [popupPos, setPopupPos] = useState<{ x: number; y: number } | null>(null);
  const [hoveredNode, setHoveredNode] = useState<LayoutNode | null>(null);
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [searchedNodeId, setSearchedNodeId] = useState<string | null>(null);
  const [viewBox, setViewBox] = useState<ViewBox>(fullViewBox(W, H));
  const [drag, setDrag] = useState<DragState | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  const highlightedSignature = highlightedNodeIds.join('|');
  const highlighted = useMemo(
    () => new Set(searchedNodeId ? [searchedNodeId] : highlightedNodeIds),
    [highlightedNodeIds, searchedNodeId],
  );
  const cited = useMemo(() => new Set(citedNodeIds), [citedNodeIds]);
  const displayGraph = useMemo<GraphPayload>(() => {
    if (searchedNodeId) {
      const visibleNodeIds = new Set(oneHopNodeIds(graph, searchedNodeId));
      return {
        nodes: graph.nodes.filter(
          node => visibleNodeIds.has(node.id) && !isUnknownNodeCategory(node.type),
        ),
        edges: graph.edges.filter(
          edge => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
        ),
        source_path: graph.source_path,
      };
    }
    if (isKgViewer) {
      if (kgViewerNodeLimit === 'all') return graph;
      return connectedGraphSubset(
        graph,
        kgViewerNodeLimit,
      );
    }
    if (highlightedNodeIds.length === 0) {
      return { nodes: [], edges: [], source_path: graph.source_path };
    }
    // Drop Unknown stubs from the top-k set — they are unresolved placeholders.
    const nodes = graph.nodes.filter(
      node => highlighted.has(node.id) && !isUnknownNodeCategory(node.type),
    );
    const visibleNodeIds = new Set(nodes.map(node => node.id));
    return {
      nodes,
      edges: graph.edges.filter(edge => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)),
      source_path: graph.source_path,
    };
  }, [graph, highlighted, highlightedNodeIds.length, isKgViewer, kgViewerNodeLimit, searchedNodeId]);
  const layout = useMemo(() => layoutGraph(displayGraph), [displayGraph]);
  const nodes = layout.nodes;
  const nodeMap = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
  const renderedNodes = useMemo(
    // Keep normal-view citation nodes mounted so panning cannot restart their
    // CSS animations at different phases. Viewer mode still culls everything.
    () => nodes.filter(node => nodeIsInView(node, viewBox) || (!isKgViewer && cited.has(node.id))),
    [nodes, viewBox, isKgViewer, cited],
  );
  const visibleEdges = useMemo(
    () => displayGraph.edges.filter(edge => {
      const source = nodeMap.get(edge.source);
      const target = nodeMap.get(edge.target);
      return Boolean(source && target && edgeIsInView(source, target, viewBox));
    }),
    [displayGraph.edges, nodeMap, viewBox],
  );
  const hasVisibleNodes = nodes.length > 0;
  // Replays the populate animation whenever the visible node set changes.
  const revealKey = useMemo(() => nodeSetKey(nodes), [nodes]);

  useEffect(() => {
    setHoverPopup(null);
    setPopupPos(null);
    setHoveredNode(null);
    setSelectedNode(null);
  }, [revealKey]);

  useEffect(() => {
    setViewBox(fullViewBox(layout.width, layout.height));
  }, [layout.width, layout.height, displayGraph.source_path]);

  useEffect(() => {
    setSearchedNodeId(null);
  }, [highlightedSignature, graph.source_path]);

  useEffect(() => {
    if (nodes.length === 0) return;
    setViewBox(fitNodesViewBox(nodes, layout.width, layout.height));
  }, [nodes, layout.width, layout.height]);

  useEffect(() => {
    setHoverPopup(prev =>
      prev?.target.kind === 'edge' ? prev : null,
    );
    setHoveredNode(prev => (prev && nodeMap.has(prev.id) ? nodeMap.get(prev.id) ?? null : null));
    setSelectedNode(prev => prev && nodeMap.has(prev.id) ? nodeMap.get(prev.id) ?? null : null);
  }, [nodeMap]);

  useEffect(() => {
    if (!searchedNodeId) return;
    const searchedNode = nodeMap.get(searchedNodeId);
    if (searchedNode) setSelectedNode(searchedNode);
  }, [nodeMap, searchedNodeId]);

  useLayoutEffect(() => {
    if (!hoverPopup || !popupRef.current || !containerRef.current || !svgRef.current) {
      setPopupPos(null);
      return;
    }

    const containerRect = containerRef.current.getBoundingClientRect();
    const svgRect = svgRef.current.getBoundingClientRect();
    const popupRect = popupRef.current.getBoundingClientRect();
    const nodeScreens = renderedNodes.map(node => nodeToContainer(node, viewBox, svgRect, containerRect));

    let anchorX = hoverPopup.anchorX;
    let anchorY = hoverPopup.anchorY;
    let anchorRadius = hoverPopup.anchorRadius;

    setPopupPos(
      choosePopupPosition(
        anchorX,
        anchorY,
        anchorRadius,
        popupRect.width,
        popupRect.height,
        containerRect.width,
        containerRect.height,
        nodeScreens,
      ),
    );
  }, [hoverPopup, renderedNodes, viewBox, nodeMap]);

  function zoom(multiplier: number, origin?: { x: number; y: number }) {
    setViewBox(current => {
      const nextWidth = current.width * multiplier;
      const nextHeight = current.height * multiplier;
      const originX = origin?.x ?? current.x + current.width / 2;
      const originY = origin?.y ?? current.y + current.height / 2;
      const ratioX = (originX - current.x) / current.width;
      const ratioY = (originY - current.y) / current.height;
      return clampViewBox(
        {
          x: originX - nextWidth * ratioX,
          y: originY - nextHeight * ratioY,
          width: nextWidth,
          height: nextHeight,
        },
        layout.width,
        layout.height,
      );
    });
  }

  function resetView() {
    if (nodes.length > 0) {
      setViewBox(fitNodesViewBox(nodes, layout.width, layout.height));
      return;
    }
    setViewBox(fullViewBox(layout.width, layout.height));
  }

  function handleNodeEnter(node: LayoutNode) {
    setHoveredNode(node);
  }

  function handleNodeLeave() {
    setHoveredNode(null);
  }

  function handleEdgeHover(
    e: React.MouseEvent<SVGGElement>,
    src: LayoutNode,
    tgt: LayoutNode,
    predicate: string,
    edgeKey: string,
  ) {
    const container = containerRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const anchor = mouseToContainer(e, containerRect);
    setHoverPopup({
      target: {
        kind: 'edge',
        sourceLabel: src.label,
        targetLabel: tgt.label,
        predicate: predicate || 'rel:related_to',
      },
      anchorX: anchor.x,
      anchorY: anchor.y,
      anchorRadius: 6,
      edgeKey,
    });
  }

  function handleEdgeLeave() {
    setHoverPopup(prev => (prev?.target.kind === 'edge' ? null : prev));
    setPopupPos(null);
  }

  function handlePointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setHoverPopup(null);
    setPopupPos(null);
    setHoveredNode(null);
    setDrag({ pointerId: e.pointerId, mouseX: e.clientX, mouseY: e.clientY, viewBox });
  }

  function handlePointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag || drag.pointerId !== e.pointerId || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const dx = (e.clientX - drag.mouseX) * (drag.viewBox.width / rect.width);
    const dy = (e.clientY - drag.mouseY) * (drag.viewBox.height / rect.height);
    setViewBox(clampViewBox({
      ...drag.viewBox,
      x: drag.viewBox.x - dx,
      y: drag.viewBox.y - dy,
    }, layout.width, layout.height));
  }

  function handlePointerEnd(e: React.PointerEvent<SVGSVGElement>) {
    if (drag?.pointerId === e.pointerId && e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setDrag(null);
  }

  function handleWheel(e: React.WheelEvent<SVGSVGElement>) {
    e.preventDefault();
    if (!svgRef.current) {
      zoom(e.deltaY > 0 ? 1.15 : 0.85);
      return;
    }
    const rect = svgRef.current.getBoundingClientRect();
    const origin = {
      x: viewBox.x + ((e.clientX - rect.left) / rect.width) * viewBox.width,
      y: viewBox.y + ((e.clientY - rect.top) / rect.height) * viewBox.height,
    };
    zoom(e.deltaY > 0 ? 1.15 : 0.85, origin);
  }

  const kgViewerButton = onToggleKgViewer ? (
    <button
      type="button"
      aria-label={isKgViewer ? 'Agent KG Viewer' : 'KG Viewer'}
      title={isKgViewer ? 'Agent KG Viewer' : 'KG Viewer'}
      onClick={onToggleKgViewer}
      className={`inline-flex h-9 items-center rounded-md border px-3 text-sm font-medium transition ${
        isKgViewer
          ? 'border-sky-300 bg-sky-50 text-sky-700 hover:bg-sky-100'
          : 'border-slate-200 bg-white text-slate-500 hover:text-slate-700'
      }`}
    >
      {isKgViewer ? 'Agent KG Viewer' : 'KG Viewer'}
    </button>
  ) : null;

  const kgViewerLimitControl = isKgViewer && onKgViewerNodeLimitChange ? (
    <label className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-500">
      Nodes
      <select
        aria-label="Nodes to render"
        title="Nodes to render"
        value={kgViewerNodeLimit}
        onChange={event => onKgViewerNodeLimitChange(
          event.target.value === 'all' ? 'all' : Number(event.target.value),
        )}
        className="bg-transparent text-sm font-medium text-slate-700 outline-none"
      >
        <option value="all">All</option>
        {Array.from({ length: 10 }, (_, index) => (index + 1) * 10).map(limit => (
          <option key={limit} value={limit}>{limit}</option>
        ))}
      </select>
    </label>
  ) : null;

  if (!hasVisibleNodes) {
    return (
      <div className="relative flex flex-1 min-w-0 flex-col" style={{ background: '#ffffff' }}>
        <svg className="absolute inset-0 w-full h-full pointer-events-none" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern id="kg-grid-empty" width="40" height="40" patternUnits="userSpaceOnUse">
              <circle cx="0" cy="0" r="1" fill="rgba(0,0,0,0.06)" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#kg-grid-empty)" />
        </svg>
        <div
          className="relative z-20 flex items-center justify-end gap-3 px-4 py-2.5 shrink-0"
          style={{ borderBottom: '1px solid rgba(0,0,0,0.07)', background: 'rgba(255,255,255,0.9)' }}
        >
          {kgViewerButton}
          <NodeSearchControl
            activeNodeId={searchedNodeId}
            onSelect={setSearchedNodeId}
          />
          {kgViewerLimitControl}
        </div>
        <div className="relative z-10 flex flex-1 items-center justify-center">
          <AsciiOrb size={400} />
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex-1 min-w-0 flex flex-col" style={{ background: '#ffffff' }}>
      <svg className="absolute inset-0 w-full h-full pointer-events-none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="kg-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <circle cx="0" cy="0" r="1" fill="rgba(0,0,0,0.06)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#kg-grid)" />
      </svg>

      <div
        className="relative z-20 flex items-center justify-end gap-3 px-4 py-2.5 shrink-0"
        style={{ borderBottom: '1px solid rgba(0,0,0,0.07)', background: 'rgba(255,255,255,0.9)' }}
      >
        <div className="flex items-center gap-2">
          {hasVisibleNodes && (
            <div className="flex items-center gap-2">
              <div
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: '#0ea5e9', boxShadow: '0 0 6px #0ea5e9' }}
              />
              <span className="text-xs" style={{ color: 'rgba(0,0,0,0.45)' }}>
                <span style={{ color: '#0ea5e9' }}>{nodes.length}</span> nodes
              </span>
            </div>
          )}
          {kgViewerButton}
          <NodeSearchControl
            activeNodeId={searchedNodeId}
            onSelect={setSearchedNodeId}
          />
          {kgViewerLimitControl}
          <button
            type="button"
            aria-label="Zoom out"
            title="Zoom out"
            onClick={() => zoom(1.2)}
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:text-slate-700"
          >
            <ZoomOut size={14} />
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            title="Zoom in"
            onClick={() => zoom(0.82)}
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:text-slate-700"
          >
            <ZoomIn size={14} />
          </button>
          <button
            type="button"
            aria-label={isKgViewer ? 'Focus viewer nodes' : 'Focus retrieved nodes'}
            title={isKgViewer ? 'Focus viewer nodes' : 'Focus retrieved nodes'}
            onClick={resetView}
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:text-slate-700"
          >
            {hasVisibleNodes ? <Crosshair size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      <div className="relative z-10 min-h-0 flex-1">
        <ResizablePanelGroup direction="vertical">
          <ResizablePanel defaultSize={45} minSize={30}>
            <div ref={containerRef} className="relative h-full min-h-0 overflow-hidden">
              <svg
              ref={svgRef}
              viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
              className="absolute inset-0 w-full h-full"
              style={{ display: 'block', cursor: drag ? 'grabbing' : 'grab', touchAction: 'none', userSelect: 'none' }}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerEnd}
              onPointerCancel={handlePointerEnd}
              onWheel={handleWheel}
            >
              <defs>
                <filter id="kg-edge-glow-filter" x="-120%" y="-120%" width="340%" height="340%">
                  <feGaussianBlur in="SourceGraphic" stdDeviation="2.8" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              {visibleEdges.map((edge, i) => {
                const src = nodeMap.get(edge.source);
                const tgt = nodeMap.get(edge.target);
                if (!src || !tgt) return null;

                const srcHl = highlighted.has(src.id);
                const tgtHl = highlighted.has(tgt.id);
                const srcCited = cited.has(src.id);
                const tgtCited = cited.has(tgt.id);
                const isCitedEdge = srcCited || tgtCited;
                const bothHl = srcHl && tgtHl;
                const connectedHl = srcHl || tgtHl;
                const color = src.color;
                const geom = directedEdgeGeometry(src, tgt);
                const stroke = isCitedEdge
                  ? 'rgba(14,165,233,0.72)'
                  : bothHl
                    ? color
                    : connectedHl
                      ? 'rgba(14,165,233,0.22)'
                      : 'rgba(0,0,0,0.09)';
                const arrowFill = isCitedEdge
                  ? 'rgba(14,165,233,0.85)'
                  : bothHl
                    ? color
                    : connectedHl
                      ? 'rgba(14,165,233,0.55)'
                      : 'rgba(0,0,0,0.35)';
                // Edges draw out after the nodes have populated.
                const edgeDelay = nodes.length <= 100 ? nodes.length * 35 + i * 20 : 0;
                const edgeKey = `${edge.source}-${edge.target}-${edge.predicate}-${i}`;
                const isEdgeHovered = hoverPopup?.edgeKey === edgeKey;
                const midX = (src.x + tgt.x) / 2;
                const midY = (src.y + tgt.y) / 2;
                const relArrow = offsetArrowPoints(geom.arrowPoints, midX, midY);

                return (
                  <g
                    key={`${revealKey}:edge:${edgeKey}`}
                    onMouseEnter={e => handleEdgeHover(e, src, tgt, edge.predicate, edgeKey)}
                    onMouseMove={e => handleEdgeHover(e, src, tgt, edge.predicate, edgeKey)}
                    onMouseLeave={handleEdgeLeave}
                  >
                    <line
                      x1={src.x} y1={src.y}
                      x2={tgt.x} y2={tgt.y}
                      stroke="transparent"
                      strokeWidth={8}
                      style={{ cursor: 'pointer' }}
                    />
                    <g transform={`translate(${midX}, ${midY})`}>
                      <g
                        className={`kg-edge-scale${isEdgeHovered && !isCitedEdge ? ' kg-edge-hovered' : ''}${isCitedEdge ? ' kg-edge-cited' : ''}`}
                      >
                        {isCitedEdge && (
                          <line
                            key={`glow-${citationAnimationKey}-${edgeKey}`}
                            className="kg-edge-cited-glow"
                            x1={geom.x1 - midX} y1={geom.y1 - midY}
                            x2={geom.x2 - midX} y2={geom.y2 - midY}
                            stroke="rgba(56,189,248,0.75)"
                            strokeWidth={bothHl ? 5 : 4}
                            strokeLinecap="round"
                            filter="url(#kg-edge-glow-filter)"
                            style={{ pointerEvents: 'none' }}
                          />
                        )}
                        <line
                          className="kg-edge-in"
                          x1={geom.x1 - midX} y1={geom.y1 - midY}
                          x2={geom.x2 - midX} y2={geom.y2 - midY}
                          stroke={stroke}
                          strokeWidth={isCitedEdge ? 1.8 : bothHl ? 1.5 : connectedHl ? 1.05 : 0.65}
                          strokeOpacity={bothHl || isCitedEdge ? 0.85 : 1}
                          style={{
                            pointerEvents: 'none',
                            ['--edge-len' as string]: `${geom.length}`,
                            strokeDasharray: geom.length,
                            strokeDashoffset: geom.length,
                            animationDelay: `${edgeDelay}ms`,
                          }}
                        />
                        <polygon
                          className={`kg-edge-arrow-in${isCitedEdge ? ' kg-edge-cited-arrow' : ''}`}
                          points={relArrow}
                          fill={arrowFill}
                          fillOpacity={bothHl || isCitedEdge ? 0.85 : 1}
                          style={{
                            pointerEvents: 'none',
                            animationDelay: `${edgeDelay}ms`,
                          }}
                        />
                      </g>
                    </g>
                  </g>
                );
              })}

              {renderedNodes.map((node, nodeIndex) => {
                const isHl = highlighted.has(node.id);
                const isCited = cited.has(node.id);
                const isHovered = hoveredNode?.id === node.id;
                const color = node.color;
                const [r, g, b] = hexToRgb(color);
                const labelLines = splitLabel(node.label);
                const showLabel = nodes.length <= 150 || isHl || isHovered;

                return (
                  <g
                    key={`${revealKey}:node:${node.id}`}
                    className="kg-node-in"
                    style={{ animationDelay: `${nodes.length <= 100 ? nodeIndex * 35 : 0}ms` }}
                    transform={`translate(${node.x}, ${node.y})`}
                    onMouseEnter={() => handleNodeEnter(node)}
                    onMouseLeave={handleNodeLeave}
                    onPointerDown={e => e.stopPropagation()}
                    onClick={e => {
                      e.stopPropagation();
                      setSelectedNode(prev => (prev?.id === node.id ? null : node));
                    }}
                  >
                    <g
                      key={isCited ? `cite-${citationAnimationKey}-${node.id}` : `node-${node.id}`}
                      className={`kg-node-scale${isHovered && !isCited ? ' kg-node-hovered' : ''}${isCited ? ' kg-node-cited' : ''}`}
                      style={{ cursor: 'pointer' }}
                    >
                      {isHl && (
                        <circle cx={0} cy={0} r={R + 11} fill={`rgba(${r},${g},${b},0.1)`} />
                      )}

                      <circle cx={0} cy={0} r={R} fill={color} stroke="none" />
                    </g>

                    {showLabel && labelLines.map((line, lineIndex) => (
                      <text
                        key={`${node.id}-label-${lineIndex}`}
                        x={0}
                        y={R + 11 + lineIndex * 11}
                        textAnchor="middle"
                        fontSize={9}
                        fontFamily="system-ui, sans-serif"
                        fill={isHl ? 'rgba(0,0,0,0.78)' : 'rgba(0,0,0,0.5)'}
                        fontWeight={isHl ? '600' : '400'}
                        pointerEvents="none"
                      >
                        {line}
                      </text>
                    ))}
                  </g>
                );
              })}
            </svg>

            {hoverPopup && (
              <KGHoverPopup
                hoverTarget={hoverPopup.target}
                popupRef={popupRef}
                style={{
                  left: popupPos?.x ?? hoverPopup.anchorX,
                  top: popupPos?.y ?? hoverPopup.anchorY,
                  visibility: popupPos ? 'visible' : 'hidden',
                }}
              />
            )}
            </div>
          </ResizablePanel>

          <ResizableHandle withHandle className="bg-slate-200" />

          <ResizablePanel defaultSize={55} minSize={20} maxSize={70}>
            <div className="h-full min-h-0 px-4 py-3">
              {selectedNode ? (
                <NodeDetailPanel
                  key={selectedNode.id}
                  node={selectedNode}
                  graph={graph}
                  graphSourcePath={graph.source_path}
                  onClose={() => {
                    if (searchedNodeId && selectedNode.id === searchedNodeId) {
                      setSearchedNodeId(null);
                      return;
                    }
                    setSelectedNode(null);
                  }}
                  onNodeUpdated={(updated, refreshedGraph) => {
                    setSelectedNode(prev => (
                      prev && prev.id === updated.id
                        ? { ...prev, ...updated, color: getNodeColor(updated.type) }
                        : prev
                    ));
                    onNodeUpdated?.(updated, refreshedGraph);
                  }}
                />
              ) : hoveredNode ? (
                <NodeHoverPreview key={hoveredNode.id} node={hoveredNode} />
              ) : (
                <div className="flex h-full min-h-0 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 text-xs text-slate-400">
                  Hover a node to preview · Click to pin
                </div>
              )}
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>

      <style>{`
        @keyframes kg-node-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .kg-node-in {
          animation: kg-node-in 0.35s ease both;
        }
        .kg-node-scale,
        .kg-edge-scale {
          transform-box: fill-box;
          transform-origin: center;
          transition: transform 0.18s ease;
        }
        .kg-node-hovered,
        .kg-edge-hovered {
          transform: scale(1.38);
        }
        @keyframes kg-cited-pulse {
          0%, 100% { transform: scale(${CITED_SCALE}); }
          50% { transform: scale(1); }
        }
        .kg-node-cited {
          animation: kg-cited-pulse ${CITED_PULSE_MS}ms ease-in-out infinite;
          transition: none;
        }
        @keyframes kg-cited-edge-glow {
          0%, 100% { opacity: 0.95; }
          50% { opacity: 0.28; }
        }
        .kg-edge-cited-glow,
        .kg-edge-cited-arrow {
          animation: kg-cited-edge-glow ${CITED_PULSE_MS}ms ease-in-out infinite;
        }
        @keyframes kg-edge-in {
          from { stroke-dashoffset: var(--edge-len); }
          to { stroke-dashoffset: 0; }
        }
        .kg-edge-in {
          animation: kg-edge-in 0.5s ease both;
        }
        @keyframes kg-edge-arrow-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .kg-edge-arrow-in {
          animation: kg-edge-arrow-in 0.5s ease both;
        }
        @media (prefers-reduced-motion: reduce) {
          .kg-node-in { animation: none; }
          .kg-node-scale,
          .kg-edge-scale { transition: none; }
          .kg-node-hovered,
          .kg-edge-hovered { transform: none; }
          .kg-edge-in {
            animation: none;
            stroke-dashoffset: 0 !important;
            stroke-dasharray: none !important;
          }
          .kg-edge-arrow-in { animation: none; }
          .kg-node-cited { animation: none; transform: scale(1.15); }
          .kg-edge-cited-glow,
          .kg-edge-cited-arrow {
            animation: none;
            opacity: 0.7;
          }
        }
      `}</style>
    </div>
  );
}
