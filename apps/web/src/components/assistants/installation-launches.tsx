// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { assistantsApi, type Assistant, type AssistantLaunch } from "@/lib/assistants-api";

type Project = { id: string; slug: string; name?: string | null };

/** Where one launch is bound: a project of the organisation and one of its workflows. */
function LaunchRow({ org, slug, launch, projects }: { org: string; slug: string; launch: AssistantLaunch; projects: Project[] }) {
  const client = useQueryClient();
  const [project, setProject] = useState(launch.project ?? "");
  const [workflow, setWorkflow] = useState(launch.workflow ?? "");
  const [error, setError] = useState<string | null>(null);
  const projectId = projects.find((p) => p.slug === project)?.id;
  const workflows = useQuery({
    queryKey: ["workflows", projectId],
    queryFn: () => api.workflows(projectId ?? ""),
    enabled: !!projectId,
  });
  async function bind(enabled: boolean) {
    setError(null);
    try {
      await assistantsApi.bindLaunch(org, slug, launch.name, { project, workflow, enabled });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["installation", org, slug] }),
        client.invalidateQueries({ queryKey: ["readiness", org, slug] }),
      ]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Binding refused");
    }
  }
  const state = !launch.project ? "Not bound" : launch.enabled ? `Bound to ${launch.workflow} in ${launch.project}` : "Switched off";
  return (
    <div className="space-y-2 rounded border border-line p-3" role="group" aria-label={`Launch ${launch.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          {launch.name} {!launch.required && <span className="text-xs text-ink-muted">(optional)</span>}
        </span>
        <Badge tone={launch.enabled ? "succeeded" : "waiting"}>{state}</Badge>
      </div>
      <p className="text-sm text-ink-muted">{launch.description}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label={`Project for ${launch.name}`}
          value={project}
          onChange={(e) => {
            setProject(e.target.value);
            setWorkflow("");
          }}
        >
          <option value="">Choose a project</option>
          {projects.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name || p.slug}
            </option>
          ))}
        </Select>
        <Select aria-label={`Workflow for ${launch.name}`} value={workflow} onChange={(e) => setWorkflow(e.target.value)} disabled={!projectId}>
          <option value="">Choose a workflow</option>
          {(workflows.data ?? []).map((w) => (
            <option key={w.name} value={w.name}>
              {w.name}
            </option>
          ))}
        </Select>
        <Button size="sm" tone="primary" disabled={!project || !workflow} onClick={() => void bind(true)}>
          Bind
        </Button>
        {!launch.required && launch.project && launch.enabled && (
          <Button size="sm" tone="default" onClick={() => void bind(false)}>
            Switch off
          </Button>
        )}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/**
 * The workflows the assistant may propose to start (ADR 0158), each bound to a project's workflow by its owners.
 * The assistant proposes; the person confirms, and the work item is filed in their name and with their rights.
 */
export function InstallationLaunches({ org, slug, assistant }: { org: string; slug: string; assistant: Assistant }) {
  const launches = assistant.launches ?? [];
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), enabled: launches.length > 0 });
  if (launches.length === 0) return null;
  return (
    <Card title="Workflows it may start" eyebrow="Launches">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          The assistant only proposes: the person confirms in the conversation and the work item is filed in their name, if
          they may create work items in the project. Binding a launch needs that right in the project too.
        </p>
        {launches.map((launch) => (
          <LaunchRow key={launch.name} org={org} slug={slug} launch={launch} projects={(projects.data?.items ?? []) as Project[]} />
        ))}
      </div>
    </Card>
  );
}
