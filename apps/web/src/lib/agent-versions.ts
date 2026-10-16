// SPDX-License-Identifier: Apache-2.0
import type { Agent, AgentVersion } from "@/lib/types";

/**
 * Helpers on an agent's versions (S40-12). The console says *revision*, the API says *version*; both are
 * an `agent_versions` row, immutable and numbered once.
 */

/** Newest first. */
export function sortedVersions(agent: Pick<Agent, "versions">): AgentVersion[] {
  return [...(agent.versions ?? [])].sort((a, b) => b.version - a.version);
}

export function versionOf(agent: Pick<Agent, "versions">, n: number): AgentVersion | undefined {
  return (agent.versions ?? []).find((v) => v.version === n);
}

/** The version just before `n`, if there is one. */
export function previousVersion(agent: Pick<Agent, "versions">, n: number): AgentVersion | undefined {
  return sortedVersions(agent).find((v) => v.version < n);
}

/**
 * Which revision is current: the latest saved, the one an author reads and edits from. Which one a run
 * loads is the channels' business (S40-02, ADR 0071): `productionVersion` below, and the Production /
 * Testing badges of the history.
 */
export function currentVersion(agent: Pick<Agent, "latest_version">): number {
  return agent.latest_version;
}

/** The revision production runs: its pointer, else the latest for an agent with none. */
export function productionVersion(agent: Pick<Agent, "latest_version"> & Partial<Pick<Agent, "stable_version">>): number {
  return agent.stable_version ?? agent.latest_version;
}

/** Whether the agent may take a new version at all: revoked and retired agents never do. */
export function canEdit(agent: Pick<Agent, "can" | "status" | "lifecycle">): boolean {
  return agent.can?.publish !== false && agent.status !== "revoked" && agent.lifecycle !== "retired";
}

/** `?compare=1..3` as a pair, or null when it is not one. */
export function parseCompare(text: string | null | undefined): [number, number] | null {
  const match = /^(\d+)\.\.(\d+)$/.exec(text ?? "");
  if (!match) return null;
  const pair: [number, number] = [Number(match[1]), Number(match[2])];
  return pair[0] === pair[1] ? null : pair;
}
