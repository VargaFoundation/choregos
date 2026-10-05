// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { use, useState } from "react";
import { Card, Empty, StateBadge } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { shortDate } from "@/lib/format";

const TONS: Record<string, "wait" | "work" | "terminal" | "blocked"> = {
  pending_approval: "wait",
  approved: "work",
  running: "work",
  succeeded: "terminal",
  failed: "blocked",
  rejected: "blocked",
  expired: "blocked",
};

/**
 * Les propositions d'action du projet : ce que des agents — ou des personnes, par la console ou par
 * leur client MCP — demandent de faire sur des objets, et qui attend une décision humaine.
 *
 * Avant le 2026-10-05, une proposition ne se décidait que par l'API : aucun écran (ADR 0030).
 */
export default function ProposalsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const [toutes, setToutes] = useState(false);
  const propositions = useQuery({
    queryKey: ["proposals", slug, toutes],
    queryFn: () => api.proposals(slug, toutes ? undefined : "pending_approval"),
    retry: false,
  });
  if (propositions.error instanceof ApiError && propositions.error.status === 404) {
    return (
      <Empty title="no ontology here">
        proposals come from the ontology of a project; this platform does not run the ontology plugin.
      </Empty>
    );
  }
  const lignes = propositions.data ?? [];
  return (
    <Card
      title={toutes ? "all proposals" : "proposals waiting for a decision"}
      action={
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={toutes} onChange={(e) => setToutes(e.target.checked)} />
          <span>show all</span>
        </label>
      }
    >
      <table aria-label="proposals">
        <thead>
          <tr>
            <th>action</th>
            <th>target</th>
            <th>proposed by</th>
            <th>when</th>
            <th>status</th>
          </tr>
        </thead>
        <tbody>
          {lignes.map((p) => (
            <tr key={p.proposal}>
              <td>
                <Link href={`/p/${slug}/proposals/${p.proposal}`} className="font-mono text-sm">
                  {p.action_type}
                </Link>
              </td>
              <td className="text-xs">{p.target.join(", ") || "—"}</td>
              <td className="text-xs">
                {p.proposed_by.id}
                {p.proposed_by.via ? ` · via ${p.proposed_by.via}` : ""}
              </td>
              <td className="text-xs text-ink-muted">{p.created_at ? shortDate(p.created_at) : "—"}</td>
              <td>
                <StateBadge state={p.status} display={p.status.replace("_", " ")} kind={TONS[p.status] ?? "work"} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {lignes.length === 0 && <Empty>{toutes ? "no proposal yet" : "nothing waits for a decision"}</Empty>}
    </Card>
  );
}
