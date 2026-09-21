"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Card, Eyebrow, Heading, Lead, Stat, buttonClasses } from "@varga/design-system";
import { api, DEFAULT_ORG } from "@/lib/api";
import { eur, percent, relative } from "@/lib/format";
import { Empty, ErrorNote, StateBadge } from "@/components/ui";

export default function ProjectsPage() {
  const { data, isLoading, error } = useQuery({ queryKey: ["projects"], queryFn: () => api.projects() });
  const projects = data?.items ?? [];
  const actifs = projects.reduce((n, p) => n + (p.stats?.active_work_items ?? 0), 0);
  const cout = projects.reduce((n, p) => n + (p.stats?.cost_month_eur ?? 0), 0);

  return (
    <div className="space-y-12">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="space-y-5">
          <Eyebrow>organisation {DEFAULT_ORG}</Eyebrow>
          <Heading as="h1" size="xl">
            projets
          </Heading>
          <Lead>chaque projet relie un dépôt à un workflow : un ticket entre, une mise en production maîtrisée sort.</Lead>
        </div>
        <Link href="/projects/new" className={buttonClasses("primary", "md")}>
          nouveau projet
        </Link>
      </header>

      {error && <ErrorNote>{(error as Error).message}</ErrorNote>}
      {isLoading && <Empty>chargement…</Empty>}
      {data && projects.length === 0 && (
        <Empty
          title="aucun projet"
          action={
            <Link href="/projects/new" className={buttonClasses("primary", "sm")}>
              créer le premier
            </Link>
          }
        >
          un projet relie un dépôt, un tracker et un workflow. le provisioning fait le reste.
        </Empty>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-2 border border-line md:grid-cols-3">
          <div className="p-6">
            <Stat value={projects.length} label="projets" />
          </div>
          <div className="border-l border-line p-6">
            <Stat value={actifs} label="tickets en cours" tone={actifs > 0 ? "accent" : "ink"} />
          </div>
          <div className="col-span-2 border-t border-line p-6 md:col-span-1 md:border-t-0 md:border-l">
            <Stat value={eur(cout)} label="dépensé ce mois" />
          </div>
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        {projects.map((project) => (
          <Link key={project.id} href={`/p/${project.slug}`} className="group block no-underline">
            <Card
              interactive
              className="h-full"
              eyebrow={project.slug}
              title={project.name}
              action={
                <StateBadge
                  state={project.status}
                  display={project.status}
                  kind={project.status === "active" ? "terminal" : "wait"}
                />
              }
            >
              <dl className="grid grid-cols-2 gap-x-6 gap-y-5 border-t border-line pt-5">
                <Stat value={project.stats?.active_work_items ?? 0} label="tickets actifs" />
                <Stat
                  value={eur(project.stats?.cost_month_eur)}
                  label={project.stats?.budget_month_eur ? `sur ${eur(project.stats.budget_month_eur)}` : "coût du mois"}
                />
                <Stat value={percent(project.stats?.first_pass_merge_rate)} label="pr au 1er passage" />
                <Stat value={project.stats?.trains_pending ?? 0} label="trains en cours" />
              </dl>
              <p className="mt-6 text-xs text-ink-muted">
                {project.workflow_name ?? "—"} · {project.policy_name ?? "—"} · {relative(project.updated_at)}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
