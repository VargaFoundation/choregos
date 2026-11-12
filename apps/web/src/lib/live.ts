// SPDX-License-Identifier: Apache-2.0
/**
 * Live pages (S59-08): a page listens to its work item's or its project's events (SSE) and refetches
 * what it shows when one arrives, instead of waiting for a reload. The stream carries only signals
 * (`type`, `subject`, `run_id`): what a page shows is always what the API answers, never a payload
 * patched in. Bursts (a run's start, its result, the state that follows) refetch once.
 */
"use client";

import { useQueryClient, type Query } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_BASE, IS_MOCK } from "./api";

/** One refetch for the events that arrive within this many milliseconds. */
export const LIVE_DEBOUNCE_MS = 300;

/**
 * Listens to `path` (an API path that answers SSE) and invalidates the active queries `affected`
 * selects when something happens. Closed when the page goes. A stream that names its events
 * (`event: activity.upsert`) is heard through `events`: an unnamed message and each named one refetch
 * alike. Answers whether the stream is open, so that a page polls while it is not (S64-06).
 */
export function useLiveRefresh(path: string | null, affected: (query: Query) => boolean, events: string[] = []): boolean {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const named = events.join(",");
  useEffect(() => {
    if (!path || IS_MOCK || typeof EventSource === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource(`${API_BASE}${path}`, { withCredentials: true });
    const refresh = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        void queryClient.invalidateQueries({ predicate: affected, refetchType: "active" });
      }, LIVE_DEBOUNCE_MS);
    };
    source.onmessage = refresh;
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    const names = named ? named.split(",") : [];
    for (const name of names) source.addEventListener?.(name, refresh);
    return () => {
      clearTimeout(timer);
      for (const name of names) source.removeEventListener?.(name, refresh);
      source.close();
      setConnected(false);
    };
    // `affected` is defined by the caller for the page's lifetime: a new function on each render
    // must not reopen the connection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, queryClient, named]);
  return connected;
}

/** The queries of a page whose key names `value` (a work item's id, a project's slug). */
export function keyNames(value: string): (query: Query) => boolean {
  return (query) => query.queryKey.some((part) => part === value);
}
