// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, ErrorNote, Field, Input, type Tone } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Agent, AgentLifecycleResult } from "@/lib/types";

/**
 * The life of an agent (S32-04): draft → active → deprecated → retired. Apart from its operational
 * status (suspended, revoked): this says what the organisation intends for it. A self-contained
 * panel, so it slots into whichever tab of the agent page holds access and lifecycle.
 */

export type Lifecycle = "draft" | "active" | "deprecated" | "retired";

const TONE: Record<Lifecycle, Tone> = { draft: "neutral", active: "ok", deprecated: "warn", retired: "danger" };

/** The moves a person may ask for from a state; `retired` is final. */
export function nextMoves(lifecycle: Lifecycle): Array<"active" | "deprecated" | "retired"> {
  switch (lifecycle) {
    case "draft":
      return ["active", "retired"];
    case "active":
      return ["deprecated", "retired"];
    case "deprecated":
      return ["active", "retired"];
    default:
      return [];
  }
}

const LIBELLE: Record<"active" | "deprecated" | "retired", (from: Lifecycle) => string> = {
  active: (from) => (from === "draft" ? "Activate" : "Reactivate"),
  deprecated: () => "Deprecate",
  retired: () => "Retire…",
};

export function LifecycleBadges({ agent }: { agent: Pick<Agent, "lifecycle" | "unused" | "last_run_at"> }) {
  const lifecycle = (agent.lifecycle ?? "active") as Lifecycle;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {lifecycle !== "active" && (
        <Badge tone={TONE[lifecycle]} data-testid="cycle">
          {lifecycle}
        </Badge>
      )}
      {agent.unused && (
        <Badge tone="warn" data-testid="inutilise" title={agent.last_run_at ? `last run ${relative(agent.last_run_at)}` : "never ran"}>
          unused for 60 days
        </Badge>
      )}
    </span>
  );
}

/** What retiring did, in one sentence. */
export function checklistSentence(result: AgentLifecycleResult): string {
  const parts = [
    `${result.revoked_credentials ?? 0} credential${result.revoked_credentials === 1 ? "" : "s"} revoked`,
    (result.unpinned_projects ?? []).length
      ? `unpinned from ${(result.unpinned_projects ?? []).join(", ")}`
      : "pinned by no project",
    "new runs refused",
  ];
  if (result.forced) parts.push(`forced past the runs of ${(result.running_work_items ?? []).join(", ")}`);
  return `Retired: ${parts.join(", ")}.`;
}

export function LifecyclePanel({ org, agent }: { org: string; agent: Agent }) {
  const client = useQueryClient();
  const lifecycle = (agent.lifecycle ?? "active") as Lifecycle;
  const [confirming, setConfirming] = useState(false);
  const [inFlight, setInFlight] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [owner, setOwner] = useState("");

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
    await client.invalidateQueries({ queryKey: ["agents", org] });
  }

  async function move(to: "active" | "deprecated" | "retired", force = false) {
    setError(null);
    setDone(null);
    try {
      const result = await api.changeAgentLifecycle(org, agent.slug, { to, force });
      setDone(to === "retired" ? checklistSentence(result) : `Now ${result.agent.lifecycle}.`);
      setConfirming(false);
      setInFlight(null);
      await refresh();
    } catch (cause) {
      if (to === "retired" && cause instanceof ApiError && cause.status === 409 && /in flight/.test(cause.message)) {
        setInFlight(cause.message);
      } else {
        setError(cause instanceof Error ? cause.message : "refused");
      }
    }
  }

  async function transfer() {
    setError(null);
    setDone(null);
    try {
      const moved = await api.transferAgentOwnership(org, agent.slug, owner);
      setDone(`Now owned by ${moved.owner ?? owner}; its credentials stay attached.`);
      setOwner("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <div className="space-y-3 text-sm" data-testid="cycle-de-vie">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={TONE[lifecycle]}>{lifecycle}</Badge>
        <span className="text-xs text-ink-muted">
          {agent.last_run_at ? `last run ${relative(agent.last_run_at)}` : "never ran"}
          {agent.unused ? " — unused for 60 days: a candidate for retirement" : ""}
        </span>
      </div>
      {lifecycle === "deprecated" && (
        <p className="text-xs text-ink-muted">Deprecated: it still runs, and every pin of it says it is on its way out.</p>
      )}
      {lifecycle === "draft" && <p className="text-xs text-ink-muted">A draft: no project pins it and no run starts with it.</p>}
      {lifecycle === "retired" ? (
        <p className="text-xs text-ink-muted">Retired{agent.retired_at ? ` ${relative(agent.retired_at)}` : ""}: final.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {nextMoves(lifecycle)
            .filter((to) => to !== "retired")
            .map((to) => (
              <Button key={to} size="sm" onClick={() => void move(to)}>
                {LIBELLE[to](lifecycle)}
              </Button>
            ))}
          {confirming ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              its credentials are revoked, its pins removed, no new run starts — for good.
              <Button size="sm" tone="danger" onClick={() => void move("retired")}>
                Retire
              </Button>
              <Button size="sm" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button size="sm" tone="danger" onClick={() => setConfirming(true)}>
              {LIBELLE.retired(lifecycle)}
            </Button>
          )}
        </div>
      )}
      {inFlight && (
        <div className="space-y-1" role="alert">
          <p className="text-xs">{inFlight}</p>
          <Button size="sm" tone="danger" onClick={() => void move("retired", true)}>
            Retire anyway
          </Button>
        </div>
      )}
      {lifecycle !== "retired" && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Transfer ownership to" className="min-w-0 flex-1">
            {(props) => (
              <Input {...props} type="email" placeholder="member@org" value={owner} onChange={(e) => setOwner(e.target.value)} />
            )}
          </Field>
          <Button size="sm" onClick={() => void transfer()} disabled={!owner.includes("@")}>
            Transfer
          </Button>
        </div>
      )}
      {done && <p role="status">{done}</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
