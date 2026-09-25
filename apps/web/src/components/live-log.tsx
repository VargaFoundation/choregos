/** Journal ACP virtualisé : 10 000 événements sans saccade, permissions mises en évidence. */
"use client";

import { useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { shortDate } from "@/lib/format";
import type { RunEventDto } from "@/lib/types";

const ROW_HEIGHT = 28;
const OVERSCAN = 12;

export function LiveLog({ events, height = 480 }: { events: RunEventDto[]; height?: number }) {
  const [scrollTop, setScrollTop] = useState(0);
  const [filter, setFilter] = useState("");
  const container = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    if (!filter) return events;
    const needle = filter.toLowerCase();
    return events.filter(
      (event) => event.type.toLowerCase().includes(needle) || JSON.stringify(event.payload).toLowerCase().includes(needle),
    );
  }, [events, filter]);

  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visible = rows.slice(start, start + Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          aria-label="filter the journal"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="filter (permission, dod, result…)"
          className="w-64 rounded border border-line bg-surface px-2 py-1 text-xs"
        />
        <span className="text-xs text-ink-muted">
          {rows.length} event{rows.length > 1 ? "s" : ""}
        </span>
      </div>
      <div
        ref={container}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        style={{ height }}
        className="overflow-auto rounded border border-line bg-surface font-mono text-xs"
        data-testid="live-log"
      >
        <div style={{ height: rows.length * ROW_HEIGHT, position: "relative" }}>
          {visible.map((event, index) => {
            const denied = event.type === "session/request_permission" && event.payload?.allowed === false;
            const isResult = event.type === "run.result";
            return (
              <div
                key={event.seq}
                style={{ position: "absolute", top: (start + index) * ROW_HEIGHT, height: ROW_HEIGHT }}
                className={cn(
                  "flex w-full items-center gap-3 border-b border-line/50 px-3",
                  denied && "bg-danger/10 text-danger",
                  isResult && "bg-ok/10 text-ok",
                )}
              >
                <span className="w-10 shrink-0 text-ink-muted">{event.seq}</span>
                <span className="w-16 shrink-0 text-ink-muted">{shortDate(event.ts).slice(-5)}</span>
                <span className="w-52 shrink-0">{event.type}</span>
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
    return `${payload.allowed ? "allowed" : "REFUSED"} · ${payload.target ?? ""} — ${payload.reason ?? ""}`;
  }
  if (event.type === "session/update") return String(payload.text ?? JSON.stringify(payload));
  return JSON.stringify(payload);
}
