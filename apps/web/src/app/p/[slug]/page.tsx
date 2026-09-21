"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";
import { Card, Label, Stat } from "@varga/design-system";
import { api } from "@/lib/api";
import { eur, percent } from "@/lib/format";
import type { DoraMetric } from "@/lib/types";
import { CostChip, Empty, ErrorNote, StateBadge } from "@/components/ui";

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
  const actifs = stats?.active_work_items ?? 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 border border-line md:grid-cols-4">
        <div className="p-6">
          <Stat value={actifs} label="tickets actifs" tone={actifs > 0 ? "accent" : "ink"} />
        </div>
        <div className="border-l border-line p-6">
          <Stat value={percent(stats?.first_pass_merge_rate)} label="pr mergées au 1er passage" />
        </div>
        <div className="border-t border-line p-6 md:border-t-0 md:border-l">
          <Stat
            value={
              stats?.cycle_time_p50_hours
                ? `${stats.cycle_time_p50_hours.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} h`
                : "—"
            }
            label="cycle time médian"
          />
        </div>
        <div className="border-t border-l border-line p-6 md:border-t-0">
          <Stat value={stats?.trains_pending ?? 0} label="trains en cours" />
        </div>
      </div>

      <Card
        eyebrow="coût"
        title="les 14 derniers jours"
        action={
          <a href={api.costsCsvUrl(slug, "day")} className="text-xs text-ink-muted hover:text-ink" download>
            exporter en csv
          </a>
        }
      >
        {rows.length === 0 ? (
          <Empty>aucune dépense enregistrée.</Empty>
        ) : (
          <>
            <div className="flex h-40 items-end gap-1.5 border-b border-ink" role="img" aria-label="coût par jour">
              {rows.map((row) => (
                <div
                  key={row.key}
                  title={`${row.key} — ${eur(row.cost_eur)} (${row.runs} runs)`}
                  style={{ height: `${Math.max(3, (row.cost_eur / max) * 100)}%` }}
                  className="flex-1 bg-agent transition-opacity hover:opacity-70"
                />
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-x-10 gap-y-3">
              <Stat value={eur(costs.data?.total.cost_eur)} label="total" />
              <Stat
                value={costs.data?.total.budget_usd ? eur(costs.data.total.budget_usd * 0.92) : "—"}
                label="budget quotidien"
              />
            </div>
          </>
        )}
      </Card>

      <Card eyebrow="livraison" title="dora">
        {dora.data === undefined ? (
          <Empty>mesures en cours de calcul.</Empty>
        ) : dora.data.deployments === 0 ? (
          <Empty>aucune mise en production enregistrée sur la fenêtre.</Empty>
        ) : (
          <div className="grid border-t border-line sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="fréquence de déploiement" metric={dora.data.deployment_frequency} />
            <Metric label="délai de livraison" metric={dora.data.lead_time} />
            <Metric label="taux d'échec des changements" metric={dora.data.change_failure_rate} ratio />
            <Metric label="délai de rétablissement" metric={dora.data.time_to_restore} />
          </div>
        )}
      </Card>

      <Card eyebrow="tickets" title="les plus récents" padding="none" className="overflow-hidden">
        <div className="px-6 pb-2">
          <table>
            <thead>
              <tr>
                <th>ticket</th>
                <th>titre</th>
                <th>état</th>
                <th>taille</th>
                <th className="text-right">coût</th>
              </tr>
            </thead>
            <tbody>
              {(items.data?.items ?? []).slice(0, 8).map((item) => (
                <tr key={item.id} className="transition-colors hover:bg-surface-muted">
                  <td className="whitespace-nowrap text-ink-muted">{item.tracker_key}</td>
                  <td>
                    <Link href={`/p/${slug}/items/${item.id}`} className="no-underline hover:underline">
                      {item.title}
                    </Link>
                  </td>
                  <td>
                    <StateBadge state={item.state} display={item.state_display} />
                  </td>
                  <td className="text-ink-muted">{item.size ?? "—"}</td>
                  <td className="text-right">
                    <CostChip costEur={item.totals?.cost_eur} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

const LEVELS: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "ink" }> = {
  elite: { label: "élite", tone: "ok" },
  high: { label: "haut", tone: "ok" },
  medium: { label: "moyen", tone: "warn" },
  low: { label: "bas", tone: "danger" },
};

function Metric({ label, metric, ratio }: { label: string; metric: DoraMetric; ratio?: boolean }) {
  const level = LEVELS[metric.level ?? ""] ?? { label: "sans verdict", tone: "ink" as const };
  const value =
    metric.value === null || metric.value === undefined
      ? "—"
      : ratio
        ? percent(metric.value)
        : `${metric.value.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} ${metric.unit ?? ""}`.trim();
  return (
    <div className="border-line p-5 sm:[&:nth-child(n+2)]:border-l lg:[&:nth-child(n+2)]:border-l">
      <Label>{label}</Label>
      <p className="mt-2 font-display text-2xl leading-none font-bold tracking-tight tabular-nums">{value}</p>
      <p className="mt-3 text-xs text-ink-muted">
        <span
          className={
            { ok: "text-ok", warn: "text-warn", danger: "text-danger", ink: "text-ink-muted" }[level.tone]
          }
        >
          {level.label}
        </span>
        {metric.sample ? ` · ${metric.sample} mesure(s)` : " · pas assez de données"}
      </p>
    </div>
  );
}
