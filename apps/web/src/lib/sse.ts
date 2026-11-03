// SPDX-License-Identifier: Apache-2.0
/**
 * SSE subscription with resume: the front end resumes after `Last-Event-ID`, so a reload loses no
 * event (§3.4).
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, IS_MOCK } from "./api";

export interface StreamOptions<T> {
  path: string;
  enabled?: boolean;
  initial?: T[];
  parse?: (raw: string) => T;
  /** In demo mode only: LOADS the events to replay. A function, so that the fixtures stay out of
   * the real bundle: imported statically, they went to every user with the page that cites them
   * (#179). */
  mockEvents?: () => Promise<T[]>;
}

export function useEventStream<T>({
  path,
  enabled = true,
  initial = [],
  parse = (raw) => JSON.parse(raw) as T,
  mockEvents,
}: StreamOptions<T>): { events: T[]; connected: boolean; error: string | null } {
  const [events, setEvents] = useState<T[]>(initial);
  // In demo mode there is no connection to open: the state is derived, not set. Setting it in the
  // effect triggered a cascading render, which `react-hooks/set-state-in-effect` rightly flags.
  const [liveConnected, setConnected] = useState(false);
  const connected = IS_MOCK ? enabled : liveConnected;
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (!enabled) return;
    if (IS_MOCK) {
      // In demo mode, the events arrive as time goes by to show the live stream.
      let index = 0;
      let timer: ReturnType<typeof setInterval> | undefined;
      let stopped = false;
      void (mockEvents?.() ?? Promise.resolve([] as T[])).then((replayed) => {
        if (stopped) return;
        timer = setInterval(() => {
          const next = replayed[index];
          if (next === undefined) {
            clearInterval(timer);
            return;
          }
          setEvents((current) => [...current, next]);
          index += 1;
        }, 700);
      });
      return () => {
        stopped = true;
        clearInterval(timer);
      };
    }
    const source = new EventSource(`${API_BASE}${path}`, { withCredentials: true });
    source.onopen = () => {
      setConnected(true);
      setError(null);
    };
    source.onmessage = (message) => {
      if (message.lastEventId && seen.current.has(message.lastEventId)) return;
      if (message.lastEventId) seen.current.add(message.lastEventId);
      try {
        setEvents((current) => [...current, parse(message.data)]);
      } catch {
        // an unreadable line must not break the stream
      }
    };
    source.onerror = () => {
      setConnected(false);
      setError("connection lost — reconnecting");
    };
    return () => source.close();
    // `parse` and `mockEvents` are supplied by the caller and stable for the stream's lifetime:
    // including them would restart the connection on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { events, connected, error };
}
