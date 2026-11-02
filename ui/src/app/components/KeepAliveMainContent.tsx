import { useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router';

/** One tab of `KeepAliveMainContent` (the subset of a Finch route it needs). */
export interface KeepAliveRoute {
  path: string;
  element: ReactNode;
  /** Transparent background with white text (the chat/graph view), as in Finch's `HubMainContent`. */
  isBackgroundTransparent?: boolean;
}

/**
 * Drop-in for Finch's `HubMainContent` that keeps every visited tab mounted.
 *
 * Finch renders `<Routes>`, which unmounts the previous route's element, so switching to Library or
 * Extract would discard the chat panel's local state (draft, an in-flight stream, panel selection).
 * Here each route is mounted on first visit and then only hidden (`hidden`), so nothing is lost.
 * Markup and classes match `HubMainContent`.
 *
 * @param props.routes - Tabs, matched by exact `path` against the router location.
 * @param props.className - Extra classes for the `<main>` element.
 */
export function KeepAliveMainContent({ routes, className }: { routes: KeepAliveRoute[]; className?: string }) {
  const { pathname } = useLocation();
  const normalized = pathname.replace(/\/+$/, '') || '/';
  const [visited, setVisited] = useState<ReadonlySet<string>>(() => new Set([normalized]));

  useEffect(() => {
    setVisited(prev => (prev.has(normalized) ? prev : new Set(prev).add(normalized)));
  }, [normalized]);

  return (
    <main className={['bg-sky-900 h-full w-full p-8 overflow-y-auto', className].filter(Boolean).join(' ')}>
      {routes.map(route => {
        const active = route.path === normalized;
        if (!active && !visited.has(route.path)) return null;
        return (
          <section
            key={route.path}
            hidden={!active}
            data-testid={`tab-${route.path}`}
            className={`${route.isBackgroundTransparent ? 'bg-transparent text-white' : 'bg-white'} w-full h-full rounded-md`}
          >
            {route.element}
          </section>
        );
      })}
    </main>
  );
}
