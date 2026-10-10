// SPDX-License-Identifier: Apache-2.0
/**
 * Journal ACP virtualisé : 10 000 événements sans saccade, permissions mises en évidence. Il a
 * l'allure d'un historique d'événements (ADR 0043) : des lignes de 32 px, l'heure en mono, un refus
 * en pastille rouge, et des en-têtes de colonne qui restent en haut quand on défile.
 */
"use client";

import { useMemo, useRef, useState } from "react";
import { Input, LEGENDE } from "@/components/ui";
import { cn } from "@/lib/cn";
import { shortDate } from "@/lib/format";
import type { RunEventDto } from "@/lib/types";

const ROW_HEIGHT = 32;
const OVERSCAN = 12;

export function LiveLog({ events, height = 480 }: { events: RunEventDto[]; height?: number }) {
  const [scrollTop, setScrollTop] = useState(0);
  const [filter, setFilter] = useState("");
  const container = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    if (!filter) return events;
    const needle = filter.toLowerCase();
    return events.filter(
      (event) =>
        event.type.toLowerCase().includes(needle) || JSON.stringify(event.payload).toLowerCase().includes(needle),
    );
  }, [events, filter]);

  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visible = rows.slice(start, start + Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">filter the journal</span>
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="filter (permission, dod, result…)"
            className="w-64 min-w-0 max-w-full"
          />
        </label>
        <span className="pb-2 font-mono text-xs tabular-nums text-ink-muted">
          {rows.length} event{rows.length > 1 ? "s" : ""}
        </span>
      </div>
      <div
        ref={container}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        style={{ height }}
        className="raised overflow-auto border border-line font-mono text-xs"
        data-testid="live-log"
      >
        <div
          aria-hidden
          className={cn("sticky top-0 z-10 flex h-8 items-center gap-3 border-b border-l-2 border-line border-l-transparent bg-raised px-3", LEGENDE)}
        >
          <span className="w-10 shrink-0">#</span>
          <span className="w-16 shrink-0">time</span>
          <span className="w-52 shrink-0">event</span>
          <span>detail</span>
        </div>
        <div style={{ height: rows.length * ROW_HEIGHT, position: "relative" }}>
          {visible.map((event, index) => {
            const denied = event.type === "session/request_permission" && event.payload?.allowed === false;
            const isResult = event.type === "run.result";
            return (
              <div
                key={event.seq}
                style={{ position: "absolute", top: (start + index) * ROW_HEIGHT, height: ROW_HEIGHT }}
                className={cn(
                  // Chaque ligne a son filet gauche, transparent sauf un refus ou un résultat : les
                  // colonnes restent alignées. Un filet plutôt qu'un fond teinté : la couleur seule ne
                  // disait pas « refusé » (S23-05).
                  "flex w-full items-center gap-3 border-b border-l-2 border-b-line/50 border-l-transparent px-3 hover:bg-surface-muted",
                  denied && "border-l-failed-solid",
                  isResult && "border-l-succeeded-solid",
                )}
              >
                <span className="w-10 shrink-0 tabular-nums text-ink-muted">{event.seq}</span>
                <span className="w-16 shrink-0 tabular-nums text-ink-muted">{shortDate(event.ts).slice(-5)}</span>
                <span className="flex w-52 shrink-0 items-center gap-1.5">
                  <span className="truncate">{event.type}</span>
                  {denied && (
                    <span className="shrink-0 rounded-full border border-failed-line bg-failed-soft px-1.5 text-[11px] leading-4 font-medium text-failed-ink">
                      denied
                    </span>
                  )}
                </span>
                <span className="truncate text-ink-muted">{summarize(event)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function summarize(event: RunEventDto): string {
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  if (event.type === "session/request_permission") {
    return `${payload.allowed ? "allowed" : "refused"} · ${payload.target ?? ""} — ${payload.reason ?? ""}`;
  }
  if (event.type === "session/update") return String(payload.text ?? JSON.stringify(payload));
  return JSON.stringify(payload);
}
