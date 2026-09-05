/* Simple polling hook: fetch now, then every `intervalMs`.
 *
 * Deliberately not SSE/websockets — plain polling is dependency-light,
 * battery-predictable, and matches the dashboard's own goal-events polling
 * model. The fetcher's failures are surfaced, not swallowed, so screens can
 * fall back to the offline cache. */
import { useCallback, useEffect, useRef, useState } from "react";

export type Polled<T> = {
  data: T | null;
  error: string | null;
  refresh: () => void;
};

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
): Polled<T> {
  const [snapshot, setSnapshot] = useState<{
    fetcher: () => Promise<T>;
    data: T | null;
    error: string | null;
  }>({ fetcher, data: null, error: null });
  const refreshCurrent = useRef<() => void>(() => {});
  const refresh = useCallback(() => refreshCurrent.current(), []);

  useEffect(() => {
    // The flag belongs to this fetcher generation, so a later effect cannot
    // revive callbacks from the previous dashboard or from before unmount.
    let current = true;
    let latestRequest = 0;
    let pending = false;
    setSnapshot({ fetcher, data: null, error: null });
    const tick = () => {
      if (!current) return;
      const request = ++latestRequest;
      pending = true;
      Promise.resolve().then(fetcher).then(
        (data) => {
          if (!current || request !== latestRequest) return;
          setSnapshot({ fetcher, data, error: null });
        },
        (e: unknown) => {
          if (!current || request !== latestRequest) return;
          setSnapshot(previous => ({
            fetcher,
            data: previous.fetcher === fetcher ? previous.data : null,
            error: e instanceof Error ? e.message : String(e),
          }));
        },
      ).finally(() => {
        if (request === latestRequest) pending = false;
      });
    };
    refreshCurrent.current = tick;
    tick();
    // Slow automatic polls must be allowed to finish instead of being made
    // obsolete on every interval. Explicit refresh may still supersede one.
    const id = setInterval(() => { if (!pending) tick(); }, intervalMs);
    return () => {
      current = false;
      refreshCurrent.current = () => {};
      clearInterval(id);
    };
  }, [fetcher, intervalMs]);

  // Hide the previous identity's data even in the render before effect cleanup.
  const matches = snapshot.fetcher === fetcher;
  return {
    data: matches ? snapshot.data : null,
    error: matches ? snapshot.error : null,
    refresh,
  };
}
