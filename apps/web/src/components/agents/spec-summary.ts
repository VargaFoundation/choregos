// SPDX-License-Identifier: Apache-2.0
import { usd } from "@/lib/format";
import { isDefaultNetwork, networkSummary } from "@/lib/network";
import { effectiveBindings, isLegacyDefault, summaryOf } from "@/lib/memories";
import type { AgentSpec } from "@/lib/types";
import { declaresWorkspace, effectiveWorkspace } from "@/lib/workspace";

/**
 * What a spec says in one line per section (S39-05), kept apart from the builder so the read view of a
 * version (S40-12) can say the same words without loading the whole form.
 */

export type SectionId = "model" | "limits" | "skills" | "tools" | "network" | "knowledge" | "memory" | "evals" | "workspace";

/** Where an agent leaves a component to its project, in the words the console uses everywhere (ADR 0127). */
export const INHERITED = "Inherited from the project";

export type ToolSourceId = "catalogue" | "extensions" | "web";

/**
 * The three tool sources a version may close (ADR 0127). `null` means the field is ABSENT: the source is
 * left to the project. A list, even an empty one, closes it: the run gets what the version names.
 */
export function toolSources(spec: Pick<AgentSpec, "tools">): Array<{ id: ToolSourceId; label: string; named: string[] | null }> {
  return [
    { id: "catalogue", label: "Catalogue", named: spec.tools?.catalogue ?? null },
    { id: "extensions", label: "Extensions", named: spec.tools?.extensions ?? null },
    { id: "web", label: "Web", named: spec.tools?.web ?? null },
  ];
}

/**
 * What a version leaves to its project, as ADR 0127 §7 says it (mirrors `choregos_core.agent_completeness`):
 * a remote (a2a) agent is complete by construction, a voice agent needs only its model. The Access tab
 * says the same from the API for one project; this one reads the spec alone, so a page may say it without one.
 */
export function missingComponents(spec: AgentSpec): string[] {
  const kind = spec.runtime?.kind as string | undefined;
  if (kind === "a2a") return [];
  const missing: string[] = [];
  if (!spec.model?.trim()) missing.push("model");
  if (kind === "voice") return missing;
  for (const source of toolSources(spec)) if (source.named === null) missing.push(`tools.${source.id}`);
  if (inheritsMemory(spec)) missing.push("memories");
  return missing;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function toolsSummary(spec: AgentSpec, servers: number): string {
  const named = toolSources(spec)
    .filter((source) => source.named !== null)
    .map((source) => `${source.id} ${source.named!.length === 0 ? "none" : source.named!.length}`);
  const parts = [servers ? plural(servers, "MCP server", "MCP servers") : null, ...named].filter((part): part is string => part !== null);
  return parts.length ? parts.join(" · ") : INHERITED.toLowerCase();
}

/** A version that says neither `memories` nor the legacy `memory` leaves the memories to its project. */
export const inheritsMemory = (spec: Pick<AgentSpec, "memories" | "memory">): boolean => spec.memories == null && spec.memory == null;

/** One line per folded section: what it holds now, so a closed section still tells. */
export function sectionSummaries(spec: AgentSpec): Record<SectionId, string> {
  const limits = [
    spec.limits?.max_turns != null ? plural(spec.limits.max_turns, "turn", "turns") : null,
    spec.limits?.max_minutes != null ? `${spec.limits.max_minutes} min` : null,
    spec.budget?.run_usd != null ? `${usd(spec.budget.run_usd)} a run` : null,
    spec.budget?.daily_usd != null ? `${usd(spec.budget.daily_usd)} a day` : null,
    spec.budget?.monthly_usd != null ? `${usd(spec.budget.monthly_usd)} a month` : null,
  ].filter((part): part is string => part !== null);
  const skills = (spec.skills ?? []).length;
  const servers = (spec.mcp_servers ?? []).filter((s) => s.connector).length;
  const bases = (spec.knowledge ?? []).length;
  const cases = (spec.evals ?? []).length;
  return {
    model: `${spec.model || INHERITED.toLowerCase()} · ${spec.backend || "the project's backend"}`,
    limits: limits.length ? limits.join(" · ") : "the role's defaults",
    skills: skills ? `${skills} attached` : "none",
    tools: toolsSummary(spec, servers),
    network: networkSummary(spec.network),
    knowledge: bases ? plural(bases, "knowledge base", "knowledge bases") : "none",
    memory: inheritsMemory(spec) ? INHERITED.toLowerCase() : summaryOf(effectiveBindings(spec)),
    evals: plural(cases, "case", "cases"),
    workspace: `${effectiveWorkspace(spec)}${declaresWorkspace(spec) ? "" : " (default)"}`,
  };
}

/** A section starts open when it holds something other than the default. */
export function openByDefault(spec: AgentSpec): Record<SectionId, boolean> {
  const spent = [spec.budget?.run_usd, spec.budget?.daily_usd, spec.budget?.monthly_usd, spec.limits?.max_turns, spec.limits?.max_minutes];
  return {
    model: Boolean(spec.model || spec.backend),
    limits: spent.some((v) => v != null),
    skills: (spec.skills ?? []).length > 0,
    tools: (spec.mcp_servers ?? []).length > 0 || toolSources(spec).some((source) => source.named !== null),
    network: !isDefaultNetwork(spec.network),
    knowledge: (spec.knowledge ?? []).length > 0,
    memory: !isLegacyDefault(effectiveBindings(spec)),
    evals: (spec.evals ?? []).length > 0,
    workspace: spec.workspace != null,
  };
}
