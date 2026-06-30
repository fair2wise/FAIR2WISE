import { useRef, useEffect, useState, useCallback } from 'react';
import { GRAPH_NODES, GRAPH_EDGES, NODE_COLORS, NODE_TYPE_LABELS, GraphNode, NodeType } from './data/materialsData';

const NODE_RADIUS = 18;

interface Transform {
  x: number;
  y: number;
  scale: number;
}

function runForceLayout(baseNodes: GraphNode[], width: number, height: number): GraphNode[] {
  const typeGroups: Record<NodeType, { angle: number; radius: number }> = {
    material:    { angle: -Math.PI / 2,   radius: 220 },
    property:    { angle: Math.PI * 0.15,  radius: 210 },
    application: { angle: Math.PI * 0.6,  radius: 195 },
    process:     { angle: Math.PI * 1.1,   radius: 185 },
    compound:    { angle: Math.PI * 0.85,  radius: 195 },
  };

  const typeCounts: Record<string, number> = {};
  const typeIdx: Record<string, number> = {};
  baseNodes.forEach(n => { typeCounts[n.type] = (typeCounts[n.type] || 0) + 1; });

  const nodes: GraphNode[] = baseNodes.map(n => {
    const idx = typeIdx[n.type] ?? 0;
    typeIdx[n.type] = idx + 1;
    const group = typeGroups[n.type];
    const count = typeCounts[n.type];
    const spread = Math.PI * 0.4;
    const angle = group.angle + (count > 1 ? (idx / (count - 1) - 0.5) * spread : 0);
    return {
      ...n,
      x: width / 2 + Math.cos(angle) * group.radius + (Math.random() - 0.5) * 40,
      y: height / 2 + Math.sin(angle) * group.radius + (Math.random() - 0.5) * 40,
      vx: 0,
      vy: 0,
    };
  });

  const ITER = 350;
  const REPULSION = 2800;
  const SPRING_LEN = 110;
  const SPRING_K = 0.07;
  const CENTER_K = 0.012;

  for (let iter = 0; iter < ITER; iter++) {
    const cooling = 1 - iter / ITER;

    nodes.forEach(n => { n.vx = 0; n.vy = 0; });

    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = REPULSION / (dist * dist);
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }

    GRAPH_EDGES.forEach(edge => {
      const src = nodes.find(n => n.id === edge.source);
      const tgt = nodes.find(n => n.id === edge.target);
      if (!src || !tgt) return;
      const dx = tgt.x - src.x;
      const dy = tgt.y - src.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = SPRING_K * (dist - SPRING_LEN);
      const fx = (dx / dist) * f;
      const fy = (dy / dist) * f;
      src.vx += fx; src.vy += fy;
      tgt.vx -= fx; tgt.vy -= fy;
    });

    let cx = 0, cy = 0;
    nodes.forEach(n => { cx += n.x; cy += n.y; });
    cx /= nodes.length; cy /= nodes.length;
    nodes.forEach(n => {
      n.vx += (width / 2 - cx) * CENTER_K;
      n.vy += (height / 2 - cy) * CENTER_K;
      n.x += n.vx * 0.85 * cooling;
      n.y += n.vy * 0.85 * cooling;
    });
  }

  return nodes;
}

function hexToRgb(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return [r, g, b];
}

interface Props {
  highlightedNodeIds: string[];
  onReset: () => void;
}

