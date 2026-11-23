import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GraphMockup } from './GraphMockup';
import { resetGraphSourceStore } from './graphSourceStore';
import type { GraphPayload } from './data/liveAgent';

/** `count` nodes: the first 40 are hubs with falling degree, every 5th later node is an Unknown placeholder. */
function graphOf(count: number): GraphPayload {
  const nodes = Array.from({ length: count }, (_, i) => ({
    id: `n${i}`,
    label: `Node ${i}`,
    type: i >= 40 && i % 5 === 0 ? 'Unknown' : 'Material',
    description: '',
  }));
  const edges = [];
  for (let i = 40; i < count; i += 1) {
    edges.push({ source: `n${i}`, target: `n${i % 40}`, predicate: 'rel:related_to' });
  }
  return { nodes, edges, source_path: 'source:json:big' };
}

function renderViewer(graph: GraphPayload) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <div style={{ width: 800, height: 600 }}>
        <GraphMockup
          graph={graph}
          highlightedNodeIds={[]}
          isKgViewer
          onToggleKgViewer={() => undefined}
          onKgViewerNodeLimitChange={() => undefined}
          onGraphSourceChange={() => undefined}
        />
      </div>
    </QueryClientProvider>,
  );
}

const nodeElements = () => document.querySelectorAll('[data-kg-node-id]').length;

beforeEach(() => {
  window.localStorage.clear();
  resetGraphSourceStore();
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })));
});
afterEach(() => vi.unstubAllGlobals());

describe('GraphMockup on a large graph', () => {
  it('opens on a 300-node overview with the banner, the N of M header and a consistent Nodes selector', () => {
    renderViewer(graphOf(3_000));
    const banner = screen.getByTestId('kg-overview-banner');
    expect(banner.textContent).toContain('Showing the 300 most connected of 3,000 nodes: search or focus a node to explore, or show more.');
    expect(banner.textContent).toContain('592 Unknown placeholder nodes are hidden');
    expect(screen.getAllByText('of 3,000', { exact: false }).length).toBeGreaterThan(0);
    expect(document.querySelector('[data-kg-node-count]')?.getAttribute('data-kg-node-count')).toBe('300');
    expect(document.querySelector('[data-kg-overview]')?.getAttribute('data-kg-overview')).toBe('true');
    expect(nodeElements()).toBeLessThanOrEqual(300);
    const select = screen.getByLabelText('Nodes to render') as HTMLSelectElement;
    expect(select.value).toBe('300');
    expect(Array.from(select.options).map(option => option.value)).toEqual(['300', '1000', '2000', '2408']);
    expect(screen.getByRole('button', { name: 'Force-directed' })).not.toBeDisabled();
  });

  it('Show more steps 300 -> 1,000 -> 2,000 and keeps the selector and banner in step', () => {
    renderViewer(graphOf(3_000));
    fireEvent.click(screen.getByTestId('kg-overview-show-more'));
    expect(screen.getByTestId('kg-overview-banner').textContent).toContain('Showing the 1,000 most connected of 3,000 nodes');
    expect((screen.getByLabelText('Nodes to render') as HTMLSelectElement).value).toBe('1000');
    expect(document.querySelector('[data-kg-node-count]')?.getAttribute('data-kg-node-count')).toBe('1000');
    fireEvent.click(screen.getByTestId('kg-overview-show-more'));
    expect((screen.getByLabelText('Nodes to render') as HTMLSelectElement).value).toBe('2000');
    expect(screen.queryByTestId('kg-overview-slow-warning')).toBeNull();
    expect(screen.getByRole('button', { name: 'Force-directed' })).not.toBeDisabled();
  });

  it('the Nodes selector drives the same step, and including Unknown placeholders widens the pool', () => {
    renderViewer(graphOf(3_000));
    fireEvent.change(screen.getByLabelText('Nodes to render'), { target: { value: '1000' } });
    expect(screen.getByTestId('kg-overview-banner').textContent).toContain('Showing the 1,000 most connected');
    fireEvent.click(screen.getByTestId('kg-overview-include-unknown'));
    expect(screen.getByTestId('kg-overview-banner').textContent).not.toContain('hidden');
    const options = Array.from((screen.getByLabelText('Nodes to render') as HTMLSelectElement).options).map(option => option.value);
    expect(options).toEqual(['300', '1000', '2000', '3000']);
  });

  it('warns above 2,000 nodes and disables force-directed layout', () => {
    renderViewer(graphOf(3_000));
    // 3,000 nodes less 592 Unknown placeholders: the last option is "All (2,408)", above the 2,000 limit.
    fireEvent.change(screen.getByLabelText('Nodes to render'), { target: { value: '2408' } });
    expect(screen.getByTestId('kg-overview-slow-warning').textContent).toContain('layout will be slow');
    expect(screen.getByRole('button', { name: 'Force-directed' })).toBeDisabled();
    expect(document.querySelector('[data-kg-layout]')?.getAttribute('data-kg-layout')).toBe('existing');
    expect(screen.queryByTestId('kg-overview-show-more')).toBeNull();
    expect(screen.getByTestId('kg-overview-banner').textContent).toContain('Showing all 2,408 of 3,000 nodes');
  }, 30_000);

  it('a small graph is unchanged: no banner, Nodes selector with All, plain node count', () => {
    renderViewer(graphOf(400));
    expect(screen.queryByTestId('kg-overview-banner')).toBeNull();
    const select = screen.getByLabelText('Nodes to render') as HTMLSelectElement;
    expect(select.value).toBe('all');
    expect(Array.from(select.options).map(option => option.value)).toContain('10000');
    expect(document.querySelector('[data-kg-node-count]')?.getAttribute('data-kg-node-count')).toBe('400');
    expect(document.querySelector('[data-kg-overview]')?.getAttribute('data-kg-overview')).toBe('false');
    expect(screen.queryByText('of 400', { exact: false })).toBeNull();
    expect(screen.getByRole('button', { name: 'Force-directed' })).not.toBeDisabled();
  });

  it('a graph at exactly the threshold is not large', () => {
    renderViewer(graphOf(1_500));
    expect(screen.queryByTestId('kg-overview-banner')).toBeNull();
  });
});

describe('default layout of the KG viewer', () => {
  const layout = () => document.querySelector('[data-kg-layout]')?.getAttribute('data-kg-layout');

  it('is force-directed for a small graph and for the 300-node overview of a large one', () => {
    const small = renderViewer(graphOf(400));
    expect(layout()).toBe('force');
    expect(screen.getByRole('button', { name: 'Force-directed' })).toHaveAttribute('aria-pressed', 'true');
    small.unmount();
    renderViewer(graphOf(3_000));
    expect(document.querySelector('[data-kg-node-count]')?.getAttribute('data-kg-node-count')).toBe('300');
    expect(layout()).toBe('force');
  });

  it('falls back to the fixed layout above 2,000 shown nodes, and force stays disabled there', () => {
    renderViewer(graphOf(3_000));
    fireEvent.change(screen.getByLabelText('Nodes to render'), { target: { value: '2408' } });
    expect(layout()).toBe('existing');
    expect(screen.getByRole('button', { name: 'Force-directed' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Nodes to render'), { target: { value: '2000' } });
    expect(layout()).toBe('force');
  });

  it('keeps a layout the user picked', () => {
    renderViewer(graphOf(400));
    fireEvent.click(screen.getByRole('button', { name: 'Fixed Layout' }));
    expect(layout()).toBe('existing');
    expect(screen.getByRole('button', { name: 'Fixed Layout' })).toHaveAttribute('aria-pressed', 'true');
  });
});

