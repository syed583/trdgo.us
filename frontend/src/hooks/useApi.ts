import { useCallback, useEffect, useRef, useState } from 'react';
import { AUTH_ERROR, MAINTENANCE_ERROR } from '../api/client';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  /** True only on the first load, so refreshes don't blank the panels. */
  initialLoading: boolean;
  error: string | null;
  refresh: () => void;
  lastUpdated: Date | null;
}

/**
 * Runs an async loader and keeps the previous data visible while refreshing.
 *
 * Options data takes seconds to assemble, so replacing the
 * screen with a spinner on every poll would make it unusable. In-flight
 * requests are aborted when the dependencies change or the component unmounts.
 */
/**
 * The last answer per call site and arguments, kept for the life of the tab.
 *
 * Going back to a page shows what it showed last time immediately, and the
 * fresh request replaces it when it lands -- so moving around the app never
 * waits on the network to draw something.
 */
const MEMO = new Map<string, { data: unknown; at: number }>();
const MEMO_LIMIT = 300;

function memoKey(loader: unknown, deps: unknown[]): string | null {
  try {
    return String(loader) + '|' + JSON.stringify(deps);
  } catch {
    return null;
  }
}

export function useApi<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  options: { refreshMs?: number; enabled?: boolean } = {},
): AsyncState<T> {
  const { refreshMs, enabled = true } = options;

  const key = memoKey(loader, deps);
  const held = key ? MEMO.get(key) : undefined;

  const [data, setData] = useState<T | null>((held?.data as T) ?? null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(enabled && !held);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

  const controllerRef = useRef<AbortController | null>(null);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) {
      setInitialLoading(false);
      return;
    }

    // Per-run cancellation flag (the canonical React data-fetch pattern). Only
    // THIS run's callbacks act, and only while it has not been superseded. Using
    // a shared controllerRef here was the bug behind the Model Performance
    // horizon switch: a succeeded request could land while the ref already
    // pointed elsewhere, so its result was dropped and `loading` stuck true.
    let cancelled = false;
    const controller = new AbortController();
    controllerRef.current = controller;

    // Different arguments on the same screen (another symbol, another tab):
    // show that one's last answer at once if there is one.
    const cached = key ? MEMO.get(key) : undefined;
    if (cached) {
      setData(cached.data as T);
      setInitialLoading(false);
    }
    setLoading(true);
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (key) {
          MEMO.delete(key);
          MEMO.set(key, { data: result, at: Date.now() });
          if (MEMO.size > MEMO_LIMIT) MEMO.delete(MEMO.keys().next().value as string);
        }
        if (cancelled || !mountedRef.current) return;
        setData(result);
        setLastUpdated(new Date());
      })
      .catch((err: Error) => {
        if (cancelled || !mountedRef.current) return;
        if (err.name === 'AbortError') return;
        // A 401 is not an outage -- the session ended. Flag it so the shared
        // error UI shows a sign-in prompt instead of "backend unreachable".
        const status = (err as { status?: number }).status;
        setError(status === 401 ? AUTH_ERROR
          : (status != null && status >= 500) ? MAINTENANCE_ERROR
            : (err.message || 'Request failed'));
      })
      .finally(() => {
        if (cancelled || !mountedRef.current) return;
        setLoading(false);
        setInitialLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  useEffect(() => {
    if (!refreshMs || !enabled) return;
    // Don't poll a tab nobody is looking at: a background Options Flow page
    // ticking every couple of minutes quietly burned the provider's request
    // budget and tripped its rate limit. Skip ticks while hidden, and take one
    // fresh read on the way back so the screen is current when refocused.
    const id = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      setTick((t) => t + 1);
    }, refreshMs);
    const onVisible = () => {
      if (typeof document !== 'undefined' && !document.hidden) setTick((t) => t + 1);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refreshMs, enabled]);

  return { data, loading, initialLoading, error, refresh, lastUpdated };
}
