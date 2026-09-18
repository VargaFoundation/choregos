"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { api } from "@/lib/api";
import { eur, percent } from "@/lib/format";
import type { DoraMetric } from "@/lib/types";
import { Card, Empty, ErrorNote } from "@/components/ui";

export default function ProjectOverview({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const costs = useQuery({ queryKey: ["costs", slug], queryFn: () => api.costs(slug, "day") });
  const items = useQuery({ queryKey: ["items", slug], queryFn: () => api.workItems(slug) });
  const dora = useQuery({ queryKey: ["dora", slug], queryFn: () => api.dora(slug) });

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
            <p className="mt-2 flex flex-wrap items-baseline gap-x-2 text-sm text-ink-muted">
              <span>
                total {eur(costs.data?.total.cost_eur)} · budget quotidien{" "}
                {costs.data?.total.budget_usd ? eur(costs.data.total.budget_usd * 0.92) : "—"}
              </span>
              <a
                href={api.costsCsvUrl(slug, "day")}
                className="underline underline-offset-2 hover:text-ink"
                download
              >
                exporter en CSV
              </a>
            </p>
          </>
        )}
      </Card>

      <Card title="Livraison (DORA)" className="lg:col-span-3">
        {dora.data === undefined ? (
          <Empty>mesures en cours de calcul</Empty>
        ) : dora.data.deployments === 0 ? (
          <Empty>aucune mise en production enregistrée sur la fenêtre</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Fréquence de déploiement" metric={dora.data.deployment_frequency} />
            <Metric label="Délai de livraison" metric={dora.data.lead_time} />
            <Metric label="Taux d'échec des changements" metric={dora.data.change_failure_rate} ratio />
            <Metric label="Délai de rétablissement" metric={dora.data.time_to_restore} />
          </div>
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

type LevelStyle = { label: string; className: string };

const UNKNOWN: LevelStyle = { label: "sans verdict", className: "text-ink-muted" };

const LEVELS: Record<string, LevelStyle> = {
  elite: { label: "élite", className: "text-ok" },
  high: { label: "haut", className: "text-ok" },
  medium: { label: "moyen", className: "text-warn" },
  low: { label: "bas", className: "text-danger" },
  unknown: UNKNOWN,
};

function Metric({ label, metric, ratio }: { label: string; metric: DoraMetric; ratio?: boolean }) {
  const level = LEVELS[metric.level ?? "unknown"] ?? UNKNOWN;
  const value =
    metric.value === null || metric.value === undefined
      ? "—"
      : ratio
        ? percent(metric.value)
        : metric.value.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
  return (
    <div className="rounded border border-line bg-surface-muted p-3">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="mt-1 text-xl font-medium">
        {value} <span className="text-xs text-ink-muted">{ratio ? "" : metric.unit}</span>
      </p>
      <p className={`mt-1 text-xs ${level.className}`}>
        {level.label}
        {metric.sample ? ` · ${metric.sample} mesure(s)` : " · pas assez de données"}
      </p>
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
