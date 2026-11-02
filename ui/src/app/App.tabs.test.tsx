import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

function stubApi(libraryStatus: number) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://x').pathname;
      if (path === '/v1/library') {
        return new Response(JSON.stringify({ mounted: true, paper_count: 0 }), { status: libraryStatus });
      }
      if (path === '/graph') return new Response(JSON.stringify({ nodes: [], edges: [], source_path: '' }));
      return new Response('{}', { status: 404 });
    }),
  );
}

/** Fresh module graph per test: App owns a module-level QueryClient whose probe result is cached. */
async function renderApp() {
  vi.resetModules();
  const { default: App } = await import('./App');
  return render(<App />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, '', '/');
});

describe('App tabs', { timeout: 30_000 }, () => {
  it('shows Library and Extract links when /v1/library answers', async () => {
    stubApi(200);
    await renderApp();
    expect(await screen.findByRole('link', { name: /Library/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Extract/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('link', { name: /Library/ }));
    expect(await screen.findByRole('heading', { name: 'Library' })).toBeVisible();
  });

  it('hides both tabs when /v1/library is 404 (ALS)', async () => {
    stubApi(404);
    await renderApp();
    expect(await screen.findByPlaceholderText('Ask a domain-specific question...')).toBeInTheDocument();
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(screen.queryByRole('link', { name: /Library/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Extract/ })).not.toBeInTheDocument();
  });

  it('keeps the real chat panel state when switching tabs and back', async () => {
    stubApi(200);
    const user = userEvent.setup();
    await renderApp();
    const box = await screen.findByPlaceholderText('Ask a domain-specific question...');
    // fireEvent: user.type does not focus this input under jsdom (overlay layers), the state path is the same.
    fireEvent.change(box, { target: { value: 'draft question' } });

    await user.click(await screen.findByRole('link', { name: /Extract/ }));
    expect(await screen.findByRole('heading', { name: 'Extraction' })).toBeVisible();
    expect(box).not.toBeVisible();
    await user.click(screen.getAllByRole('link').find(link => link.getAttribute('href') === '/')!);

    expect(box.isConnected).toBe(true);
    expect(box).toBeVisible();
    expect(box).toHaveValue('draft question');
  });
});
