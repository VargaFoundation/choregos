// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge, Card, ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { toSentenceCase } from "@/lib/format";
import { isProjectKind, kindLabel, PROJECT_KINDS } from "@/lib/connector-kinds";
import type { ConnectorDto, OrgConnector, ProjectDto } from "@/lib/types";

/**
 * The organisation's connectors fall into two families (review of 7 Oct: "it mixes
 * agents, MCPs and API connectors"): the business systems, which DECLARE their
 * operations, and the MCP servers, whose tools are DISCOVERED. A supplier's agent served
 * over MCP is, for Choregos, an MCP server.
 */
export function groupConnectors(instances: OrgConnector[]): { metier: OrgConnector[]; mcp: OrgConnector[] } {
  return {
    metier: instances.filter((instance) => instance.kind !== "mcp"),
    mcp: instances.filter((instance) => instance.kind === "mcp"),
  };
}

export type DeliveryRow = {
  slug: string;
  name: string;
  tools: { kind: string; type: string; status: string }[] | null;
  refusal: string | null;
};

/** One row per project: its configured delivery tools, in the order of the requirements, or why they are not seen. */
export function deliveryRows(
  projects: Pick<ProjectDto, "slug" | "name">[],
  lectures: { data?: ConnectorDto[]; error?: unknown }[],
): DeliveryRow[] {
  return projects.map((project, index) => {
    const lecture = lectures[index] ?? {};
    if (lecture.error) {
      const interdit = lecture.error instanceof ApiError && lecture.error.status === 403;
      return { slug: project.slug, name: project.name, tools: null, refusal: interdit ? "No access" : "Could not read" };
    }
    if (!lecture.data) return { slug: project.slug, name: project.name, tools: null, refusal: null };
    const tools = lecture.data
      .filter((connector) => isProjectKind(connector.kind))
      .sort((a, b) => PROJECT_KINDS.indexOf(a.kind as never) - PROJECT_KINDS.indexOf(b.kind as never))
      .map((connector) => ({ kind: connector.kind, type: connector.type, status: connector.status ?? "unknown" }));
    return { slug: project.slug, name: project.name, tools: tools, refusal: null };
  });
}

/** Each project's delivery tools, read-only: they are set in the project's settings. */
export function DeliveryTools({ org }: { org: string }) {
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const list = projects.data?.items ?? [];
  const lectures = useQueries({
    queries: list.map((project) => ({
      queryKey: ["connectors", project.slug],
      queryFn: () => api.connectors(project.slug),
      retry: false,
    })),
  });
  const rows = deliveryRows(list, lectures);
  return (
    <Card title="Project delivery tools — configured in each project">
      <div className="space-y-3 text-sm" id="delivery">
        <p className="max-w-3xl text-ink-muted">
          Each project&apos;s tracker, repository, CI and deployment — set in the project&apos;s settings, never here. A
          kind a project does not configure falls back to the platform default.
        </p>
        {projects.error ? (
          <ErrorNote>{String(projects.error)}</ErrorNote>
        ) : !projects.data ? (
          <p className="text-ink-muted">Reading the projects…</p>
        ) : rows.length === 0 ? (
          <p className="text-ink-muted">No project yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full" data-testid="delivery-tools">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">Project</th>
                  <th className="py-1 font-normal">Configured tools</th>
                  <th className="py-1 font-normal">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.slug} className="border-t border-line" data-testid={`delivery-${row.slug}`}>
                    <td className="py-2 font-medium">{row.name}</td>
                    <td className="py-2">
                      {row.refusal ? (
                        <span className="text-xs text-ink-muted">{row.refusal}</span>
                      ) : !row.tools ? (
                        <span className="text-xs text-ink-muted">Reading…</span>
                      ) : row.tools.length === 0 ? (
                        <span className="text-xs text-ink-muted">Platform defaults only</span>
                      ) : (
                        <span className="flex flex-wrap gap-2">
                          {row.tools.map((tool) => (
                            <span key={tool.kind} title={`Status: ${tool.status}`}>
                              <Badge tone={tool.status === "error" ? "danger" : "neutral"}>
                                {toSentenceCase(kindLabel(tool.kind))}: {tool.type}
                              </Badge>
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      <Link href={`/p/${row.slug}/settings`} className="text-xs underline">
                        Settings
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {projects.data?.meta?.has_more && (
          <p className="text-xs text-ink-muted">And more projects: see the projects page.</p>
        )}
      </div>
    </Card>
  );
}
