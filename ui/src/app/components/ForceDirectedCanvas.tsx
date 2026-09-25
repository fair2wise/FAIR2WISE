import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { KGHoverPopup, KGHoverTarget } from './KGInfoPanel';
import {
  catalogIdsFromGraph,
  isolateNeighborIds,
  nodeDegreeMap,
  overlayCatalogColor,
  pathContainsEdge,
  shortestUndirectedPath,
} from './kgCatalog';
import { getNodeColor, isUnknownNodeCategory } from './kgNodeColors';
import type { GraphPayload, LiveGraphNode } from './data/liveAgent';

const NODE_R = 16;
const CITED_PULSE_MS = 2200;
const CITED_SCALE = 1.35;

export interface ForceLayoutNode extends LiveGraphNode {
  color: string;
  x: number;
  y: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface ForceDirectedCanvasHandle {
  resetView: () => void;
  focusNode: (nodeId: string) => void;
}

interface ForceLink {
  source: ForceLayoutNode | string;
  target: ForceLayoutNode | string;
  predicate: string;
}

function splitLabel(label: string) {
  const clean = label.length > 28 ? `${label.slice(0, 25)}...` : label;
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [clean];
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid).join(' '), words.slice(mid).join(' ')];
}

// ─────────────────────────────────────────────────────────────────────────────
// ForceDirectedCanvas
//
// Performance architecture:
//   • Uses HTML5 Canvas instead of SVG for rendering — eliminates thousands of
//     DOM element updates on every simulation tick.
//   • Simulation tick calls drawFrame() directly (no React state update).
//   • React state is only used for the search UI overlay and the hover popup.
//   • Simulation only restarts on graph topology changes (nodes/edges/graphKey),
//     NOT when highlight/cite/search props change — those just redraw.
//   • Cited-node pulse animation runs via a dedicated RAF loop.
// ─────────────────────────────────────────────────────────────────────────────
export const ForceDirectedCanvas = forwardRef<ForceDirectedCanvasHandle, {
  graph: GraphPayload;
  highlightedNodeIds: string[];
  citedNodeIds: string[];
  citationAnimationKey: string;
  searchedNodeId: string | null;
  hideWeak: boolean;
  selectedNodeId: string | null;
  onSelectNode: (node: ForceLayoutNode | null) => void;
  onHoverNode: (node: ForceLayoutNode | null) => void;
}>(function ForceDirectedCanvas({
  graph,
  highlightedNodeIds,
  citedNodeIds,
  citationAnimationKey: _citationAnimationKey, // used only for reset timing; animation is time-based
  searchedNodeId,
  hideWeak,
  selectedNodeId,
  onSelectNode,
  onHoverNode,
}, ref) {
  // ── Canvas & D3 refs ──────────────────────────────────────────────────────
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const zoomRef = useRef<d3.ZoomBehavior<HTMLCanvasElement, unknown> | null>(null);
  const simRef = useRef<d3.Simulation<ForceLayoutNode, ForceLink> | null>(null);
  const nodesRef = useRef<ForceLayoutNode[]>([]);
  const transformRef = useRef(d3.zoomIdentity);

  // ── React state (UI overlays only — NOT for rendering positions) ──────────
  const [searchableNodes, setSearchableNodes] = useState<ForceLayoutNode[]>([]);
  const [isolatedId, setIsolatedId] = useState<string | null>(null);
  const [pathEnds, setPathEnds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [hoverPopup, setHoverPopup] = useState<{
    target: Exclude<KGHoverTarget, null>;
    x: number;
    y: number;
  } | null>(null);

  // ── Refs for draw loop: sync on every render so drawFrame sees latest values
  // (drawFrame has empty deps — reads exclusively through these refs) ─────────
  const highlightedRef = useRef(new Set<string>());
  const citedRef = useRef(new Set<string>());
  const isolatedIdsRef = useRef<Set<string> | null>(null);
  const pathIdsRef = useRef<string[] | null>(null);
  const selectedNodeIdRef = useRef<string | null>(selectedNodeId);
  const pathEndsRef = useRef<string[]>([]);
  const hideWeakRef = useRef(hideWeak);
  const degreesRef = useRef(new Map<string, number>());
  const overlayActiveRef = useRef(false);
  const graphRef = useRef(graph);
  const onHoverNodeRef = useRef(onHoverNode);
  const onSelectNodeRef = useRef(onSelectNode);

  // Sync all draw-relevant values into refs on every render
  useLayoutEffect(() => {
    highlightedRef.current = new Set(searchedNodeId ? [searchedNodeId] : highlightedNodeIds);
    citedRef.current = new Set(citedNodeIds);
    selectedNodeIdRef.current = selectedNodeId;
    hideWeakRef.current = hideWeak;
    graphRef.current = graph;
    overlayActiveRef.current = catalogIdsFromGraph(graph).length > 1;
    onHoverNodeRef.current = onHoverNode;
    onSelectNodeRef.current = onSelectNode;
  });

  useLayoutEffect(() => { pathEndsRef.current = pathEnds; }, [pathEnds]);

  const degrees = useMemo(
    () => nodeDegreeMap(graph.nodes, graph.edges),
    [graph.nodes, graph.edges],
  );
  useLayoutEffect(() => { degreesRef.current = degrees; }, [degrees]);

  const graphKey = useMemo(
    () => `${graph.source_path}:${graph.nodes.map(node => node.id).join('|')}:${graph.edges.length}`,
    [graph],
  );

  const isolatedIds = useMemo(() => {
    if (!isolatedId) return null;
    return isolateNeighborIds(graph.edges, isolatedId);
  }, [graph.edges, isolatedId]);
  useLayoutEffect(() => { isolatedIdsRef.current = isolatedIds; }, [isolatedIds]);

  const pathIds = useMemo(() => {
    if (pathEnds.length !== 2) return null;
    return shortestUndirectedPath(graph.edges, pathEnds[0], pathEnds[1]);
  }, [graph.edges, pathEnds]);
  useLayoutEffect(() => { pathIdsRef.current = pathIds; }, [pathIds]);

  useEffect(() => {
    setIsolatedId(null);
    setPathEnds([]);
    setSearchQuery('');
  }, [graphKey]);

  // ── Core draw function ────────────────────────────────────────────────────
  // Stable reference (empty deps) — reads all visual state through refs.
  // No React state updates; pure canvas 2D API calls.
  const drawFrame = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Sync canvas buffer to physical pixel size
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    const targetW = Math.round(cssW * dpr);
    const targetH = Math.round(cssH * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();

    const t = transformRef.current;
    ctx.scale(dpr, dpr);
    ctx.translate(t.x, t.y);
    ctx.scale(t.k, t.k);

    const nodes = nodesRef.current;
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const highlighted = highlightedRef.current;
    const cited = citedRef.current;
    const isoIds = isolatedIdsRef.current;
    const pIds = pathIdsRef.current;
    const selId = selectedNodeIdRef.current;
    const pEnds = pathEndsRef.current;
    const now = performance.now();
    const hw = hideWeakRef.current;
    const degs = degreesRef.current;
    const g = graphRef.current;

    function isHid(nodeId: string): boolean {
      if (isoIds && !isoIds.has(nodeId)) return true;
      if (hw && (degs.get(nodeId) ?? 0) < 2) return true;
      return false;
    }

    // ── Draw edges ──────────────────────────────────────────────────────────
    // Animated path dash offset (flows at ~20px/s)
    const dashOffset = -(now / 1000 * 20) % 24;

    for (const edge of g.edges) {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) continue;
      if (isHid(src.id) || isHid(tgt.id)) continue;

      const onPath = pathContainsEdge(pIds, src.id, tgt.id);
      const isCitedEdge = cited.has(src.id) || cited.has(tgt.id);
      const bothHl = highlighted.has(src.id) && highlighted.has(tgt.id);
      const connectedHl = highlighted.has(src.id) || highlighted.has(tgt.id);

      let stroke: string;
      let lw: number;
      if (onPath) {
        stroke = '#0ea5e9';
        lw = 2.4;
      } else if (isCitedEdge) {
        stroke = 'rgba(14,165,233,0.72)';
        lw = 2.4;
      } else if (bothHl) {
        stroke = src.color;
        lw = 1.5;
      } else if (connectedHl) {
        stroke = 'rgba(14,165,233,0.22)';
        lw = 0.8;
      } else {
        stroke = 'rgba(0,0,0,0.12)';
        lw = 0.8;
      }

      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lw;
      if (onPath) {
        ctx.setLineDash([8, 4]);
        ctx.lineDashOffset = dashOffset;
      } else {
        ctx.setLineDash([]);
        ctx.lineDashOffset = 0;
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;

    // ── Draw nodes ──────────────────────────────────────────────────────────
    for (const node of nodes) {
      if (isHid(node.id)) continue;

      const isHl = highlighted.has(node.id);
      const isCited = cited.has(node.id);
      const isSelected = selId === node.id || pEnds.includes(node.id);

      // Cited pulse: oscillates between 1 and CITED_SCALE
      let scale = 1;
      if (isCited) {
        const phase = (now % CITED_PULSE_MS) / CITED_PULSE_MS;
        scale = 1 + (CITED_SCALE - 1) * Math.abs(Math.sin(Math.PI * phase));
      }
      const r = NODE_R * scale;

      // Highlight halo
      if (isHl) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 11, 0, Math.PI * 2);
        ctx.fillStyle = `${node.color}1a`;
        ctx.fill();
      }

      // Node fill
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      ctx.fillStyle = node.color;
      ctx.fill();

      // Selection / overlay border
      if (isSelected) {
        ctx.strokeStyle = '#0ea5e9';
        ctx.lineWidth = 3;
        ctx.stroke();
      } else if (overlayActiveRef.current && node.graph_id) {
        const oc = overlayCatalogColor(node.graph_id);
        if (oc) {
          ctx.strokeStyle = oc;
          ctx.lineWidth = 2.5;
          ctx.stroke();
        }
      }

      // Label — always shown
      {
        const lines = splitLabel(node.label);
        ctx.textAlign = 'center';
        ctx.font = `${isHl ? '600' : '400'} 9px system-ui, sans-serif`;
        ctx.fillStyle = isHl ? 'rgba(0,0,0,0.78)' : 'rgba(0,0,0,0.5)';
        for (let li = 0; li < lines.length; li++) {
          ctx.fillText(lines[li], node.x, node.y + r + 11 + li * 11);
        }
      }
    }

    ctx.restore();
  }, []); // stable — reads exclusively from refs

  // ── Animation loop for cited-node pulse & path dash ──────────────────────
  // Runs only while there are cited nodes or an active path, keeping the
  // canvas alive without restarting the physics simulation.
  useEffect(() => {
    const hasCited = citedNodeIds.length > 0;
    const hasPath = pathIds !== null;
    if (!hasCited && !hasPath) {
      drawFrame();
      return;
    }
    let running = true;
    let rafId = 0;
    function loop() {
      if (!running) return;
      drawFrame();
      rafId = window.requestAnimationFrame(loop);
    }
    rafId = window.requestAnimationFrame(loop);
    return () => {
      running = false;
      window.cancelAnimationFrame(rafId);
    };
  }, [citedNodeIds, pathIds, drawFrame]);

  // Redraw when visual state changes (but do NOT restart the simulation)
  useEffect(() => {
    drawFrame();
  }, [drawFrame, highlightedNodeIds, searchedNodeId, selectedNodeId, hideWeak, isolatedIds, pathIds, pathEnds]);

  // ── Resize observer: redraw when canvas CSS size changes ──────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ro = new ResizeObserver(() => { drawFrame(); });
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [drawFrame]);

  // ── Zoom on canvas ────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    function hitNode(offsetX: number, offsetY: number): boolean {
      const [wx, wy] = transformRef.current.invert([offsetX, offsetY]);
      return nodesRef.current.some(node => {
        const dx = node.x - wx;
        const dy = node.y - wy;
        return dx * dx + dy * dy <= (NODE_R + 4) * (NODE_R + 4);
      });
    }

    const zoom = d3.zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.1, 5])
      .filter(event => {
        if (event.type === 'wheel') return true;
        // Prevent zoom from swallowing node drag gestures
        if (event instanceof MouseEvent || event instanceof PointerEvent) {
          return !hitNode(event.offsetX, event.offsetY);
        }
        return true;
      })
      .on('zoom', event => {
        transformRef.current = event.transform;
        drawFrame();
      });

    d3.select(canvas).call(zoom);
    zoomRef.current = zoom;
    return () => {
      d3.select(canvas).on('.zoom', null);
      zoomRef.current = null;
    };
  }, [drawFrame]);

  // ── Force simulation ──────────────────────────────────────────────────────
  // Deps: only graph topology. Highlight / cite / search changes do NOT restart.
  useEffect(() => {
    const canvas = canvasRef.current;
    const width = canvas?.clientWidth || 900;
    const height = canvas?.clientHeight || 640;

    const simNodes: ForceLayoutNode[] = graph.nodes
      .filter(node => !isUnknownNodeCategory(node.type))
      .map((node, index) => {
        const previous = nodesRef.current.find(item => item.id === node.id);
        const angle = (index / Math.max(graph.nodes.length, 1)) * Math.PI * 2;
        return {
          ...node,
          label: node.label || node.id,
          color: getNodeColor(node.type),
          x: previous?.x ?? width / 2 + Math.cos(angle) * 40,
          y: previous?.y ?? height / 2 + Math.sin(angle) * 40,
          vx: previous?.vx ?? 0,
          vy: previous?.vy ?? 0,
        };
      });
    const nodeById = new Map(simNodes.map(node => [node.id, node]));
    const links: ForceLink[] = graph.edges
      .filter(edge => nodeById.has(edge.source) && nodeById.has(edge.target))
      .map(edge => ({
        source: edge.source,
        target: edge.target,
        predicate: edge.predicate,
      }));

    nodesRef.current = simNodes;
    setSearchableNodes(simNodes); // for search suggestions (one-time, no tick update)

    let raf = 0;
    const simulation = d3.forceSimulation(simNodes)
      .force('link', d3.forceLink<ForceLayoutNode, ForceLink>(links).id(node => node.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-240))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide<ForceLayoutNode>().radius(NODE_R + 6))
      .on('tick', () => {
        // Throttle to one draw per animation frame — no React state update
        if (raf) return;
        raf = window.requestAnimationFrame(() => {
          raf = 0;
          drawFrame();
        });
      });

    simRef.current = simulation;
    return () => {
      window.cancelAnimationFrame(raf);
      simulation.stop();
      simRef.current = null;
    };
  // NOTE: citedNodeIds / highlightedNodeIds / searchedNodeId intentionally
  // excluded — visual changes must NOT restart the physics simulation.
  }, [graph.edges, graph.nodes, graphKey, drawFrame]);

  // ── Hit testing ───────────────────────────────────────────────────────────
  function hitTestNode(offsetX: number, offsetY: number): ForceLayoutNode | null {
    const [wx, wy] = transformRef.current.invert([offsetX, offsetY]);
    for (const node of nodesRef.current) {
      const dx = node.x - wx;
      const dy = node.y - wy;
      if (dx * dx + dy * dy <= (NODE_R + 4) * (NODE_R + 4)) return node;
    }
    return null;
  }

  function hitTestEdge(offsetX: number, offsetY: number) {
    const [wx, wy] = transformRef.current.invert([offsetX, offsetY]);
    const nodeMap = new Map(nodesRef.current.map(n => [n.id, n]));
    const threshold = 6 / (transformRef.current.k || 1);
    let best: { src: ForceLayoutNode; tgt: ForceLayoutNode; predicate: string; dist: number } | null = null;

    for (const edge of graphRef.current.edges) {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) continue;
      const dx = tgt.x - src.x;
      const dy = tgt.y - src.y;
      const lenSq = dx * dx + dy * dy;
      if (lenSq === 0) continue;
      const t2 = Math.max(0, Math.min(1, ((wx - src.x) * dx + (wy - src.y) * dy) / lenSq));
      const dist = Math.hypot(wx - (src.x + t2 * dx), wy - (src.y + t2 * dy));
      if (dist < threshold && (!best || dist < best.dist)) {
        best = { src, tgt, predicate: edge.predicate, dist };
      }
    }
    return best;
  }

  // ── Navigation helpers ────────────────────────────────────────────────────
  function zoomToNode(node: ForceLayoutNode) {
    const canvas = canvasRef.current;
    const zoom = zoomRef.current;
    if (!canvas || !zoom) return;
    const width = canvas.clientWidth || 900;
    const height = canvas.clientHeight || 640;
    const scale = 2;
    const next = d3.zoomIdentity
      .translate(width / 2 - node.x * scale, height / 2 - node.y * scale)
      .scale(scale);
    d3.select(canvas).transition().duration(750).call(zoom.transform, next);
  }

  function resetView() {
    const canvas = canvasRef.current;
    const zoom = zoomRef.current;
    if (!canvas || !zoom) return;
    setIsolatedId(null);
    setPathEnds([]);
    d3.select(canvas).transition().duration(750).call(zoom.transform, d3.zoomIdentity);
  }

  function focusNode(nodeId: string) {
    const node = nodesRef.current.find(item => item.id === nodeId);
    if (!node) return;
    onSelectNodeRef.current(node);
    zoomToNode(node);
  }

  useImperativeHandle(ref, () => ({ resetView, focusNode }), []);

  // Zoom to searched node when it first appears in the simulation
  useEffect(() => {
    if (!searchedNodeId) return;
    const id = searchedNodeId;
    const zoomToFocused = () => {
      const node = nodesRef.current.find(item => item.id === id)
        || nodesRef.current.find(item => item.id.toLowerCase() === id.toLowerCase());
      if (!node) return false;
      onSelectNodeRef.current(node);
      zoomToNode(node);
      return true;
    };
    if (zoomToFocused()) return undefined;
    const timer = window.setTimeout(() => { zoomToFocused(); }, 450);
    return () => window.clearTimeout(timer);
  }, [graphKey, searchedNodeId]);

  // ── Canvas event handlers ─────────────────────────────────────────────────
  function handleCanvasPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    const { offsetX, offsetY } = event.nativeEvent;
    const node = hitTestNode(offsetX, offsetY);
    onHoverNodeRef.current(node);

    if (node) {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (rect) {
        setHoverPopup({
          target: {
            kind: 'node',
            node: {
              id: node.id,
              label: node.label || node.id,
              type: node.type || 'Entity',
              description: node.description || '',
              color: node.color,
            },
          },
          x: event.clientX - rect.left + 12,
          y: event.clientY - rect.top + 12,
        });
      }
    } else {
      const edgeHit = hitTestEdge(offsetX, offsetY);
      if (edgeHit) {
        const rect = canvasRef.current?.getBoundingClientRect();
        if (rect) {
          setHoverPopup({
            target: { kind: 'edge', sourceLabel: edgeHit.src.label, targetLabel: edgeHit.tgt.label, predicate: edgeHit.predicate },
            x: event.clientX - rect.left + 12,
            y: event.clientY - rect.top + 12,
          });
        }
      } else {
        setHoverPopup(null);
      }
    }
  }

  function handleCanvasPointerLeave() {
    onHoverNodeRef.current(null);
    setHoverPopup(null);
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const node = hitTestNode(event.nativeEvent.offsetX, event.nativeEvent.offsetY);
    if (node) {
      onSelectNodeRef.current(node);
      setPathEnds(prev => {
        if (prev.includes(node.id)) return prev;
        return [...prev, node.id].slice(-2);
      });
    } else {
      if (isolatedId) setIsolatedId(null);
      setPathEnds([]);
      setHoverPopup(null);
    }
  }

  function handleCanvasDoubleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const node = hitTestNode(event.nativeEvent.offsetX, event.nativeEvent.offsetY);
    if (node) {
      setIsolatedId(node.id);
      onSelectNodeRef.current(node);
    }
  }

  function handleCanvasPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return;
    const node = hitTestNode(event.nativeEvent.offsetX, event.nativeEvent.offsetY);
    if (!node) return;

    event.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;

    const sim = simRef.current;
    const live = nodesRef.current.find(n => n.id === node.id);
    if (!live) return;

    if (sim) {
      sim.alphaTarget(0.3).restart();
      live.fx = live.x;
      live.fy = live.y;
    }

    const pointerId = event.pointerId;
    canvas.setPointerCapture(pointerId);

    function pointFromEvent(clientX: number, clientY: number) {
      if (!canvas) return { x: live!.x, y: live!.y };
      const rect = canvas.getBoundingClientRect();
      const [ix, iy] = transformRef.current.invert([clientX - rect.left, clientY - rect.top]);
      return { x: ix, y: iy };
    }

    function onMove(moveEvent: PointerEvent) {
      if (moveEvent.pointerId !== pointerId) return;
      const pt = pointFromEvent(moveEvent.clientX, moveEvent.clientY);
      live!.fx = pt.x;
      live!.fy = pt.y;
    }

    function onUp(upEvent: PointerEvent) {
      if (upEvent.pointerId !== pointerId) return;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (sim) sim.alphaTarget(0);
      live!.fx = null;
      live!.fy = null;
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  // ── Search suggestions (derived from searchableNodes, not tick positions) ─
  const suggestions = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return [];
    return searchableNodes
      .filter(node => (node.label || '').toLowerCase().includes(query))
      .slice(0, 20);
  }, [searchableNodes, searchQuery]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="relative h-full min-h-0 overflow-hidden">
      {/* Search overlay */}
      <div className="pointer-events-none absolute left-3 top-3 z-20 w-56">
        <div className="pointer-events-auto rounded-md border border-slate-200 bg-white/95 p-2 shadow-sm">
          <input
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
            placeholder="Find node…"
            aria-label="Find node"
            className="h-8 w-full rounded border border-slate-200 px-2 text-xs text-slate-700 outline-none focus:border-sky-400"
          />
          {suggestions.length > 0 && (
            <div className="mt-1 max-h-40 overflow-y-auto">
              {suggestions.map(node => (
                <button
                  key={node.id}
                  type="button"
                  onClick={() => {
                    setSearchQuery(node.label);
                    onSelectNodeRef.current(node);
                    zoomToNode(node);
                  }}
                  className="block w-full truncate rounded px-2 py-1 text-left text-xs text-slate-600 hover:bg-sky-50 hover:text-sky-800"
                >
                  {node.label}
                </button>
              ))}
            </div>
          )}
          <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
            Drag nodes · scroll zoom · double-click isolates neighbors · click two nodes for a path
          </p>
        </div>
      </div>

      {/* Canvas: all graph rendering happens here */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full"
        style={{ display: 'block', cursor: 'grab', touchAction: 'none', userSelect: 'none' }}
        onPointerDown={handleCanvasPointerDown}
        onPointerMove={handleCanvasPointerMove}
        onPointerLeave={handleCanvasPointerLeave}
        onClick={handleCanvasClick}
        onDoubleClick={handleCanvasDoubleClick}
      />

      {hoverPopup && (
        <KGHoverPopup
          hoverTarget={hoverPopup.target}
          style={{ left: hoverPopup.x, top: hoverPopup.y }}
        />
      )}
    </div>
  );
});

ForceDirectedCanvas.displayName = 'ForceDirectedCanvas';
