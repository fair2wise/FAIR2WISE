import { createContext, useContext, useEffect, useState } from 'react';

const TabActiveContext = createContext(true);

/** Provided by `KeepAliveMainContent`: `false` while a kept-alive tab is mounted but hidden. */
export const TabActiveProvider = TabActiveContext.Provider;

/** Whether the tab this component lives in is the one on screen (`true` outside `KeepAliveMainContent`). */
export function useTabActive(): boolean {
  return useContext(TabActiveContext);
}

/** Whether the browser page is visible (`document.visibilityState`); follows `visibilitychange`. */
export function usePageVisible(): boolean {
  const read = () => (typeof document === 'undefined' ? true : document.visibilityState !== 'hidden');
  const [visible, setVisible] = useState(read);
  useEffect(() => {
    const onChange = () => setVisible(read());
    document.addEventListener('visibilitychange', onChange);
    return () => document.removeEventListener('visibilitychange', onChange);
  }, []);
  return visible;
}

/**
 * Whether polling should run: the tab is on screen and the page is visible. Hidden tabs and background pages
 * stop their timers; when this turns `true` again a poll fetches at once from the kept cursor.
 */
export function useLive(): boolean {
  const tabActive = useTabActive();
  const pageVisible = usePageVisible();
  return tabActive && pageVisible;
}
