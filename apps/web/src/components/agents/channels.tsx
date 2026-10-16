// SPDX-License-Identifier: Apache-2.0
"use client";

import { Badge } from "@/components/ui";
import type { Agent, AgentVersion } from "@/lib/types";

/**
 * Where an agent's revisions are (S40-02, ADR 0071): production runs one, testing may run another for
 * the projects that follow it and for a share of the others' work items. Words, not colour alone.
 */

/** "Production v4", "Testing v5 · 10 %", "Approval pending": one line for a header, a list or an overview. */
export function ChannelBadges({
  agent,
  pending,
}: {
  agent: Pick<Agent, "stable_version" | "candidate_version" | "candidate_percent" | "pending_release">;
  /** The list knows from the approvers' inbox which agents wait; absent, the agent's own `pending_release` says. */
  pending?: boolean;
}) {
  const percent = agent.candidate_percent ?? 0;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" data-testid="channels">
      <Badge tone="ok" data-testid="channel-production">
        {agent.stable_version != null ? `Production v${agent.stable_version}` : "Production: latest"}
      </Badge>
      {agent.candidate_version != null && (
        <Badge tone="agent" data-testid="channel-testing">
          {`Testing v${agent.candidate_version}${percent > 0 ? ` · ${percent} %` : ""}`}
        </Badge>
      )}
      {(pending ?? agent.pending_release != null) && (
        <Badge tone="warn" data-testid="channel-pending">
          Approval pending
        </Badge>
      )}
    </span>
  );
}

/** Where one revision sits now: for a row of the versions table (and the history S40-12 builds on it). */
export function RevisionChannels({ channels }: { channels: AgentVersion["channels"] }) {
  const list = channels ?? [];
  if (list.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="revision-channels">
      {list.includes("stable") && <Badge tone="ok">Production</Badge>}
      {list.includes("candidate") && <Badge tone="agent">Testing</Badge>}
    </span>
  );
}
