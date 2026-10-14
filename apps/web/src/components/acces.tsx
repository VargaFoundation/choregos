// SPDX-License-Identifier: Apache-2.0
"use client";

import { Provenance } from "@/components/provenance";
import { Card, Empty, LEGENDE } from "@/components/ui";
import { cn } from "@/lib/cn";

export type AccesRun = {
  evenements: number;
  refusals: number;
  /** décisions dont la nature a été déduite du titre : l'agent n'avait pas dit `kind` */
  deduites?: number;
  tool_cost_eur?: number;
  acces: { nature: string; target: string; requests: number; refusals: number; motifs?: string[] }[];
};

const LIBELLE: Record<string, string> = {
  read: "read",
  write: "write",
  execute: "execute",
  network: "network",
  tool: "tool",
  autre: "other",
};

/**
 * Ce à quoi l'agent a touché.
 *
 * Everything was already logged — every permission, every refusal and its reason — but as a
 * raw ACP log: two agents produce two hundred events in a few minutes, and nobody reads them.
 * A log nobody reads is not an audit.
 *
 * Refusals come first, with their reason: that is what one comes looking for.
 */
export function Acces({ acces }: { acces?: AccesRun }) {
  const lignes = acces?.acces ?? [];
  const refuses = lignes.filter((l) => l.refusals > 0);

  return (
    <Card
      title="Access"
      action={
        <span className="flex items-center gap-3 text-xs text-ink-muted">
          <Provenance de="observed" />
          {acces
            ? `${acces.evenements} events · ${acces.refusals} refusals${acces.deduites ? ` · ${acces.deduites} kinds inferred` : ""}`
            : "—"}
        </span>
      }
    >
      {lignes.length === 0 ? (
        <Empty>No access recorded for this run.</Empty>
      ) : (
        <>
          {refuses.length > 0 && (
            <div className="mb-4 border-l-2 border-failed pl-3">
              <p className={cn(LEGENDE, "mb-1 text-failed-ink")}>Refused</p>
              <ul className="space-y-1 text-sm">
                {refuses.map((ligne) => (
                  <li key={`${ligne.nature}:${ligne.target}`}>
                    <span className="font-mono text-xs">{ligne.target}</span>
                    <span className="text-ink-muted"> — {ligne.motifs?.join(" · ") || "no reason"}</span>
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
                {lignes.map((ligne) => (
                  <tr key={`${ligne.nature}:${ligne.target}`}>
                    <td className="text-xs">{LIBELLE[ligne.nature] ?? ligne.nature}</td>
                    <td>
                      <span className="font-mono text-xs break-all">{ligne.target}</span>
                    </td>
                    <td className="text-xs">
                      {ligne.requests}
                      {ligne.refusals > 0 && <span className="text-danger"> · {ligne.refusals} refused</span>}
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
