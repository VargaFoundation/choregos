// SPDX-License-Identifier: Apache-2.0
"use client";

import {
  Bot,
  Brain,
  FileText,
  Globe,
  MoveRight,
  Pencil,
  Search,
  Terminal,
  ToggleRight,
  Trash2,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Badge, Table, TBody, TD, TH, THead, TR, type Tone } from "@/components/ui";
import type { RunActivity, ToolCall, CallStatus } from "@/lib/run-activity";
import { duration } from "@/lib/format";

const ICONES: Record<string, LucideIcon> = {
  read: FileText,
  edit: Pencil,
  delete: Trash2,
  move: MoveRight,
  search: Search,
  execute: Terminal,
  think: Brain,
  fetch: Globe,
  switch_mode: ToggleRight,
};

const TONS: Record<CallStatus, Tone> = { pending: "neutral", in_progress: "running", completed: "succeeded", failed: "failed" };
const LIBELLES: Record<CallStatus, string> = { pending: "Pending", in_progress: "Running", completed: "Done", failed: "Failed" };

const HEURE = new Intl.DateTimeFormat("en-GB", { timeStyle: "medium" });

/** A call's duration: to the nearest second, to the tenth under one second. */
function callDuration(ms: number | undefined): string {
  if (ms === undefined) return "—";
  return ms < 1000 ? `${(ms / 1000).toFixed(1)} s` : duration(ms / 1000);
}

/**
 * A run's activity (S25-02), above the raw journal: each tool call once, its state,
 * its duration, and the refusal that blocked it; then the refusals that nothing links to a call.
 */
export function RunActivityView({ activite }: { activite: RunActivity }) {
  const { calls, refusals, termines, sousAgents } = activite;
  const orphelins = refusals.filter((r) => !r.call || !calls.some((a) => a.id === r.call));
  return (
    <div className="space-y-3" data-testid="run-activity">
      <p className="font-mono text-xs text-ink-muted" data-testid="activity-summary">
        {calls.length} tool call{calls.length === 1 ? "" : "s"} · {termines} finished · {refusals.length} refused
        {sousAgents > 0 ? ` · ${sousAgents} sub-agent${sousAgents === 1 ? "" : "s"}` : ""}
      </p>
      {calls.length > 0 && (
        <div className="max-h-96 overflow-auto">
          <Table aria-label="Tool calls">
            <THead>
              <TR>
                <TH>Started</TH>
                <TH>Tool</TH>
                <TH>Status</TH>
                <TH align="right">Duration</TH>
              </TR>
            </THead>
            <TBody>
              {calls.map((call) => (
                <Row key={call.id} call={call} />
              ))}
            </TBody>
          </Table>
        </div>
      )}
      {orphelins.length > 0 && (
        <ul aria-label="Refusals outside a tool call" className="space-y-1 text-xs">
          {orphelins.map((r, i) => (
            <li key={`${r.target}-${i}`} className="flex flex-wrap items-baseline gap-2">
              <Badge tone="failed">Denied</Badge>
              <span className="font-mono">{r.target}</span>
              <span className="text-ink-muted">{r.reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ call }: { call: ToolCall }) {
  const Icone = call.sousAgent ? Bot : (ICONES[call.genre] ?? Wrench);
  return (
    <TR data-call={call.id} data-status={call.status}>
      <TD className="font-mono text-xs whitespace-nowrap text-ink-muted tabular-nums">{HEURE.format(new Date(call.start))}</TD>
      <TD className="min-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <Icone aria-hidden className="size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
          <span className="truncate" title={call.paths.join(", ") || undefined}>
            {call.title}
          </span>
          <span className="sr-only">, {call.sousAgent ? "sub-agent" : call.genre} </span>
          {call.sousAgent && <Badge tone="agent">Sub-agent</Badge>}{" "}
          {call.refusals && (
            <span title={call.refusals}>
              <Badge tone="failed">Denied</Badge>
            </span>
          )}
        </span>
      </TD>
      <TD>
        <Badge tone={TONS[call.status]}>{LIBELLES[call.status]}</Badge>
      </TD>
      <TD align="right" className="font-mono text-xs whitespace-nowrap tabular-nums">
        {callDuration(call.durationMs)}
      </TD>
    </TR>
  );
}
