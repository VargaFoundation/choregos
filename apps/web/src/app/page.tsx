// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { hasReleaseTrain } from "@/lib/release-train";
import { useSession } from "@/lib/session";
import { eur, percent, relative } from "@/lib/format";
import { buttonClasses, Card, Empty, ErrorNote, Eyebrow, Heading, Lead, Stat, StateBadge } from "@/components/ui";

export default function ProjectsPage() {
  const { org } = useSession();
  const { data, isLoading, error } = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const projects = data?.items ?? [];
  const actifs = projects.reduce((n, p) => n + (p.stats?.active_work_items ?? 0), 0);
  const cout = projects.reduce((n, p) => n + (p.stats?.cost_month_eur ?? 0), 0);

  return (
    <div className="space-y-12">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="space-y-5">
          <Eyebrow>Organisation {org}</Eyebrow>
          <Heading as="h1" size="xl">
            Projects
          </Heading>
          <Lead>Each project runs its own workflows: a request comes in, agents and people move it on under the rules you set, and every step leaves its evidence.</Lead>
        </div>
        <Link href="/projects/new" className={buttonClasses("primary", "md")}>
          New project
        </Link>
      </header>

      {error && <ErrorNote>{(error as Error).message}</ErrorNote>}
      {isLoading && <Empty>Loading…</Empty>}
      {data && projects.length === 0 && (
        <Empty
          title="No project"
          action={
            <Link href="/projects/new" className={buttonClasses("primary", "sm")}>
              Create the first one
            </Link>
          }
        >
          A project brings together its workflows, the tools they need and the people who decide. Provisioning does the rest.
        </Empty>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-2 border border-line md:grid-cols-3">
          <div className="p-6">
            <Stat value={projects.length} label="Projects" />
          </div>
          <div className="border-l border-line p-6">
            <Stat value={actifs} label="Tickets in flight" tone={actifs > 0 ? "accent" : "ink"} />
          </div>
          <div className="col-span-2 border-t border-line p-6 md:col-span-1 md:border-t-0 md:border-l">
            <Stat value={eur(cout)} label="Spent this month" />
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
              titreDonnee
              action={
                <StateBadge
                  state={project.status}
                  display={project.status}
                  kind={project.status === "active" ? "terminal" : "wait"}
                />
              }
            >
              {/* Not a <dl>: `Stat` renders neither <dt> nor <dd>, and axe rightly refuses it (definition-list). */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-5 border-t border-line pt-5">
                <Stat value={project.stats?.active_work_items ?? 0} label="Active tickets" />
                <Stat
                  value={eur(project.stats?.cost_month_eur)}
                  label={project.stats?.budget_month_eur ? `of ${eur(project.stats.budget_month_eur)}` : "Cost this month"}
                />
                <Stat value={percent(project.stats?.first_pass_merge_rate)} label="PRs on first pass" />
                {hasReleaseTrain(project) && (
                  <Stat value={project.stats?.trains_pending ?? 0} label="Trains in flight" />
                )}
              </div>
              <p className="mt-6 text-xs text-ink-muted">
                <span data-donnee>{project.workflow_name ?? "—"}</span> · {project.policy_name ?? "—"} ·{" "}
                {relative(project.updated_at)}
              </p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
