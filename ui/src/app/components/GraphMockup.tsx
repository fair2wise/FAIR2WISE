import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Crosshair, Maximize2, X, ZoomIn, ZoomOut } from 'lucide-react';
import { AsciiOrb } from './AsciiOrb';
import { CodeBlock } from './CodeBlock';
import { KGHoverPopup, KGHoverTarget } from './KGInfoPanel';
import { PublicationList } from './PublicationList';
import {
  fetchGraphNodeDetail,
  GraphPayload,
  LinkedCodeSnippet,
  LiveGraphNode,
  PublicationInfo,
} from './data/liveAgent';

const W = 900;
const H = 640;
const R = 16;
const ARROW_SIZE = 7;
const MIN_VIEW = 180;
const WORLD_PADDING = 96;

type NodeType = 'material' | 'property' | 'application' | 'process' | 'compound' | 'code' | 'other';

interface LayoutNode extends LiveGraphNode {
  bucket: NodeType;
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
}

const NODE_COLORS: Record<NodeType, string> = {
  material: '#2563eb',
  property: '#059669',
  application: '#d97706',
  process: '#7c3aed',
  compound: '#dc2626',
  code: '#0891b2',
  other: '#64748b',
};

const NODE_TYPE_LABELS: Record<NodeType, string> = {
  material: 'Material',
  property: 'Property',
  application: 'Application',
  process: 'Process',
  compound: 'Compound',
  code: 'Code',
  other: 'Other',
};

