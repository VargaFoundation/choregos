"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { api } from "@/lib/api";
import { eur, percent } from "@/lib/format";
import { Card, Empty, ErrorNote } from "@/components/ui";

export default function ProjectOverview({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const costs = useQuery({ queryKey: ["costs", slug], queryFn: () => api.costs(slug, "day") });
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.workItems(slug) });

  if (project.error) return <ErrorNote>{(project.error as Error).message}</ErrorNote>;
  const stats = project.data?.stats;
  const rows = costs.data?.rows ?? [];
  const max = Math.max(1, ...rows.map((row) => row.cost_eur));

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Débit et qualité">
        <dl className="space-y-2 text-sm">
          <Stat label="Tickets actifs" value={String(stats?.active_work_items ?? 0)} />
          <Stat label="PR mergées au 1er passage" value={percent(stats?.first_pass_merge_rate)} />
          <Stat
            label="Cycle time médian"
            value={stats?.cycle_time_p50_hours ? `${stats.cycle_time_p50_hours.toFixed(1)} h` : "—"}
          />
          <Stat label="Trains en cours" value={String(stats?.trains_pending ?? 0)} />
        </dl>
      </Card>

      <Card title="Coût des 14 derniers jours" className="lg:col-span-2">
        {rows.length === 0 ? (
          <Empty>aucune dépense enregistrée</Empty>
        ) : (
          <>
            <div className="flex h-32 items-end gap-1" role="img" aria-label="Coût par jour">
              {rows.map((row) => (
                <div
                  key={row.key}
                  title={`${row.key} — ${eur(row.cost_eur)} (${row.runs} runs)`}
                  style={{ height: `${Math.max(4, (row.cost_eur / max) * 100)}%` }}
                  className="flex-1 rounded-t bg-agent/60"
                />
              ))}
            </div>
            <p className="mt-2 text-sm text-ink-muted">
              total {eur(costs.data?.total.cost_eur)} · budget quotidien{" "}
              {costs.data?.total.budget_usd ? eur(costs.data.total.budget_usd * 0.92) : "—"}
            </p>
          </>
        )}
      </Card>

      <Card title="Tickets récents" className="lg:col-span-3">
        <table>
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Titre</th>
              <th>État</th>
              <th>Taille</th>
              <th className="text-right">Coût</th>
            </tr>
          </thead>
          <tbody>
            {(items.data?.items ?? []).slice(0, 8).map((item) => (
              <tr key={item.id}>
                <td className="font-mono text-xs">{item.tracker_key}</td>
                <td>{item.title}</td>
                <td>{item.state_display ?? item.state}</td>
                <td>{item.size ?? "—"}</td>
                <td className="text-right">{eur(item.totals?.cost_eur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
