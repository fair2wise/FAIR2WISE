import type { CSSProperties, RefObject } from 'react';

export interface KGInfoNode {
  id: string;
  label: string;
  type: string;
  description: string;
  color: string;
}

export type KGHoverTarget =
  | { kind: 'node'; node: KGInfoNode }
  | { kind: 'edge'; sourceLabel: string; targetLabel: string; predicate: string }
  | null;

export function KGHoverPopup({
  hoverTarget,
  popupRef,
  style,
}: {
  hoverTarget: Exclude<KGHoverTarget, null>;
  popupRef?: RefObject<HTMLDivElement | null>;
  style?: CSSProperties;
}) {
  return (
    <div
      ref={popupRef}
      className="pointer-events-none absolute z-30 max-w-xs rounded-md border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg backdrop-blur-sm"
      style={style}
    >
      {hoverTarget.kind === 'node' && (
        <>
          <div className="flex min-w-0 items-center gap-2">
            <div
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: hoverTarget.node.color }}
            />
            <span
              className="shrink-0 font-medium"
              style={{ color: hoverTarget.node.color }}
            >
              {hoverTarget.node.type || 'Node'}
            </span>
            <span className="min-w-0 font-medium text-slate-800">{hoverTarget.node.label}</span>
          </div>
          {hoverTarget.node.description && (
            <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap font-semibold leading-relaxed text-slate-700">
              {hoverTarget.node.description}
            </p>
          )}
        </>
      )}

      {hoverTarget.kind === 'edge' && (
        <p className="min-w-0">
          <span className="font-medium text-slate-700">{hoverTarget.sourceLabel}</span>
          <span className="mx-1 font-mono text-[11px] text-sky-600">{hoverTarget.predicate}</span>
          <span className="font-medium text-slate-700">{hoverTarget.targetLabel}</span>
        </p>
      )}
    </div>
  );
}

// Keep export alias for any external imports
export const KGInfoPanel = KGHoverPopup;
