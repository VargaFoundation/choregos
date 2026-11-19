// SPDX-License-Identifier: Apache-2.0
/**
 * The neutral names of ADR 0181 C1, read first, with the former names the API also serves until 0.30.0
 * as a fallback. When the former names leave, each fallback leaves with them.
 */

/** The relation of the link to the change a step proposes for review (`rel: change_request`). */
export const CHANGE_REQUEST = "change_request";

type Linked = { links?: Array<{ rel: string; url: string }> | null; pr_url?: string | null };

/** The URL of the change a work item or a step's artifacts propose, if any. */
export function changeRequestOf(item: Linked | null | undefined): string | null {
  if (!item) return null;
  return item.links?.find((link) => link.rel === CHANGE_REQUEST)?.url ?? item.pr_url ?? null;
}

/** The workspace a work item works on, and whether it is fixed. */
export function workspaceOf(item: { workspace?: string | null; repository?: string | null }): string | null {
  return item.workspace ?? item.repository ?? null;
}

export function workspaceFixed(item: { workspace_fixed?: boolean; repository_fixed?: boolean }): boolean {
  return item.workspace_fixed ?? item.repository_fixed ?? false;
}

/** Whether a catalogue entry needs a workspace its steps read and write. */
export function needsWorkspace(entry: { needs_workspace?: boolean; needs_repository?: boolean }): boolean {
  return entry.needs_workspace ?? entry.needs_repository ?? false;
}

/** Whether a step changes the work item's workspace. */
export function writesWorkspace(step: { writes_workspace?: boolean; writes_branch?: boolean } | null | undefined): boolean {
  return step?.writes_workspace ?? step?.writes_branch ?? false;
}

/** Whether the change request's body carries an artifact. */
export function inChangeRequest(artifact: { in_change_request?: boolean; in_pull_request?: boolean }): boolean {
  return artifact.in_change_request ?? artifact.in_pull_request ?? false;
}

/** The share of work items accepted without a rework loop. */
export function acceptanceRate(
  stats: { first_pass_acceptance_rate?: number | null; first_pass_merge_rate?: number | null } | null | undefined,
): number | null | undefined {
  return stats?.first_pass_acceptance_rate ?? stats?.first_pass_merge_rate;
}
