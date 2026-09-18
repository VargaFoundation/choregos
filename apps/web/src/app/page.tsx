"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, DEFAULT_ORG } from "@/lib/api";
import { eur, percent, relative } from "@/lib/format";
import { Card, Empty, ErrorNote, StateBadge } from "@/components/ui";

export default function ProjectsPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["projects"], queryFn: () => api.projects() });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Projets</h1>
          <p className="text-sm text-ink-muted">Organisation {DEFAULT_ORG}</p>
        </div>
        <Link
          href="/projects/new"
          className="rounded border border-agent/40 bg-agent/10 px-3 py-1.5 text-sm text-agent no-underline"
        >
          Nouveau projet
        </Link>
      </div>

      {error && <ErrorNote>{(error as Error).message}</ErrorNote>}
      {isLoading && <Empty>chargement…</Empty>}
      {data?.items.length === 0 && <Empty>aucun projet — commencez par en créer un.</Empty>}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {data?.items.map((project) => (
          <Card
            key={project.id}
            title={
              <Link href={`/p/${project.slug}`} className="text-sm font-semibold no-underline">
                {project.name}
              </Link>
            }
            action={<StateBadge state={project.status} display={project.status} kind={project.status === "active" ? "terminal" : "wait"} />}
          >
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <dt className="text-xs text-ink-muted">Tickets actifs</dt>
                <dd>{project.stats?.active_work_items ?? 0}</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-muted">Coût du mois</dt>
                <dd>
                  {eur(project.stats?.cost_month_eur)}
                  {project.stats?.budget_month_eur ? (
                    <span className="text-ink-muted"> / {eur(project.stats.budget_month_eur)}</span>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-ink-muted">PR au 1er passage</dt>
                <dd>{percent(project.stats?.first_pass_merge_rate)}</dd>
              </div>
              <div>
                <dt className="text-xs text-ink-muted">Trains en cours</dt>
                <dd>{project.stats?.trains_pending ?? 0}</dd>
              </div>
            </dl>
            <p className="mt-3 text-xs text-ink-muted">
              workflow {project.workflow_name ?? "—"} · politique {project.policy_name ?? "—"} · modifié{" "}
              {relative(project.updated_at)}
            </p>
          </Card>
        ))}
      </div>
    </div>
  );
}
