import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AppSettingsButton } from './AppSettingsButton';

// Owner decision 2026-11-09 (Splash packet Q6): the Knowledge graph source descriptions name the editable
// Tiled-backed mode and the read-only JSON mode. The radio ids (`splash_links`, `json`) are the frozen browser
// storage tokens (FRONTEND_COMPATIBILITY.md UI04) and must not change with the text.

function stubSettingsEndpoint() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({
      backend: 'cborg', model: 'lbl/cborg-chat', workflow_mode: 'agentic', extraction_mode: 'targeted',
      graph_source: 'json', available_json_graphs: [], available_cborg_models: [],
    }), { status: 200 })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('AppSettingsButton knowledge graph source', () => {
  it('describes the Tiled-backed editable mode and the read-only JSON mode, keeping the legacy radio ids', async () => {
    stubSettingsEndpoint();
    render(<AppSettingsButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    const editable = await screen.findByRole('radio', { name: /splash_links/ });
    expect(editable).toHaveAttribute('id', 'splash_links');
    expect(editable).toHaveAttribute('value', 'splash_links');
    const readOnly = screen.getByRole('radio', { name: /^JSON/ });
    expect(readOnly).toHaveAttribute('id', 'json');
    expect(readOnly).toHaveAttribute('value', 'json');

    expect(screen.getByText(/Editable knowledge graph backed by Tiled Graph/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only: load MatKG JSON files from storage\/kg/)).toBeInTheDocument();
    expect(screen.queryByText(/requires a running splash server/)).not.toBeInTheDocument();
  });
});
