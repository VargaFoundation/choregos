// SPDX-License-Identifier: Apache-2.0
/**
 * The workspace an agent declares it needs (S51-03, ADR 0119), as the console says it. The repository
 * is never the agent's: each project that runs it supplies its own (or none).
 */
import type { AgentSpec } from "./types";

export type Workspace = "none" | "read" | "write";

export const WORKSPACE_LABELS: Record<Workspace, string> = {
  none: "none — never sees the repository",
  read: "read — reads the project's repository, writes none of it",
  write: "write — edits the project's repository",
};

/** What a run of this version gets: a remote agent none, a hosted one its runtime's, any other its `workspace`. */
export function effectiveWorkspace(spec: AgentSpec | null | undefined): Workspace {
  const runtime = spec?.runtime;
  if (runtime?.kind === "a2a") return "none";
  if (runtime?.kind === "hosted") return runtime.workspace ?? "none";
  return spec?.workspace ?? "write";
}

/** Whether the version says it, or leaves it to the default (`write` for a coding agent). */
export function declaresWorkspace(spec: AgentSpec | null | undefined): boolean {
  return spec?.workspace != null || spec?.runtime?.kind === "a2a" || spec?.runtime?.kind === "hosted";
}

/** One line: the mode and what it means, "(default)" when the version leaves it unsaid. */
export function workspaceLine(spec: AgentSpec | null | undefined): string {
  const mode = effectiveWorkspace(spec);
  return `${WORKSPACE_LABELS[mode]}${declaresWorkspace(spec) ? "" : " (default)"}`;
}
