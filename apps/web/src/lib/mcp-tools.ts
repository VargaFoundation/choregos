// SPDX-License-Identifier: Apache-2.0
/**
 * What an agent's MCP server entry opens, in words. The broker opens NOTHING for a server named
 * without a pattern (`apps/api/.../services/broker.py`, `tools_of_selection`): "every tool" is `*`,
 * written. Every place that shows a server's tools says so with this one helper (S51-07).
 */
export const NO_TOOL_LABEL = "no tool — name tools, or * for all";

export function toolsLabel(tools: readonly string[] | null | undefined): string {
  const named = tools ?? [];
  return named.length ? named.join(", ") : NO_TOOL_LABEL;
}
