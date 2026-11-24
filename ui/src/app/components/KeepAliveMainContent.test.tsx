import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { Link, MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { ExtractionTab } from './ExtractionTab';
import { KeepAliveMainContent } from './KeepAliveMainContent';
import { useTabActive } from './tabActivity';
import { LibraryTab } from './LibraryTab';

/** Stand-in for the chat panel: local draft state plus a counter that lives in the component. */
function ChatStub() {
  const [draft, setDraft] = useState('');
  return (
    <label>
      Chat draft
      <input value={draft} onChange={event => setDraft(event.target.value)} />
    </label>
  );
}

function renderTabs(tabs: { library: boolean }) {
  const routes = [
    { path: '/', element: <ChatStub />, isBackgroundTransparent: true },
    ...(tabs.library
      ? [
          { path: '/library', element: <LibraryTab /> },
          { path: '/extract', element: <ExtractionTab /> },
        ]
      : []),
  ];
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={['/']}>
      <nav>
        <Link to="/">chat</Link>
        <Link to="/library">library</Link>
        <Link to="/extract">extract</Link>
      </nav>
      <KeepAliveMainContent routes={routes} />
    </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('KeepAliveMainContent', () => {
  it('keeps chat state (a typed draft) when switching to Library and Extract and back', async () => {
    const user = userEvent.setup();
    renderTabs({ library: true });
    await user.type(screen.getByLabelText('Chat draft'), 'what is RSoXS?');

    await user.click(screen.getByText('library'));
    expect(screen.getByRole('heading', { name: 'Library' })).toBeVisible();
    expect(screen.getByTestId('tab-/')).not.toBeVisible();

    await user.click(screen.getByText('extract'));
    expect(screen.getByRole('heading', { name: 'Extraction' })).toBeVisible();

    await user.click(screen.getByText('chat'));
    expect(screen.getByLabelText('Chat draft')).toBeVisible();
    expect(screen.getByLabelText('Chat draft')).toHaveValue('what is RSoXS?');
  });

  it('mounts a tab lazily on first visit', async () => {
    const user = userEvent.setup();
    renderTabs({ library: true });
    expect(screen.queryByRole('heading', { name: 'Library' })).not.toBeInTheDocument();
    await user.click(screen.getByText('library'));
    expect(screen.getByRole('heading', { name: 'Library' })).toBeInTheDocument();
  });

  it('renders nothing for a tab that is not registered (probe hid it)', async () => {
    const user = userEvent.setup();
    renderTabs({ library: false });
    await user.click(screen.getByText('library'));
    expect(screen.queryByRole('heading', { name: 'Library' })).not.toBeInTheDocument();
  });
});

describe('KeepAliveMainContent tab activity', () => {
  it('tells a tab whether it is on screen, so a hidden tab can stop polling', async () => {
    const user = userEvent.setup();
    function Probe({ name }: { name: string }) {
      return <p data-testid={`probe-${name}`}>{useTabActive() ? 'active' : 'hidden'}</p>;
    }
    render(
      <MemoryRouter initialEntries={['/a']}>
        <nav>
          <Link to="/a">to a</Link>
          <Link to="/b">to b</Link>
        </nav>
        <KeepAliveMainContent routes={[
          { path: '/a', element: <Probe name="a" /> },
          { path: '/b', element: <Probe name="b" /> },
        ]} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId('probe-a')).toHaveTextContent('active');
    await user.click(screen.getByText('to b'));
    expect(screen.getByTestId('probe-a')).toHaveTextContent('hidden');
    expect(screen.getByTestId('probe-b')).toHaveTextContent('active');
    await user.click(screen.getByText('to a'));
    expect(screen.getByTestId('probe-a')).toHaveTextContent('active');
    expect(screen.getByTestId('probe-b')).toHaveTextContent('hidden');
  });
});
