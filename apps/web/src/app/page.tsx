"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Card, Eyebrow, Heading, Lead, Stat, buttonClasses } from "@varga/design-system";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { eur, percent, relative } from "@/lib/format";
import { Empty, ErrorNote, StateBadge } from "@/components/ui";

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
          <Eyebrow>organisation {org}</Eyebrow>
          <Heading as="h1" size="xl">
            projects
          </Heading>
          <Lead>each project ties a repository to a workflow: a ticket goes in, a controlled production release comes out.</Lead>
        </div>
        <Link href="/projects/new" className={buttonClasses("primary", "md")}>
          new project
        </Link>
      </header>

      {error && <ErrorNote>{(error as Error).message}</ErrorNote>}
      {isLoading && <Empty>loading…</Empty>}
      {data && projects.length === 0 && (
        <Empty
          title="no project"
          action={
            <Link href="/projects/new" className={buttonClasses("primary", "sm")}>
              create the first one
            </Link>
          }
        >
          a project ties a repository, a tracker and a workflow. provisioning does the rest.
        </Empty>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-2 border border-line md:grid-cols-3">
          <div className="p-6">
            <Stat value={projects.length} label="projects" />
          </div>
          <div className="border-l border-line p-6">
            <Stat value={actifs} label="tickets in flight" tone={actifs > 0 ? "accent" : "ink"} />
          </div>
          <div className="col-span-2 border-t border-line p-6 md:col-span-1 md:border-t-0 md:border-l">
            <Stat value={eur(cout)} label="spent this month" />
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
                <Stat value={project.stats?.active_work_items ?? 0} label="active tickets" />
                <Stat
                  value={eur(project.stats?.cost_month_eur)}
                  label={project.stats?.budget_month_eur ? `of ${eur(project.stats.budget_month_eur)}` : "cost this month"}
                />
                <Stat value={percent(project.stats?.first_pass_merge_rate)} label="prs on first pass" />
                <Stat value={project.stats?.trains_pending ?? 0} label="trains in flight" />
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
