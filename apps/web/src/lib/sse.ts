/**
 * Abonnement SSE avec reprise : le front reprend après `Last-Event-ID`, donc un
 * rechargement ne perd pas d'événement (§3.4).
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, IS_MOCK } from "./api";

export interface StreamOptions<T> {
  path: string;
  enabled?: boolean;
  initial?: T[];
  parse?: (raw: string) => T;
  mockEvents?: T[];
}

export function useEventStream<T>({
  path,
  enabled = true,
  initial = [],
  parse = (raw) => JSON.parse(raw) as T,
  mockEvents,
}: StreamOptions<T>): { events: T[]; connected: boolean; error: string | null } {
  const [events, setEvents] = useState<T[]>(initial);
  // En mode démo il n'y a pas de connexion à ouvrir : l'état est dérivé, pas posé. Le poser
  // dans l'effet déclenchait un rendu en cascade, ce que `react-hooks/set-state-in-effect`
  // signale à juste titre.
  const [liveConnected, setConnected] = useState(false);
  const connected = IS_MOCK ? enabled : liveConnected;
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(new Set<string>());

  useEffect(() => {
    if (!enabled) return;
    if (IS_MOCK) {
      // En mode démo, les événements arrivent au fil de l'eau pour montrer le direct.
      let index = 0;
      const timer = setInterval(() => {
        const next = mockEvents?.[index];
        if (next === undefined) {
          clearInterval(timer);
          return;
        }
        setEvents((current) => [...current, next]);
        index += 1;
      }, 700);
      return () => clearInterval(timer);
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
        // une ligne illisible ne doit pas casser le flux
      }
    };
    source.onerror = () => {
      setConnected(false);
      setError("connexion interrompue — reprise automatique");
    };
    return () => source.close();
    // `parse` et `mockEvents` sont fournis par l'appelant et stables pour la durée du flux :
    // les inclure relancerait la connexion à chaque rendu.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { events, connected, error };
}
