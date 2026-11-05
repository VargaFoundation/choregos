// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { csvUrl, fetchReport, reportKey, type Scope } from "@/components/spend/breakdown";
import { Card, ReadState } from "@/components/ui";
import type { CostFilter } from "@/lib/api";
import { type Currency, amount, money } from "@/lib/spend";

/** The doors a tool call comes through (ADR 0122 §4), always listed, so that a door no call used reads 0. */
const DOORS = [
  { key: "model", label: "model", meaning: "the agent's own tool calls" },
  { key: "script", label: "script", meaning: "calls from a script the agent wrote, through the tool bridge" },
  { key: "job", label: "job", meaning: "calls from a job step" },
] as const;

/** A tool line booked before the door was recorded (S57-06) says none. */
const UNKNOWN = { key: "—", label: "—", meaning: "calls booked before the door was recorded" };

/**
 * The run's tool calls by the door they came through (S57-10): the model, a script, a job step. Read
 * from the ledger's tool lines grouped by `origin`; one line is one call. The door is attribution,
 * never a guard: every call is governed the same way, whatever its door (ADR 0122 §2).
 */
export function ToolCallsByDoorCard({ scope, currency, filtre }: { scope: Scope; currency: Currency; filtre: CostFilter }) {
  const tools: CostFilter = { ...filtre, kind: "tool" };
  const report = useQuery({ queryKey: reportKey(scope, "origin", tools), queryFn: () => fetchReport(scope, "origin", tools) });
  const rows = report.data?.rows ?? [];
  const byKey = new Map(rows.map((row) => [row.key, row]));
  const doors = [...DOORS, ...(byKey.has(UNKNOWN.key) ? [UNKNOWN] : [])];
  const total = rows.reduce((sum, row) => sum + (row.runs ?? 0), 0);
  const title = "Tool calls by door";
  return (
    <Card
      title={title}
      action={
        <a href={csvUrl(scope, "origin", tools)} className="text-xs text-ink-muted hover:text-ink" download>
          CSV
        </a>
      }
    >
      {!report.data ? (
        <ReadState lecture={report} quoi={title.toLowerCase()} />
      ) : (
        <>
          <table aria-label={title} className="w-full text-sm" data-testid="tool-calls-by-door">
            <thead className="sr-only">
              <tr>
                <th scope="col">Door</th>
                <th scope="col">Share of the calls</th>
                <th scope="col">Calls</th>
                <th scope="col">Cost</th>
              </tr>
            </thead>
            <tbody>
              {doors.map((door) => {
                const row = byKey.get(door.key);
                const calls = row?.runs ?? 0;
                const share = total > 0 ? calls / total : 0;
                return (
                  <tr key={door.key} data-door={door.key} className="border-t border-line first:border-t-0">
                    <th scope="row" className="w-2/5 py-1.5 pr-3 text-left font-normal">
                      <span className="font-mono">{door.label}</span>
                      <span className="block text-xs text-ink-muted">{door.meaning}</span>
                    </th>
                    <td className="w-1/4 py-1.5">
                      <div className="h-2 rounded-sm bg-surface-muted" aria-hidden>
                        {calls > 0 && <div className="h-2 rounded-sm bg-agent" style={{ width: `${Math.max(2, share * 100)}%` }} />}
                      </div>
                    </td>
                    <td className="py-1.5 pl-3 text-right tabular-nums">
                      {calls} call{calls === 1 ? "" : "s"}
                    </td>
                    <td className="py-1.5 pl-2 text-right text-xs tabular-nums text-ink-muted">{money(row ? amount(row, currency) : 0, currency)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted">
            Every call is governed the same way, whatever its door: the door says who made it, never what it may do.
          </p>
        </>
      )}
    </Card>
  );
}
