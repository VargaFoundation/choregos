// SPDX-License-Identifier: Apache-2.0
/**
 * What the runtime brings to an agent before the version names a single tool (the Composition card).
 *
 * A coding agent (an `acp` runtime, or no `runtime` at all) runs in a sandbox with its harness's own
 * tools: files, shell and search on the work item's clone of the repository, and git through the shell.
 * The harness is the version's `backend`, else the project's `agent.default_backend` (`claude-code` unless
 * the project says otherwise, `ProjectConfig.agent.default_backend`). The runner gives the Claude Code
 * harness a `PreToolUse` hook on `Write|Edit|MultiEdit` and `Bash`, and denies `WebFetch` and `WebSearch`
 * when `network.builtin_web_tools` is false (`choregos_runner/backends/claude_code.py`). Every other
 * runtime (prompt, voice, hosted, a2a) has no coding harness: it has only what the sources below name.
 */
import type { AgentSpec } from "./types";
import { effectiveWorkspace, type Workspace } from "./workspace";

export type BuiltinTool = { id: "files" | "shell" | "git" | "search" | "web"; label: string; detail: string };

export interface Harness {
  /** `coding` runs in the sandbox with the harness's tools; `none` has no tool of its own. */
  kind: "coding" | "none";
  /** The harness named by the version; `null` leaves it to the project. */
  backend: string | null;
  /** Why there is no built-in tool, for a `none` harness. */
  reason?: string;
  workspace: Workspace;
  tools: BuiltinTool[];
}

export const DEFAULT_BACKEND = "claude-code";

const NO_HARNESS: Record<string, string> = {
  a2a: "It runs elsewhere and is reached over A2A.",
  prompt: "It is a prompt agent: the platform runs its tool loop, with no sandbox.",
  voice: "It is a voice session: no sandbox.",
  hosted: "It runs in its own container: the platform gives it no coding tools.",
};

export function harnessOf(spec: Pick<AgentSpec, "backend" | "runtime" | "workspace" | "network">): Harness {
  const kind = spec.runtime?.kind as string | undefined;
  const workspace = effectiveWorkspace(spec as AgentSpec);
  if (kind && kind in NO_HARNESS) return { kind: "none", backend: null, reason: NO_HARNESS[kind], workspace, tools: [] };
  const named = spec.backend ?? (spec.runtime?.kind === "acp" ? spec.runtime.backend : null) ?? null;
  const tools: BuiltinTool[] = [
    {
      id: "files",
      label: "Files",
      detail:
        workspace === "none"
          ? "no repository is cloned: only the step's own working files"
          : workspace === "read"
            ? "read and search the work item's clone; nothing in it may be written"
            : "read, edit and create files in the work item's clone",
    },
    { id: "shell", label: "Shell", detail: "run commands in the sandbox, within the platform's guardrails" },
  ];
  if (workspace !== "none") tools.push({ id: "git", label: "Git", detail: "through the shell, on the work item's clone" });
  if (workspace !== "none") tools.push({ id: "search", label: "Search", detail: "find files and text in the clone" });
  if ((named ?? DEFAULT_BACKEND) === DEFAULT_BACKEND && spec.network?.builtin_web_tools !== false) {
    tools.push({ id: "web", label: "Web fetch and search", detail: "the CLI's own, unless the project's network policy switches them off" });
  }
  return { kind: "coding", backend: named, workspace, tools };
}