export function GraphCanvas({ highlightedNodeIds, onReset }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const transformRef = useRef<Transform>({ x: 0, y: 0, scale: 1 });
  const highlightedRef = useRef<string[]>([]);
  const animTimeRef = useRef(0);
  const rafRef = useRef<number>(0);
  const dragRef = useRef<{
    mode: 'none' | 'node' | 'pan';
    nodeId: string | null;
    startMouseX: number;
    startMouseY: number;
    startNodeX: number;
    startNodeY: number;
    startTransX: number;
    startTransY: number;
  }>({ mode: 'none', nodeId: null, startMouseX: 0, startMouseY: 0, startNodeX: 0, startNodeY: 0, startTransX: 0, startTransY: 0 });
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [initialized, setInitialized] = useState(false);

  highlightedRef.current = highlightedNodeIds;

  const getNodeAt = useCallback((cssX: number, cssY: number): GraphNode | null => {
    const t = transformRef.current;
    const gx = (cssX - t.x) / t.scale;
    const gy = (cssY - t.y) / t.scale;
    return nodesRef.current.find(n => {
      const dx = n.x - gx;
      const dy = n.y - gy;
      return Math.sqrt(dx * dx + dy * dy) <= NODE_RADIUS + 2;
    }) ?? null;
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const t = transformRef.current;
    const highlighted = highlightedRef.current;
    const hasHighlight = highlighted.length > 0;
    animTimeRef.current += 0.05;
    const at = animTimeRef.current;

    ctx.save();
    ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);

    // Background
    ctx.fillStyle = '#0f1117';
    ctx.fillRect(0, 0, w, h);

    // Grid dots
    ctx.save();
    ctx.fillStyle = 'rgba(255,255,255,0.04)';
    const gridStep = 40 * t.scale;
    const offX = ((t.x % gridStep) + gridStep) % gridStep;
    const offY = ((t.y % gridStep) + gridStep) % gridStep;
    for (let gx = offX; gx < w; gx += gridStep) {
      for (let gy = offY; gy < h; gy += gridStep) {
        ctx.beginPath();
        ctx.arc(gx, gy, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();

    ctx.translate(t.x, t.y);
    ctx.scale(t.scale, t.scale);

    // Draw edges
    GRAPH_EDGES.forEach(edge => {
      const src = nodesRef.current.find(n => n.id === edge.source);
      const tgt = nodesRef.current.find(n => n.id === edge.target);
      if (!src || !tgt) return;

      const bothHighlighted = hasHighlight && highlighted.includes(src.id) && highlighted.includes(tgt.id);
      const eitherHighlighted = hasHighlight && (highlighted.includes(src.id) || highlighted.includes(tgt.id));

      if (hasHighlight && !eitherHighlighted) {
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 0.5;
      } else if (bothHighlighted) {
        const [r, g, b] = hexToRgb(NODE_COLORS[src.type]);
        ctx.strokeStyle = `rgba(${r},${g},${b},0.7)`;
        ctx.lineWidth = 2;
      } else if (eitherHighlighted) {
        ctx.strokeStyle = 'rgba(255,255,255,0.15)';
        ctx.lineWidth = 1;
      } else {
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 1;
      }

      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();

      // Arrowhead for highlighted edges
      if (bothHighlighted) {
        const dx = tgt.x - src.x;
        const dy = tgt.y - src.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const ex = tgt.x - (dx / dist) * (NODE_RADIUS + 4);
        const ey = tgt.y - (dy / dist) * (NODE_RADIUS + 4);
        const angle = Math.atan2(dy, dx);
        const [r, g, b] = hexToRgb(NODE_COLORS[src.type]);
        ctx.save();
        ctx.translate(ex, ey);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(-8, -4);
        ctx.lineTo(-8, 4);
        ctx.closePath();
        ctx.fillStyle = `rgba(${r},${g},${b},0.7)`;
        ctx.fill();
        ctx.restore();
      }
    });

    // Draw nodes
    nodesRef.current.forEach(node => {
      const isHighlighted = hasHighlight && highlighted.includes(node.id);
      const isDimmed = hasHighlight && !isHighlighted;
      const color = NODE_COLORS[node.type];
      const [r, g, b] = hexToRgb(color);

      if (isHighlighted) {
        // Outer glow
        const pulse = 0.6 + 0.4 * Math.sin(at * 2.5);
        const glowRadius = NODE_RADIUS + 10 + 4 * pulse;
        const grad = ctx.createRadialGradient(node.x, node.y, NODE_RADIUS, node.x, node.y, glowRadius);
        grad.addColorStop(0, `rgba(${r},${g},${b},${0.4 * pulse})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.beginPath();
        ctx.arc(node.x, node.y, glowRadius, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();

        // Ring
        ctx.beginPath();
        ctx.arc(node.x, node.y, NODE_RADIUS + 4 + pulse * 2, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${r},${g},${b},${0.8 * pulse})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // Node background
      ctx.beginPath();
      ctx.arc(node.x, node.y, NODE_RADIUS, 0, Math.PI * 2);
      if (isDimmed) {
        ctx.fillStyle = 'rgba(40,40,50,0.6)';
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      } else if (isHighlighted) {
        ctx.fillStyle = color;
        ctx.strokeStyle = 'rgba(255,255,255,0.4)';
      } else {
        ctx.fillStyle = `rgba(${r},${g},${b},0.2)`;
        ctx.strokeStyle = `rgba(${r},${g},${b},0.5)`;
      }
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();

      // Label
      const labelAlpha = isDimmed ? 0.2 : isHighlighted ? 1 : 0.75;
      ctx.fillStyle = `rgba(255,255,255,${labelAlpha})`;
      ctx.font = `${isHighlighted ? '500' : '400'} 10px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      const words = node.label.split(' ');
      if (words.length === 1) {
        ctx.fillText(node.label, node.x, node.y + NODE_RADIUS + 12);
      } else {
        ctx.fillText(words[0], node.x, node.y + NODE_RADIUS + 10);
        ctx.fillText(words.slice(1).join(' '), node.x, node.y + NODE_RADIUS + 21);
      }
    });

    ctx.restore();

    rafRef.current = requestAnimationFrame(draw);
  }, []);

  // Initialize layout
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const { width, height } = container.getBoundingClientRect();
    const canvas = canvasRef.current!;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d')!;
    ctx.scale(dpr, dpr);

    nodesRef.current = runForceLayout(GRAPH_NODES, width, height);
    setInitialized(true);
    rafRef.current = requestAnimationFrame(draw);

    return () => cancelAnimationFrame(rafRef.current);
  }, [draw]);

  // Handle resize
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      const canvas = canvasRef.current!;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
      const ctx = canvas.getContext('2d')!;
      ctx.scale(dpr, dpr);
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const drag = dragRef.current;
    if (drag.mode === 'pan') {
      transformRef.current.x = drag.startTransX + (e.clientX - drag.startMouseX);
      transformRef.current.y = drag.startTransY + (e.clientY - drag.startMouseY);
      return;
    }
    if (drag.mode === 'node' && drag.nodeId) {
      const t = transformRef.current;
      const node = nodesRef.current.find(n => n.id === drag.nodeId);
      if (node) {
        node.x = drag.startNodeX + (e.clientX - drag.startMouseX) / t.scale;
        node.y = drag.startNodeY + (e.clientY - drag.startMouseY) / t.scale;
      }
      return;
    }

    const node = getNodeAt(x, y);
    setHoveredNode(node);
    setTooltipPos({ x: e.clientX, y: e.clientY });
    if (canvasRef.current) {
      canvasRef.current.style.cursor = node ? 'grab' : 'default';
    }
  }, [getNodeAt]);

  const onMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const node = getNodeAt(x, y);
    const t = transformRef.current;

    if (node) {
      dragRef.current = {
        mode: 'node',
        nodeId: node.id,
        startMouseX: e.clientX,
        startMouseY: e.clientY,
        startNodeX: node.x,
        startNodeY: node.y,
        startTransX: t.x,
        startTransY: t.y,
      };
      if (canvasRef.current) canvasRef.current.style.cursor = 'grabbing';
    } else {
      dragRef.current = {
        mode: 'pan',
        nodeId: null,
        startMouseX: e.clientX,
        startMouseY: e.clientY,
        startNodeX: 0,
        startNodeY: 0,
        startTransX: t.x,
        startTransY: t.y,
      };
      if (canvasRef.current) canvasRef.current.style.cursor = 'grabbing';
    }
  }, [getNodeAt]);

  const onMouseUp = useCallback(() => {
    dragRef.current.mode = 'none';
    if (canvasRef.current) canvasRef.current.style.cursor = 'default';
  }, []);

  const onWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const t = transformRef.current;
    const delta = -e.deltaY * 0.001;
    const newScale = Math.max(0.3, Math.min(3, t.scale * (1 + delta)));
    const scaleRatio = newScale / t.scale;
    t.x = mx - scaleRatio * (mx - t.x);
    t.y = my - scaleRatio * (my - t.y);
    t.scale = newScale;
  }, []);

  const onMouseLeave = useCallback(() => {
    dragRef.current.mode = 'none';
    setHoveredNode(null);
  }, []);

  return (
    <div className="flex flex-col flex-1 min-w-0 relative" style={{ background: '#0f1117' }}>
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b shrink-0" style={{ borderColor: 'rgba(255,255,255,0.08)', background: '#0f1117' }}>
        <span className="text-xs" style={{ color: 'rgba(255,255,255,0.5)' }}>Knowledge Graph · {GRAPH_NODES.length} nodes · {GRAPH_EDGES.length} edges</span>
        {highlightedNodeIds.length > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-xs" style={{ color: 'rgba(255,255,255,0.4)' }}>
              <span style={{ color: '#10b981' }}>{highlightedNodeIds.length}</span> nodes retrieved
            </span>
            <button
              onClick={onReset}
              className="text-xs px-2 py-0.5 rounded border transition-colors"
              style={{ color: 'rgba(255,255,255,0.5)', borderColor: 'rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.04)' }}
            >
              Show all
            </button>
          </div>
        )}
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="flex-1 relative overflow-hidden">
        <canvas
          ref={canvasRef}
          onMouseMove={onMouseMove}
          onMouseDown={onMouseDown}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseLeave}
          onWheel={onWheel}
          className="block"
          style={{ width: '100%', height: '100%' }}
        />

        {/* Tooltip */}
        {hoveredNode && (
          <div
            className="fixed z-50 pointer-events-none max-w-xs rounded-lg p-3 shadow-xl"
            style={{
              left: tooltipPos.x + 14,
              top: tooltipPos.y - 10,
              background: 'rgba(15,17,23,0.95)',
              border: `1px solid ${NODE_COLORS[hoveredNode.type]}55`,
              backdropFilter: 'blur(12px)',
            }}
          >
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full shrink-0" style={{ background: NODE_COLORS[hoveredNode.type] }} />
              <span className="text-xs" style={{ color: NODE_COLORS[hoveredNode.type] }}>{NODE_TYPE_LABELS[hoveredNode.type]}</span>
            </div>
            <div className="text-sm mb-1" style={{ color: 'rgba(255,255,255,0.9)', fontWeight: 500 }}>{hoveredNode.label}</div>
            <div className="text-xs leading-relaxed" style={{ color: 'rgba(255,255,255,0.5)' }}>{hoveredNode.description}</div>
          </div>
        )}

        {/* Loading indicator */}
        {!initialized && (
          <div className="absolute inset-0 flex items-center justify-center" style={{ color: 'rgba(255,255,255,0.3)' }}>
            <span className="text-sm">Running force layout…</span>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 px-4 py-2 shrink-0 flex-wrap" style={{ background: 'rgba(15,17,23,0.9)', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
        {(Object.entries(NODE_COLORS) as [NodeType, string][]).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />
            <span className="text-xs" style={{ color: 'rgba(255,255,255,0.4)' }}>{NODE_TYPE_LABELS[type]}</span>
          </div>
        ))}
        <span className="ml-auto text-xs" style={{ color: 'rgba(255,255,255,0.2)' }}>scroll to zoom · drag to pan</span>
      </div>
    </div>
  );
}
