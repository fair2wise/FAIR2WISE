import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
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
  citationAnimationKey,
  searchedNodeId,
  hideWeak,
  selectedNodeId,
  onSelectNode,
  onHoverNode,
}, ref) {
  const svgRef = useRef<SVGSVGElement>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const simRef = useRef<d3.Simulation<ForceLayoutNode, ForceLink> | null>(null);
  const nodesRef = useRef<ForceLayoutNode[]>([]);
  const transformRef = useRef(d3.zoomIdentity);
  const [nodes, setNodes] = useState<ForceLayoutNode[]>([]);
  const [transform, setTransform] = useState(() => d3.zoomIdentity);
  const [isolatedId, setIsolatedId] = useState<string | null>(null);
  const [pathEnds, setPathEnds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [hoverPopup, setHoverPopup] = useState<{
    target: Exclude<KGHoverTarget, null>;
    x: number;
    y: number;
  } | null>(null);

  const highlighted = useMemo(
    () => new Set(searchedNodeId ? [searchedNodeId] : highlightedNodeIds),
    [highlightedNodeIds, searchedNodeId],
  );
  const cited = useMemo(() => new Set(citedNodeIds), [citedNodeIds]);
  const overlayIds = useMemo(() => catalogIdsFromGraph(graph), [graph]);
  const overlayActive = overlayIds.length > 1;
  const nodeById = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
  const degrees = useMemo(
    () => nodeDegreeMap(graph.nodes, graph.edges),
    [graph.edges, graph.nodes],
  );
  const graphKey = useMemo(
    () => `${graph.source_path}:${graph.nodes.map(node => node.id).join('|')}:${graph.edges.length}`,
    [graph],
  );

  useEffect(() => {
    setIsolatedId(null);
    setPathEnds([]);
    setSearchQuery('');
  }, [graphKey]);

  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 5])
      .filter(event => {
        if (event.type === 'wheel') return true;
        const target = event.target as Element | null;
        return !target?.closest?.('[data-kg-node="true"]');
      })
      .on('zoom', event => {
        transformRef.current = event.transform;
        setTransform(event.transform);
      });

    d3.select(svgEl).call(zoom);
    zoomRef.current = zoom;
    return () => {
      d3.select(svgEl).on('.zoom', null);
      zoomRef.current = null;
    };
  }, []);

  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const width = svgEl.clientWidth || 900;
    const height = svgEl.clientHeight || 640;
    const keep = new Set([
      ...highlightedNodeIds,
      ...citedNodeIds,
      ...(searchedNodeId ? [searchedNodeId] : []),
    ]);
    const simNodes: ForceLayoutNode[] = graph.nodes
      .filter(node => !isUnknownNodeCategory(node.type) || keep.has(node.id))
      .map((node, index) => {
        const previous = nodesRef.current.find(item => item.id === node.id);
        const angle = (index / Math.max(graph.nodes.length, 1)) * Math.PI * 2;
        return {
          ...node,
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
    setNodes(simNodes);

    let raf = 0;
    const simulation = d3.forceSimulation(simNodes)
      .force('link', d3.forceLink<ForceLayoutNode, ForceLink>(links).id(node => node.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-240))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide<ForceLayoutNode>().radius(NODE_R + 6))
      .on('tick', () => {
        if (raf) return;
        raf = window.requestAnimationFrame(() => {
          raf = 0;
          setNodes(nodesRef.current.slice());
        });
      });

    simRef.current = simulation;
    return () => {
      window.cancelAnimationFrame(raf);
      simulation.stop();
      simRef.current = null;
    };
  }, [citedNodeIds, graph.edges, graph.nodes, graphKey, highlightedNodeIds, searchedNodeId]);

  function zoomToNode(node: ForceLayoutNode) {
    const svgEl = svgRef.current;
    const zoom = zoomRef.current;
    if (!svgEl || !zoom) return;
    const width = svgEl.clientWidth || 900;
    const height = svgEl.clientHeight || 640;
    const scale = 2;
    const next = d3.zoomIdentity
      .translate(width / 2 - node.x * scale, height / 2 - node.y * scale)
      .scale(scale);
    d3.select(svgEl).transition().duration(750).call(zoom.transform, next);
  }

  function resetView() {
    const svgEl = svgRef.current;
    const zoom = zoomRef.current;
    if (!svgEl || !zoom) return;
    setIsolatedId(null);
    setPathEnds([]);
    d3.select(svgEl).transition().duration(750).call(zoom.transform, d3.zoomIdentity);
  }

  function focusNode(nodeId: string) {
    const node = nodesRef.current.find(item => item.id === nodeId);
    if (!node) return;
    onSelectNode(node);
    zoomToNode(node);
  }

  useImperativeHandle(ref, () => ({ resetView, focusNode }), [onSelectNode]);

  useEffect(() => {
    if (!searchedNodeId) return;
    const id = searchedNodeId;
    const zoomToFocused = () => {
      const node = nodesRef.current.find(item => item.id === id)
        || nodesRef.current.find(item => item.id.toLowerCase() === id.toLowerCase());
      if (!node) return false;
      onSelectNode(node);
      zoomToNode(node);
      return true;
    };
    if (zoomToFocused()) return undefined;
    const timer = window.setTimeout(() => {
      zoomToFocused();
    }, 450);
    return () => window.clearTimeout(timer);
  }, [graphKey, searchedNodeId]);

  const isolatedIds = useMemo(() => {
    if (!isolatedId) return null;
    return isolateNeighborIds(graph.edges, isolatedId);
  }, [graph.edges, isolatedId]);

  const pathIds = useMemo(() => {
    if (pathEnds.length !== 2) return null;
    return shortestUndirectedPath(graph.edges, pathEnds[0], pathEnds[1]);
  }, [graph.edges, pathEnds]);

  const suggestions = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return [];
    return nodes
      .filter(node => (node.label || '').toLowerCase().includes(query))
      .slice(0, 20);
  }, [nodes, searchQuery]);

  function isHidden(nodeId: string): boolean {
    if (isolatedIds && !isolatedIds.has(nodeId)) return true;
    if (hideWeak && (degrees.get(nodeId) ?? 0) < 2) return true;
    return false;
  }

  function handleNodeClick(event: React.MouseEvent, node: ForceLayoutNode) {
    event.stopPropagation();
    onSelectNode(node);
    setPathEnds(prev => {
      if (prev.includes(node.id)) return prev;
      const next = [...prev, node.id].slice(-2);
      return next;
    });
  }

  function handleNodeDoubleClick(event: React.MouseEvent, node: ForceLayoutNode) {
    event.stopPropagation();
    setIsolatedId(node.id);
    onSelectNode(node);
  }

  function handleBackgroundClick() {
    if (isolatedId) setIsolatedId(null);
    setPathEnds([]);
    setHoverPopup(null);
  }

  function handleNodePointerDown(event: React.PointerEvent, node: ForceLayoutNode) {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const sim = simRef.current;
    const live = nodesRef.current.find(item => item.id === node.id);
    if (!live) return;
    if (sim) {
      sim.alphaTarget(0.3).restart();
      live.fx = live.x;
      live.fy = live.y;
    }

    const svgEl = svgRef.current;
    const pointerId = event.pointerId;

    function pointFromEvent(clientX: number, clientY: number) {
      if (!svgEl) return { x: live.x, y: live.y };
      const rect = svgEl.getBoundingClientRect();
      const inverted = transformRef.current.invert([clientX - rect.left, clientY - rect.top]);
      return { x: inverted[0], y: inverted[1] };
    }

    function onMove(moveEvent: PointerEvent) {
      if (moveEvent.pointerId !== pointerId) return;
      const point = pointFromEvent(moveEvent.clientX, moveEvent.clientY);
      live.fx = point.x;
      live.fy = point.y;
    }

    function onUp(upEvent: PointerEvent) {
      if (upEvent.pointerId !== pointerId) return;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (sim) sim.alphaTarget(0);
      live.fx = null;
      live.fy = null;
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  function handleEdgeHover(
    event: React.MouseEvent<SVGGElement>,
    sourceLabel: string,
    targetLabel: string,
    predicate: string,
  ) {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    setHoverPopup({
      target: { kind: 'edge', sourceLabel, targetLabel, predicate },
      x: event.clientX - rect.left + 12,
      y: event.clientY - rect.top + 12,
    });
  }

  return (
    <div className="relative h-full min-h-0 overflow-hidden">
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
                    onSelectNode(node);
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

      <svg
        ref={svgRef}
        className="absolute inset-0 h-full w-full"
        style={{ display: 'block', cursor: 'grab', touchAction: 'none', userSelect: 'none' }}
        onClick={handleBackgroundClick}
      >
        <defs>
          <filter id="kg-force-edge-glow" x="-120%" y="-120%" width="340%" height="340%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="2.8" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g transform={transform.toString()}>
          {graph.edges.map((edge, index) => {
            const source = nodeById.get(edge.source);
            const target = nodeById.get(edge.target);
            if (!source || !target) return null;
            if (isHidden(source.id) || isHidden(target.id)) return null;
            const onPath = pathContainsEdge(pathIds, source.id, target.id);
            const isCitedEdge = cited.has(source.id) || cited.has(target.id);
            const bothHl = highlighted.has(source.id) && highlighted.has(target.id);
            const connectedHl = highlighted.has(source.id) || highlighted.has(target.id);
            const stroke = onPath
              ? '#0ea5e9'
              : isCitedEdge
                ? 'rgba(14,165,233,0.72)'
                : bothHl
                  ? source.color
                  : connectedHl
                    ? 'rgba(14,165,233,0.22)'
                    : 'rgba(0,0,0,0.12)';
            return (
              <g
                key={`${edge.source}-${edge.target}-${edge.predicate}-${index}`}
                onMouseEnter={event => handleEdgeHover(event, source.label, target.label, edge.predicate)}
                onMouseMove={event => handleEdgeHover(event, source.label, target.label, edge.predicate)}
                onMouseLeave={() => setHoverPopup(null)}
              >
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke="transparent"
                  strokeWidth={8}
                />
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke={stroke}
                  strokeWidth={onPath || isCitedEdge ? 2.4 : bothHl ? 1.5 : 0.8}
                  strokeDasharray={onPath ? '8 4' : undefined}
                  className={onPath ? 'kg-force-path' : undefined}
                  style={{ pointerEvents: 'none' }}
                />
              </g>
            );
          })}

          {nodes.map(node => {
            if (isHidden(node.id)) return null;
            const isHl = highlighted.has(node.id);
            const isCited = cited.has(node.id);
            const isSelected = selectedNodeId === node.id || pathEnds.includes(node.id);
            const overlayStroke = overlayActive && node.graph_id
              ? overlayCatalogColor(node.graph_id)
              : null;
            const labelLines = splitLabel(node.label);
            const showLabel = nodes.length <= 150 || isHl || isSelected;
            return (
              <g
                key={node.id}
                data-kg-node="true"
                transform={`translate(${node.x}, ${node.y})`}
                onPointerDown={event => handleNodePointerDown(event, node)}
                onClick={event => handleNodeClick(event, node)}
                onDoubleClick={event => handleNodeDoubleClick(event, node)}
                onMouseEnter={() => onHoverNode(node)}
                onMouseLeave={() => onHoverNode(null)}
                style={{ cursor: 'pointer' }}
              >
                <g
                  key={isCited ? `cite-${citationAnimationKey}-${node.id}` : `node-${node.id}`}
                  className={isCited ? 'kg-node-cited' : undefined}
                >
                  {isHl && (
                    <circle cx={0} cy={0} r={NODE_R + 11} fill={`${node.color}1a`} />
                  )}
                  <circle
                    cx={0}
                    cy={0}
                    r={NODE_R}
                    fill={node.color}
                    stroke={isSelected ? '#0ea5e9' : overlayStroke || 'none'}
                    strokeWidth={isSelected ? 3 : overlayStroke ? 2.5 : 0}
                  />
                </g>
                {showLabel && labelLines.map((line, lineIndex) => (
                  <text
                    key={`${node.id}-label-${lineIndex}`}
                    x={0}
                    y={NODE_R + 11 + lineIndex * 11}
                    textAnchor="middle"
                    fontSize={9}
                    fontFamily="system-ui, sans-serif"
                    fill={isHl ? 'rgba(0,0,0,0.78)' : 'rgba(0,0,0,0.5)'}
                    fontWeight={isHl ? 600 : 400}
                    pointerEvents="none"
                  >
                    {line}
                  </text>
                ))}
              </g>
            );
          })}
        </g>
      </svg>

      {hoverPopup && (
        <KGHoverPopup
          hoverTarget={hoverPopup.target}
          style={{ left: hoverPopup.x, top: hoverPopup.y }}
        />
      )}

      <style>{`
        @keyframes kg-cited-pulse {
          0%, 100% { transform: scale(${CITED_SCALE}); }
          50% { transform: scale(1); }
        }
        .kg-node-cited {
          transform-box: fill-box;
          transform-origin: center;
          animation: kg-cited-pulse ${CITED_PULSE_MS}ms ease-in-out infinite;
        }
        @keyframes kg-force-dash {
          to { stroke-dashoffset: -24; }
        }
        .kg-force-path {
          animation: kg-force-dash 1.2s linear infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .kg-node-cited { animation: none; transform: scale(1.15); }
          .kg-force-path { animation: none; }
        }
      `}</style>
    </div>
  );
});

ForceDirectedCanvas.displayName = 'ForceDirectedCanvas';