function hexToRgb(hex: string): [number, number, number] {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

function classifyNodeType(type: string): NodeType {
  const value = type.toLowerCase();
  if (value.includes('material')) return 'material';
  if (value.includes('property') || value.includes('parameter')) return 'property';
  if (value.includes('application') || value.includes('device')) return 'application';
  if (value.includes('process') || value.includes('technique') || value.includes('method')) return 'process';
  if (value.includes('compound') || value.includes('chemical') || value.includes('molecule')) return 'compound';
  if (value.includes('codesnippet') || value.includes('code') || value.includes('function')) return 'code';
  return 'other';
}

function clamp(value: number, min: number, max: number) {
  if (max < min) return min;
  return Math.max(min, Math.min(max, value));
}

function fullViewBox(width: number, height: number): ViewBox {
  return { x: 0, y: 0, width, height };
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
  const sorted = [...graph.nodes].sort((a, b) => a.label.localeCompare(b.label));
  const count = sorted.length;
  if (count === 0) return { nodes: [], width: W, height: H };

  const aspect = W / H;
  const spacing = count > 240 ? 70 : count > 120 ? 82 : count > 50 ? 96 : 116;
  const area = Math.max(W * H, count * spacing * spacing);
  const width = Math.max(W, Math.ceil(Math.sqrt(area * aspect)) + WORLD_PADDING * 2);
  const height = Math.max(H, Math.ceil(width / aspect));
  const cx = width / 2;
  const cy = height / 2;
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const radiusStep = Math.min(width, height) / (2.4 * Math.sqrt(count));

  const nodes = sorted.map((node, index) => {
    const radius = radiusStep * Math.sqrt(index + 0.5);
    const angle = index * goldenAngle;
    return {
      ...node,
      bucket: classifyNodeType(node.type),
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    };
  });

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

function NodeDetailPopup({
  node,
  graphSourcePath,
  onClose,
}: {
  node: LayoutNode;
  graphSourcePath: string;
  onClose: () => void;
}) {
  const [shown, setShown] = useState(false);
  const [detail, setDetail] = useState<LiveGraphNode | null>(null);
  const color = NODE_COLORS[node.bucket];

  useEffect(() => {
    const raf = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDetail(node);

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

  const close = () => {
    setShown(false);
    window.setTimeout(onClose, 200);
  };

  const display = detail ?? node;
  const publications = display.publications ?? [];
  const linkedSnippets = display.linked_code_snippets ?? [];

  return (
    <div
      className={`absolute inset-0 z-20 flex items-center justify-center p-5 transition-opacity duration-200 ${shown ? 'opacity-100' : 'opacity-0'}`}
    >
      <div className="absolute inset-0 bg-slate-900/20" onClick={close} />
      <div
        className={`relative z-10 max-h-full w-full max-w-lg overflow-y-auto rounded-xl border border-slate-200 bg-white p-4 shadow-xl transition-all duration-200 ${shown ? 'scale-100 opacity-100' : 'scale-95 opacity-0'}`}
      >
        <div className="flex items-start gap-3">
          <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium" style={{ color }}>
              {node.type || NODE_TYPE_LABELS[node.bucket]}
            </div>
            <div className="mt-0.5 text-sm font-semibold text-slate-800">{node.label}</div>
          </div>
          <button
            type="button"
            aria-label="Close node details"
            title="Close"
            onClick={close}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-slate-200 bg-white text-slate-400 hover:text-slate-600"
          >
            <X size={13} />
          </button>
        </div>
        {(display.description || node.id || publications.length > 0 || display.code_snippet) && (
          <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
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

export function GraphMockup({ graph, highlightedNodeIds }: GraphMockupProps) {
  const [hoverPopup, setHoverPopup] = useState<HoverPopupState | null>(null);
  const [popupPos, setPopupPos] = useState<{ x: number; y: number } | null>(null);
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null);
  const [viewBox, setViewBox] = useState<ViewBox>(fullViewBox(W, H));
  const [drag, setDrag] = useState<DragState | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  const highlighted = useMemo(() => new Set(highlightedNodeIds), [highlightedNodeIds]);
  const displayGraph = useMemo<GraphPayload>(() => {
    if (highlightedNodeIds.length === 0) {
      return { nodes: [], edges: [], source_path: graph.source_path };
    }
    const nodes = graph.nodes.filter(node => highlighted.has(node.id));
    const visibleNodeIds = new Set(nodes.map(node => node.id));
    return {
      nodes,
      edges: graph.edges.filter(edge => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)),
      source_path: graph.source_path,
    };
  }, [graph, highlighted, highlightedNodeIds.length]);
  const layout = useMemo(() => layoutGraph(displayGraph), [displayGraph]);
  const nodes = layout.nodes;
  const nodeMap = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
  const visibleEdges = displayGraph.edges;
  const hasVisibleNodes = nodes.length > 0;
  // Replays the populate animation whenever the visible node set changes.
  const revealKey = useMemo(() => nodes.map(node => node.id).join('|'), [nodes]);

  useEffect(() => {
    setHoverPopup(null);
    setPopupPos(null);
  }, [revealKey]);

  useEffect(() => {
    setViewBox(fullViewBox(layout.width, layout.height));
  }, [layout.width, layout.height, displayGraph.source_path]);

  useEffect(() => {
    if (nodes.length === 0) return;
    setViewBox(fitNodesViewBox(nodes, layout.width, layout.height));
  }, [nodes, layout.width, layout.height]);

  useEffect(() => {
    setHoverPopup(prev =>
      prev?.target.kind === 'node' && nodeMap.has(prev.target.node.id) ? prev : null,
    );
    setSelectedNode(prev => prev && nodeMap.has(prev.id) ? nodeMap.get(prev.id) ?? null : null);
  }, [nodeMap]);

  useLayoutEffect(() => {
    if (!hoverPopup || !popupRef.current || !containerRef.current || !svgRef.current) {
      setPopupPos(null);
      return;
    }

    const containerRect = containerRef.current.getBoundingClientRect();
    const svgRect = svgRef.current.getBoundingClientRect();
    const popupRect = popupRef.current.getBoundingClientRect();
    const nodeScreens = nodes.map(node => nodeToContainer(node, viewBox, svgRect, containerRect));

    let anchorX = hoverPopup.anchorX;
    let anchorY = hoverPopup.anchorY;
    let anchorRadius = hoverPopup.anchorRadius;
    if (hoverPopup.target.kind === 'node') {
      const node = nodeMap.get(hoverPopup.target.node.id);
      if (node) {
        const anchor = nodeToContainer(node, viewBox, svgRect, containerRect);
        anchorX = anchor.x;
        anchorY = anchor.y;
        anchorRadius = anchor.radius;
      }
    }

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
  }, [hoverPopup, nodes, viewBox, nodeMap]);

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

  function setNodeHover(node: LayoutNode) {
    const container = containerRef.current;
    const svg = svgRef.current;
    if (!container || !svg) return;
    const containerRect = container.getBoundingClientRect();
    const svgRect = svg.getBoundingClientRect();
    const anchor = nodeToContainer(node, viewBox, svgRect, containerRect);
    setHoverPopup({
      target: {
        kind: 'node',
        node: {
          id: node.id,
          label: node.label,
          type: node.type,
          description: node.description,
          bucket: node.bucket,
        },
      },
      anchorX: anchor.x,
      anchorY: anchor.y,
      anchorRadius: anchor.radius,
    });
  }

  function handleNodeEnter(node: LayoutNode) {
    setNodeHover(node);
  }

  function handleNodeLeave() {
    setHoverPopup(prev => (prev?.target.kind === 'node' ? null : prev));
    setPopupPos(null);
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
          className="relative z-10 flex items-center justify-between gap-3 px-4 py-2.5 shrink-0"
          style={{ borderBottom: '1px solid rgba(0,0,0,0.07)', background: 'rgba(255,255,255,0.9)' }}
        >
          <span className="text-xs" style={{ color: 'rgba(0,0,0,0.35)' }}>
            0 nodes · 0 edges
          </span>
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
        className="relative z-10 flex items-center justify-between gap-3 px-4 py-2.5 shrink-0"
        style={{ borderBottom: '1px solid rgba(0,0,0,0.07)', background: 'rgba(255,255,255,0.9)' }}
      >
        <span className="text-xs" style={{ color: 'rgba(0,0,0,0.35)' }}>
          {nodes.length} nodes · {visibleEdges.length} edges
        </span>
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
            aria-label="Focus retrieved nodes"
            title="Focus retrieved nodes"
            onClick={resetView}
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:text-slate-700"
          >
            {hasVisibleNodes ? <Crosshair size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      <div className="relative z-10 min-h-0 flex-1">
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
          {visibleEdges.map((edge, i) => {
            const src = nodeMap.get(edge.source);
            const tgt = nodeMap.get(edge.target);
            if (!src || !tgt) return null;

            const srcHl = highlighted.has(src.id);
            const tgtHl = highlighted.has(tgt.id);
            const bothHl = srcHl && tgtHl;
            const connectedHl = srcHl || tgtHl;
            const color = NODE_COLORS[src.bucket];
            const geom = directedEdgeGeometry(src, tgt);
            const stroke = bothHl ? color : connectedHl ? 'rgba(14,165,233,0.22)' : 'rgba(0,0,0,0.09)';
            const arrowFill = bothHl ? color : connectedHl ? 'rgba(14,165,233,0.55)' : 'rgba(0,0,0,0.35)';
            // Edges draw out after the nodes have populated.
            const edgeDelay = nodes.length * 35 + i * 20;
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
                    className={`kg-edge-scale${isEdgeHovered ? ' kg-edge-hovered' : ''}`}
                  >
                    <line
                      className="kg-edge-in"
                      x1={geom.x1 - midX} y1={geom.y1 - midY}
                      x2={geom.x2 - midX} y2={geom.y2 - midY}
                      stroke={stroke}
                      strokeWidth={bothHl ? 1.6 : connectedHl ? 1.1 : 0.7}
                      strokeOpacity={bothHl ? 0.85 : 1}
                      style={{
                        pointerEvents: 'none',
                        ['--edge-len' as string]: `${geom.length}`,
                        strokeDasharray: geom.length,
                        strokeDashoffset: geom.length,
                        animationDelay: `${edgeDelay}ms`,
                      }}
                    />
                    <polygon
                      className="kg-edge-arrow-in"
                      points={relArrow}
                      fill={arrowFill}
                      fillOpacity={bothHl ? 0.85 : 1}
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

          {nodes.map((node, nodeIndex) => {
            const isHl = highlighted.has(node.id);
            const isHovered =
              hoverPopup?.target.kind === 'node' && hoverPopup.target.node.id === node.id;
            const color = NODE_COLORS[node.bucket];
            const [r, g, b] = hexToRgb(color);

            return (
              <g
                key={`${revealKey}:node:${node.id}`}
                className="kg-node-in"
                style={{ animationDelay: `${nodeIndex * 35}ms` }}
                transform={`translate(${node.x}, ${node.y})`}
                onMouseEnter={() => handleNodeEnter(node)}
                onMouseLeave={handleNodeLeave}
                onPointerDown={e => e.stopPropagation()}
                onClick={e => {
                  e.stopPropagation();
                  setSelectedNode(node);
                }}
              >
                <g
                  className={`kg-node-scale${isHovered ? ' kg-node-hovered' : ''}`}
                  style={{ cursor: 'pointer' }}
                >
                  {isHl && (
                    <circle cx={0} cy={0} r={R + 11} fill={`rgba(${r},${g},${b},0.1)`} />
                  )}

                  <circle cx={0} cy={0} r={R} fill={color} stroke="none" />
                </g>
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

        {selectedNode && (
          <NodeDetailPopup
            key={selectedNode.id}
            node={selectedNode}
            graphSourcePath={graph.source_path}
            onClose={() => setSelectedNode(null)}
          />
        )}
        </div>
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
        }
      `}</style>
    </div>
  );
}
