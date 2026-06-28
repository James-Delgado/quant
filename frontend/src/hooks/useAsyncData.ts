import { useEffect, useState } from "react";

/**
 * Minimal async-data hook over the static data client. Owns the three honest
 * states every panel needs — loading, error, ready — and aborts the in-flight
 * request on unmount / dependency change. The frontend never computes data
 * (DECISIONS #1); this only fetches and tracks status.
 */
export type AsyncState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: Error }
  | { status: "ready"; data: T; error: null };

export function useAsyncData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[] = [],
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    status: "loading",
    data: null,
    error: null,
  });

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: "loading", data: null, error: null });
    load(ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setState({ status: "ready", data, error: null });
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        const error = err instanceof Error ? err : new Error(String(err));
        setState({ status: "error", data: null, error });
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
