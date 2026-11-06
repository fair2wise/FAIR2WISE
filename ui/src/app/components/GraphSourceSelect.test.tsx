import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphSourceSelect, sourceKindLabel, sourceOptionLabel } from './GraphSourceSelect';
import { getSelectedGraphSource, resetGraphSourceStore, setGraphSourceStatus, setSelectedGraphSource } from './graphSourceStore';
import { parseGraphSourceListing } from './graphSourcesApi';

const JSON_ROW = { id: 'json:rsoxs_v1', kind: 'json', label: 'matkg_rsoxs_v1.json', read_only: true, available: true };
const TILED_ROW = { id: 'tiled:e2e_demo', kind: 'tiled', label: 'e2e_demo / v1', read_only: true, available: true,
  node_count: 268, edge_count: 126, revision: 'v1@abc' };

function listing(tiledStatus = 'ok', sources: unknown[] = [JSON_ROW, TILED_ROW]) {
  return { sources, tiled: { status: tiledStatus, detail: null } };
}

function stubListing(body: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

function renderSelect(onChange?: (id: string | null) => void) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <GraphSourceSelect onChange={onChange} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  resetGraphSourceStore();
});
afterEach(() => vi.unstubAllGlobals());

describe('GraphSourceSelect', () => {
  it('lists the default graph, JSON KGs and Tiled graphs in groups, with node counts', async () => {
    stubListing(listing());
    renderSelect();
    const select = await screen.findByRole('combobox', { name: 'Graph source' });
    await waitFor(() => expect(screen.getByRole('option', { name: /e2e_demo \/ v1 \(268 nodes\)/ })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'Default graph' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'matkg_rsoxs_v1.json' })).toBeInTheDocument();
    expect(select.querySelectorAll('optgroup')).toHaveLength(2);
    expect(screen.getByTestId('graph-source-kind')).toHaveTextContent('Default');
  });

  it('stores the choice, shows its kind and read-only state, and reports it to the host', async () => {
    stubListing(listing());
    const onChange = vi.fn();
    renderSelect(onChange);
    const select = await screen.findByRole('combobox', { name: 'Graph source' });
    await waitFor(() => expect(screen.getByRole('option', { name: /e2e_demo/ })).toBeInTheDocument());
    fireEvent.change(select, { target: { value: 'tiled:e2e_demo' } });
    expect(getSelectedGraphSource()).toBe('tiled:e2e_demo');
    expect(window.localStorage.getItem('fair2wise-graph-source-v1')).toBe('tiled:e2e_demo');
    expect(onChange).toHaveBeenCalledWith('tiled:e2e_demo');
    expect(screen.getByTestId('graph-source-kind')).toHaveTextContent('Tiled · read-only');
    fireEvent.change(select, { target: { value: '' } });
    expect(getSelectedGraphSource()).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('shows "Tiled unavailable" when the server reports the Tiled side down', async () => {
    stubListing(listing('unavailable', [JSON_ROW]));
    renderSelect();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Tiled unavailable' })).toBeDisabled());
    expect(screen.queryByRole('alert')).toBeNull();
    setSelectedGraphSource('tiled:e2e_demo');
    expect(await screen.findByRole('alert')).toHaveTextContent('Tiled unavailable');
  });

  it('shows the failure of a graph read for the selected source', async () => {
    stubListing(listing());
    renderSelect();
    await screen.findByRole('combobox', { name: 'Graph source' });
    await waitFor(() => expect(screen.getByRole('option', { name: /e2e_demo/ })).toBeInTheDocument());
    setSelectedGraphSource('tiled:e2e_demo');
    setGraphSourceStatus({ loading: false, error: 'Tiled graph is unavailable' });
    expect(await screen.findByRole('alert')).toHaveTextContent('Tiled graph is unavailable');
    setGraphSourceStatus({ loading: true, error: null });
    expect(await screen.findByLabelText('Loading graph source')).toBeInTheDocument();
  });

  it('does not offer a Tiled group when the deployment has none (not_offered)', async () => {
    stubListing(listing('not_offered', [JSON_ROW]));
    renderSelect();
    const select = await screen.findByRole('combobox', { name: 'Graph source' });
    await waitFor(() => expect(screen.getByRole('option', { name: 'matkg_rsoxs_v1.json' })).toBeInTheDocument());
    expect(select.querySelectorAll('optgroup')).toHaveLength(1);
  });

  it('disables a source the server marks unavailable', async () => {
    stubListing(listing('ok', [{ ...TILED_ROW, available: false }]));
    renderSelect();
    await waitFor(() => expect(screen.getByRole('option', { name: /e2e_demo/ })).toBeDisabled());
  });

  it.each([404, 401, 403])('hides itself when the server has no listing (HTTP %i)', async status => {
    const fetchMock = vi.fn(async () => new Response('{}', { status }));
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderSelect();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it('keeps the default available and says so when the listing cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('down'); }));
    renderSelect();
    expect(await screen.findByRole('alert')).toHaveTextContent('Graph sources unavailable');
    expect(screen.getByRole('option', { name: 'Default graph' })).toBeInTheDocument();
  });

  it('falls back to the default when the remembered source is no longer listed', async () => {
    setSelectedGraphSource('json:gone');
    stubListing(listing());
    const onChange = vi.fn();
    renderSelect(onChange);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(null));
    expect(getSelectedGraphSource()).toBeNull();
  });

  it('keeps a remembered Tiled source while Tiled is down (it may come back)', async () => {
    setSelectedGraphSource('tiled:e2e_demo');
    stubListing(listing('unavailable', [JSON_ROW]));
    const onChange = vi.fn();
    renderSelect(onChange);
    await waitFor(() => expect(screen.getByRole('option', { name: 'Tiled unavailable' })).toBeInTheDocument());
    expect(getSelectedGraphSource()).toBe('tiled:e2e_demo');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('labels', () => {
  it('builds option and kind labels', () => {
    const [tiled] = parseGraphSourceListing(listing('ok', [TILED_ROW])).sources;
    expect(sourceOptionLabel(tiled)).toBe('e2e_demo / v1 (268 nodes)');
    expect(sourceOptionLabel({ ...tiled, node_count: null })).toBe('e2e_demo / v1');
    expect(sourceKindLabel(null)).toBe('Default');
    expect(sourceKindLabel('tiled:x')).toBe('Tiled');
    expect(sourceKindLabel('json:x')).toBe('JSON');
  });
});
