// Test double for hooks/useApi: the page calls useApi(loader); the mocked client returns a marker {__k}, and the state for that marker is read from globalThis.__apiState (set by each test).
export function useApi(loader: (s?: AbortSignal) => any) {
  const marker = loader(undefined);
  const st = ((globalThis as any).__apiState || {})[marker?.__k] || {};
  return { data: st.data ?? null, error: st.error ?? null, loading: false, initialLoading: !!st.initialLoading, refresh: () => {} };
}
