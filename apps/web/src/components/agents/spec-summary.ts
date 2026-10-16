// SPDX-License-Identifier: Apache-2.0
import { usd } from "@/lib/format";
import type { AgentSpec } from "@/lib/types";

/**
 * What a spec says in one line per section (S39-05), kept apart from the builder so the read view of a
 * version (S40-12) can say the same words without loading the whole form.
 */

/** Whose memory a run reads and writes (S32-02); the API's default is the project's. */
export const MEMORY_SCOPES = {
  project: "The project's shared memory",
  agent: "Its own, within the project",
  none: "None",
} as const;

export type SectionId = "model" | "limits" | "skills" | "tools" | "knowledge" | "memory" | "evals";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

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
    model: `${spec.model || "the project's profile"} · ${spec.backend || "the project's backend"}`,
    limits: limits.length ? limits.join(" · ") : "the role's defaults",
    skills: skills ? `${skills} attached` : "none",
    tools: servers ? plural(servers, "MCP server", "MCP servers") : "none",
    knowledge: bases ? plural(bases, "knowledge base", "knowledge bases") : "none",
    memory: MEMORY_SCOPES[spec.memory ?? "project"].toLowerCase(),
    evals: plural(cases, "case", "cases"),
  };
}

/** A section starts open when it holds something other than the default. */
export function openByDefault(spec: AgentSpec): Record<SectionId, boolean> {
  const spent = [spec.budget?.run_usd, spec.budget?.daily_usd, spec.budget?.monthly_usd, spec.limits?.max_turns, spec.limits?.max_minutes];
  return {
    model: Boolean(spec.model || spec.backend),
    limits: spent.some((v) => v != null),
    skills: (spec.skills ?? []).length > 0,
    tools: (spec.mcp_servers ?? []).length > 0,
    knowledge: (spec.knowledge ?? []).length > 0,
    memory: Boolean(spec.memory && spec.memory !== "project"),
    evals: (spec.evals ?? []).length > 0,
  };
}
