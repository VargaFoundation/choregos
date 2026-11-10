// SPDX-License-Identifier: Apache-2.0
/**
 * What an agent's MCP server entry opens, in words. The broker opens NOTHING for a server named
 * without a pattern (`apps/api/.../services/broker.py`, `tools_of_selection`): "every tool" is `*`,
 * written. Every place that shows a server's tools says so with this one helper (S51-07).
 */
import type { AgentToolGrant } from "@choregos/contracts/api";

export const NO_TOOL_LABEL = "no tool — name tools, or * for all";

/** A tool an agent's MCP server entry grants: a name or pattern, or one with bound arguments (ADR 0128). */
export type ToolGrant = string | AgentToolGrant;

export function grantName(grant: ToolGrant): string {
  return typeof grant === "string" ? grant : grant.name;
}

export function grantNames(tools: readonly ToolGrant[] | null | undefined): string[] {
  return (tools ?? []).map(grantName);
}

/** The names edited as text, each keeping the argument bound it had (ADR 0128): editing the list of
 * names never drops a bound; removing the name does. */
export function keepBounds(previous: readonly ToolGrant[] | null | undefined, names: string[]): ToolGrant[] {
  const bound = new Map((previous ?? []).flatMap((g) => (typeof g === "string" ? [] : [[g.name, g] as const])));
  return names.map((name) => bound.get(name) ?? name);
}

export function toolsLabel(tools: readonly ToolGrant[] | null | undefined): string {
  const named = (tools ?? []).map((g) => (typeof g === "string" ? g : `${g.name} (arguments bound)`));
  return named.length ? named.join(", ") : NO_TOOL_LABEL;
}
