// SPDX-License-Identifier: Apache-2.0
import type { ConnectorDto, WorkflowCatalogueAgent, WorkflowCatalogueEntry } from "@/lib/types";

/** What the deployment provides when a project configures nothing (readiness: `PROVIDED_BY_THE_DEPLOYMENT`). */
export const PROVIDED = new Set(["runtime", "gateway", "memory", "tracker"]);

export type RepositoryFilter = "any" | "without" | "with";

export function sourceLabel(source: string): string {
  if (source === "core") return "Platform";
  if (source === "org") return "Your organisation";
  if (source.startsWith("template:")) return `Template ${source.slice("template:".length)}`;
  if (source.startsWith("plugin:")) return `Plugin ${source.slice("plugin:".length)}`;
  return source;
}

/** The entries a search and the repository filter keep, in the catalogue's order. */
export function filterEntries(entries: WorkflowCatalogueEntry[], query: string, repository: RepositoryFilter): WorkflowCatalogueEntry[] {
  const q = query.trim().toLowerCase();
  return entries.filter((e) => {
    if (repository === "without" && e.needs_repository) return false;
    if (repository === "with" && !e.needs_repository) return false;
    if (!q) return true;
    return [e.name, e.title, e.summary, e.source, ...e.tags].some((text) => text.toLowerCase().includes(q));
  });
}

export type PreviewLine = { capability: string; state: "configured" | "provided" | "missing"; reason: string };

/**
 * The project's readiness as it would be for this entry (ADR 0108 §3), from what the console knows: a
 * connector of that kind, else what the deployment provides, else missing. A preview, not the verdict:
 * the readiness panel says the rest once the workflow is adopted.
 */
export function readinessPreview(entry: WorkflowCatalogueEntry, connectors: ConnectorDto[]): PreviewLine[] {
  const kinds = new Set(connectors.map((c) => c.kind));
  return entry.requires.map((r) => ({
    capability: r.capability,
    state: kinds.has(r.capability) ? "configured" : PROVIDED.has(r.capability) ? "provided" : "missing",
    reason: r.reasons[0] ?? "",
  }));
}

export function unresolved(entry: WorkflowCatalogueEntry): WorkflowCatalogueAgent[] {
  return entry.agents.filter((a) => a.resolution === "unresolved");
}

export const RESOLUTION_TEXT: Record<WorkflowCatalogueAgent["resolution"], string> = {
  org: "in your organisation",
  plugin: "installed by a plugin",
  remote: "remote (A2A)",
  catalogue: "installed on adoption",
  unresolved: "not available: install its plugin first",
};
