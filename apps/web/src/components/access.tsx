// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Card, Empty, LEGENDE } from "@/components/ui";
import { cn } from "@/lib/cn";

export type RunAccess = {
  events: number;
  refusals: number;
  /** decisions whose kind was inferred from the title: the agent did not state `kind` */
  inferred?: number;
  tool_cost_eur?: number;
  access: { nature: string; target: string; requests: number; refusals: number; reasons?: string[] }[];
};

const LIBELLE: Record<string, string> = {
  read: "read",
  write: "write",
  execute: "execute",
  network: "network",
  tool: "tool",
  other: "other",
};

/**
 * What the agent touched.
 *
 * Everything was already logged — every permission, every refusal and its reason — but as a
 * raw ACP log: two agents produce two hundred events in a few minutes, and nobody reads them.
 * A log nobody reads is not an audit.
 *
 * Refusals come first, with their reason: that is what one comes looking for.
 */
export function Access({ acces }: { acces?: RunAccess }) {
  const rows = acces?.access ?? [];
  const refuses = rows.filter((l) => l.refusals > 0);

  return (
    <Card
      title="Access"
      action={
        <span className="flex items-center gap-3 text-xs text-ink-muted">
          <Provenance de="observed" />
          {acces
            ? `${acces.events} events · ${acces.refusals} refusals${acces.inferred ? ` · ${acces.inferred} kinds inferred` : ""}`
            : "—"}
        </span>
      }
    >
      {rows.length === 0 ? (
        <Empty>No access recorded for this run.</Empty>
      ) : (
        <>
          {refuses.length > 0 && (
            <div className="mb-4 border-l-2 border-failed pl-3">
              <p className={cn(LEGENDE, "mb-1 text-failed-ink")}>Refused</p>
              <ul className="space-y-1 text-sm">
                {refuses.map((row) => (
                  <li key={`${row.nature}:${row.target}`}>
                    <span className="font-mono text-xs">{row.target}</span>
                    <span className="text-ink-muted"> — {row.reasons?.join(" · ") || "no reason"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Target</th>
                  <th>Requests</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.nature}:${row.target}`}>
                    <td className="text-xs">{LIBELLE[row.nature] ?? row.nature}</td>
                    <td>
                      <span className="font-mono text-xs break-all">{row.target}</span>
                    </td>
                    <td className="text-xs">
                      {row.requests}
                      {row.refusals > 0 && <span className="text-danger"> · {row.refusals} refused</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {acces?.tool_cost_eur ? (
            <p className="mt-2 text-xs text-ink-muted">Catalogue tools: {acces.tool_cost_eur.toFixed(4)} €</p>
          ) : null}
        </>
      )}
    </Card>
  );
}
