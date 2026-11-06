import { useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { ApiError, HIDDEN_STATUSES } from './apiHttp';
import { getSelectedGraphSource, setSelectedGraphSource, useGraphSourceState } from './graphSourceStore';
import { useGraphSources } from './graphSourcesHooks';
import type { GraphSourceInfo, GraphSourceListing } from './graphSourcesApi';

/** The value of the "default graph" option: no `source` parameter is sent. */
export const DEFAULT_SOURCE_VALUE = '';

/** Option text: the label plus the node count when the server knows it. */
export function sourceOptionLabel(source: GraphSourceInfo): string {
  const count = source.node_count === null ? '' : ` (${source.node_count.toLocaleString()} nodes)`;
  return `${source.label}${count}`;
}

/** Human readable kind of a source id, for the badge. */
export function sourceKindLabel(id: string | null): string {
  if (id === null) return 'Default';
  return id.startsWith('tiled:') ? 'Tiled' : 'JSON';
}

/** Whether the listing request failed in a way that means "this server has no source listing". */
function isHidden(error: unknown): boolean {
  return error instanceof ApiError && HIDDEN_STATUSES.has(error.status);
}

function groups(listing: GraphSourceListing | undefined) {
  const sources = listing?.sources ?? [];
  return {
    json: sources.filter(source => source.kind === 'json'),
    tiled: sources.filter(source => source.kind === 'tiled'),
  };
}

/**
 * Source selector of the KG viewer: the default graph, the JSON KGs and the Tiled graphs the server lists.
 * Shows the kind of the selected source, a loading spinner, and a clear state when Tiled is unavailable or
 * the graph read failed. `onChange` runs after the choice is stored so the host can reload the graph.
 */
export function GraphSourceSelect({ onChange }: { onChange?: (id: string | null) => void }) {
  const query = useGraphSources();
  const { selected, loading, error } = useGraphSourceState();
  const listing = query.data;

  // A remembered id the server no longer lists (file removed, Tiled graph gone): fall back to the default.
  useEffect(() => {
    if (!listing || listing.tiled.status === 'unavailable') return;
    const current = getSelectedGraphSource();
    if (current !== null && !listing.sources.some(source => source.id === current)) {
      setSelectedGraphSource(null);
      onChange?.(null);
    }
  }, [listing, onChange]);

  if (isHidden(query.error)) return null;
  const { json, tiled } = groups(listing);
  const known = listing?.sources.find(source => source.id === selected) ?? null;
  const tiledDown = listing?.tiled.status === 'unavailable';
  const note = error
    ?? (query.isError ? 'Graph sources unavailable' : null)
    ?? (tiledDown && (selected?.startsWith('tiled:') ?? false) ? 'Tiled unavailable' : null);

  return (
    <div className="inline-flex items-center gap-2" data-testid="graph-source">
      <label className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-500">
        Source
        <select
          aria-label="Graph source"
          title="Graph source"
          value={selected ?? DEFAULT_SOURCE_VALUE}
          onChange={event => {
            const next = event.target.value === DEFAULT_SOURCE_VALUE ? null : event.target.value;
            setSelectedGraphSource(next);
            onChange?.(next);
          }}
          className="max-w-[16rem] bg-transparent text-sm font-medium text-slate-700 outline-none"
        >
          <option value={DEFAULT_SOURCE_VALUE}>Default graph</option>
          {selected !== null && !known ? <option value={selected}>{selected}</option> : null}
          {json.length > 0 ? (
            <optgroup label="JSON KGs">
              {json.map(source => (
                <option key={source.id} value={source.id} disabled={!source.available}>
                  {sourceOptionLabel(source)}
                </option>
              ))}
            </optgroup>
          ) : null}
          {tiled.length > 0 ? (
            <optgroup label="Tiled">
              {tiled.map(source => (
                <option key={source.id} value={source.id} disabled={!source.available}>
                  {sourceOptionLabel(source)}
                </option>
              ))}
            </optgroup>
          ) : null}
          {tiledDown ? (
            <optgroup label="Tiled">
              <option disabled value="__tiled_unavailable">Tiled unavailable</option>
            </optgroup>
          ) : null}
        </select>
      </label>
      <span
        data-testid="graph-source-kind"
        title={selected === null ? 'The graph chosen in Settings' : 'Read-only source'}
        className="inline-flex h-6 items-center rounded-full border border-slate-200 bg-slate-50 px-2 text-[11px] font-medium text-slate-600"
      >
        {sourceKindLabel(selected)}
        {selected !== null ? ' · read-only' : ''}
      </span>
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-500" aria-label="Loading graph source" /> : null}
      {note ? (
        <span role="alert" className="max-w-[18rem] truncate text-xs text-rose-600" title={note}>
          {note}
        </span>
      ) : null}
    </div>
  );
}
