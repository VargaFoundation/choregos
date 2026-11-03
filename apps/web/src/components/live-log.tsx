// SPDX-License-Identifier: Apache-2.0
/**
 * Virtualised ACP journal: 10,000 events without a stutter, permissions highlighted. It has
 * the look of an event history (ADR 0043): 32 px rows, the time in mono, a refusal
 * as a red pill, and column headers that stay on top when scrolling.
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
          <span className="text-xs text-ink-muted">Filter the journal</span>
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter (permission, dod, result…)"
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
          <span className="w-16 shrink-0">Time</span>
          <span className="w-52 shrink-0">Event</span>
          <span>Detail</span>
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
                  // Each row has its left rule, transparent except for a refusal or a result: the
                  // columns stay aligned. A rule rather than a tinted background: colour alone did not
                  // say "refused" (S23-05).
                  "flex w-full items-center gap-3 border-b border-l-2 border-b-line/50 border-l-transparent px-3 hover:bg-surface-muted",
                  denied && "border-l-failed",
                  isResult && "border-l-succeeded",
                )}
              >
                <span className="w-10 shrink-0 tabular-nums text-ink-muted">{event.seq}</span>
                <span className="w-16 shrink-0 tabular-nums text-ink-muted">{shortDate(event.ts).slice(-5)}</span>
                <span className="flex w-52 shrink-0 items-center gap-1.5">
                  <span className="truncate">{event.type}</span>
                  {denied && (
                    <span className="shrink-0 rounded-full border border-failed-line bg-failed-soft px-1.5 text-[11px] leading-4 font-medium text-failed-ink">
                      Denied
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
    return `${payload.allowed ? "Allowed" : "Refused"} · ${payload.target ?? ""} — ${payload.reason ?? ""}`;
  }
  if (event.type === "session/update") return summariseUpdate(payload);
  // The runner journals the whole result (#410); one line says its status and summary.
  if (event.type === "run.result") return `${payload.status ?? ""} · ${payload.summary ?? ""}`;
  return JSON.stringify(payload);
}

/** An ACP update in one line: a message's text, a plan's state, the JSON otherwise. */
function summariseUpdate(payload: Record<string, unknown>): string {
  const update = (payload.update ?? payload) as Record<string, unknown>;
  const genre = String(update.sessionUpdate ?? "");
  const content = update.content as { text?: unknown } | undefined;
  if ((genre === "agent_message_chunk" || genre === "agent_thought_chunk") && typeof content?.text === "string") {
    return genre === "agent_thought_chunk" ? `(thinking) ${content.text}` : content.text;
  }
  if (genre === "tool_call" || genre === "tool_call_update") {
    const title = typeof update.title === "string" ? update.title : String(update.toolCallId ?? "");
    return [title, typeof update.status === "string" ? update.status : null].filter(Boolean).join(" · ");
  }
  if (genre === "plan" && Array.isArray(update.entries)) {
    const entries = update.entries as { status?: unknown }[];
    return `Plan · ${entries.filter((e) => e.status === "completed").length} of ${entries.length} done`;
  }
  return String(payload.text ?? JSON.stringify(payload));
}
