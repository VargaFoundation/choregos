// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Input, Select } from "@/components/ui";
import { useBrouillon } from "@/components/workflows/brouillon";
import { describeActor } from "@/components/workflows/document";
import { operations } from "@/components/workflows/operations";
import { api } from "@/lib/api";
import type { Agent, AgentCatalogueEntry } from "@/lib/types";

const NAME = /^[a-z][a-z0-9_-]{0,62}$/;
const ROLE = /^[a-z][a-z0-9_]{1,31}$/;

/** An agent a workflow may name here (ADR 0108 §2): the organisation's, or the platform catalogue's. */
export type OfferedAgent = { slug: string; display: string; role: string; installed: boolean };

/**
 * The agents a picker offers: the organisation's own and common agents (never a personal or another
 * project's, ADR 0061; never a retired one), then the platform catalogue's internal entries not installed
 * yet, which install themselves when the workflow is published (ADR 0040 §5).
 */
export function offeredAgents(agents: Agent[], catalogue: AgentCatalogueEntry[]): OfferedAgent[] {
  const roles = new Map(catalogue.map((e) => [e.slug, e.role]));
  const ownAgents = agents
    .filter((a) => (a.scope ?? "org") !== "personal" && a.scope !== "project" && a.lifecycle !== "retired")
    .filter((a) => a.kind !== "external")
    .map((a) => ({ slug: a.slug, display: a.display_name, role: roles.get(a.slug) ?? "work", installed: true }));
  const seen = new Set(ownAgents.map((a) => a.slug));
  const fromCatalogue = catalogue
    .filter((e) => e.kind === "internal" && !e.installed && !seen.has(e.slug))
    .map((e) => ({ slug: e.slug, display: e.display_name, role: e.role, installed: false }));
  return [...ownAgents, ...fromCatalogue];
}

/** The workflow's actors (ADR 0109 §2): who acts in it, added and removed as typed operations. */
export function ActorsPanel({ org }: { org: string }) {
  const draft = useBrouillon();
  const actors = Object.entries(draft.document?.actors ?? {});
  return (
    <Card title="Actors">
      <div className="space-y-3 text-sm" data-testid="actors-panel">
        <ul className="space-y-2" aria-label="Actors of the workflow">
          {actors.map(([name, actor]) => (
            <li key={name} className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0">
                <code>{name}</code> — {describeActor(actor)}
              </span>
              <button
                type="button"
                className="text-xs text-ink-muted underline hover:text-danger"
                aria-label={`Remove the actor ${name}`}
                onClick={() => void draft.appliquer(operations.removeActor(name))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
        <AddActorForm org={org} taken={new Set(actors.map(([name]) => name))} />
      </div>
    </Card>
  );
}

function AddActorForm({ org, taken }: { org: string; taken: Set<string> }) {
  const draft = useBrouillon();
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org), enabled: !!org });
  const catalogue = useQuery({ queryKey: ["agent-catalogue", org], queryFn: () => api.agentCatalogue(org), enabled: !!org });
  const offered = offeredAgents(agents.data ?? [], catalogue.data ?? []);
  const [kind, setKind] = useState<"agent" | "human" | "job" | "system">("agent");
  const [name, setName] = useState("");
  const [agent, setAgent] = useState("");
  const [role, setRole] = useState("");
  const [group, setGroup] = useState("");
  const [sla, setSla] = useState("24");
  const [command, setCommand] = useState("");
  const chosen = offered.find((a) => a.slug === agent);
  const effectiveRole = role || chosen?.role || "";
  const effectiveName = name || (kind === "agent" ? agent.replace(/-/g, "_") : kind === "human" ? group.replace(/-/g, "_") : "");
  const valid =
    NAME.test(effectiveName) &&
    !taken.has(effectiveName) &&
    (kind === "agent"
      ? !!agent && ROLE.test(effectiveRole)
      : kind === "human"
        ? !!group
        : kind === "job"
          ? ROLE.test(role) && command.trim() !== ""
          : true);

  function spec(): Record<string, unknown> {
    if (kind === "agent") return { type: "agent", role: effectiveRole, agent };
    if (kind === "human") return sla ? { type: "human", group: group, sla_hours: Number(sla) } : { type: "human", group: group };
    if (kind === "job") return { type: "job", role, command: command.trim().split(/\s+/) };
    return { type: "system" };
  }

  return (
    <form
      className="space-y-2 border-t border-line pt-3"
      aria-label="Add an actor"
      onSubmit={(event) => {
        event.preventDefault();
        void draft.appliquer(operations.addActor(effectiveName, spec())).then((ok) => {
          if (!ok) return;
          setName("");
          setAgent("");
          setRole("");
        });
      }}
    >
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Add</span>
        <Select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="agent">An agent</option>
          <option value="human">People of a group</option>
          <option value="job">A job (a command, no model)</option>
          <option value="system">The platform</option>
        </Select>
      </label>
      {kind === "agent" && (
        <>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Agent</span>
            <Select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">—</option>
              {offered.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.display} ({a.slug}) — {a.installed ? "in your organisation" : "installed when published"}
                </option>
              ))}
            </Select>
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Role</span>
            <Input value={effectiveRole} onChange={(e) => setRole(e.target.value)} />
          </label>
        </>
      )}
      {kind === "human" && (
        <div className="grid grid-cols-2 gap-2">
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Group</span>
            <Input value={group} onChange={(e) => setGroup(e.target.value)} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Answers within (hours)</span>
            <Input type="number" min={1} value={sla} onChange={(e) => setSla(e.target.value)} />
          </label>
        </div>
      )}
      {kind === "job" && (
        <>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Role</span>
            <Input value={role} onChange={(e) => setRole(e.target.value)} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-ink-muted">Command (words, no shell)</span>
            <Input value={command} onChange={(e) => setCommand(e.target.value)} />
          </label>
        </>
      )}
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Name in the workflow</span>
        <Input value={effectiveName} onChange={(e) => setName(e.target.value)} />
      </label>
      {taken.has(effectiveName) && <p className="text-xs text-warn">An actor already has this name.</p>}
      <Button size="sm" type="submit" disabled={!valid || draft.occupe}>
        Add the actor
      </Button>
    </form>
  );
}
