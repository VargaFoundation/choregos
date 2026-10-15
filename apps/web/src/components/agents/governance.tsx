// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Badge, Button, Drawer, Field, Input, Select, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Agent } from "@/lib/types";
import { forkSlug, scopeHint, scopeLabel, SCOPES, type AgentScope, type MakeScope } from "./registre";

/**
 * Who owns an agent and who may change it (S39-09, ADR 0061). The console never guesses from roles:
 * the API sends `can` and one sentence, `cannot_reason`, and this file shows them as they are.
 */

const TONE: Record<AgentScope, Tone> = { platform: "accent", org: "neutral", project: "agent", personal: "warn" };

export function ScopeBadge({ agent }: { agent: Pick<Agent, "scope" | "project"> }) {
  const scope = (agent.scope ?? "org") as AgentScope;
  return (
    <Badge tone={TONE[scope]} data-testid="scope" title={scopeHint(scope)}>
      {scopeLabel(scope, agent.project)}
    </Badge>
  );
}

/** The chips of the registry: one per scope, with how many agents each holds. */
export function ScopeFilter({
  agents,
  value,
  onChange,
}: {
  agents: Agent[];
  value: AgentScope | null;
  onChange: (scope: AgentScope | null) => void;
}) {
  const count = (scope: AgentScope) => agents.filter((a) => (a.scope ?? "org") === scope).length;
  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1 text-xs ${active ? "border-accent-line bg-accent-soft text-accent-strong" : "border-line text-ink-muted hover:bg-surface-muted"}`;
  return (
    <div role="group" aria-label="Filter by scope" className="flex flex-wrap gap-2" data-testid="scope-filter">
      <button type="button" className={chip(value === null)} aria-pressed={value === null} onClick={() => onChange(null)}>
        All ({agents.length})
      </button>
      {SCOPES.map((scope) => (
        <button
          key={scope}
          type="button"
          className={chip(value === scope)}
          aria-pressed={value === scope}
          onClick={() => onChange(value === scope ? null : scope)}
        >
          {scopeLabel(scope)} ({count(scope)})
        </button>
      ))}
    </div>
  );
}

/** The server's sentence for what the caller may not do, in the page itself (not only a tooltip). */
export function ReasonNote({ agent }: { agent: Pick<Agent, "can" | "cannot_reason"> }) {
  if (agent.can?.publish !== false) return null;
  return (
    <div data-testid="cannot-reason">
      <Alert tone="warn">
      <span className="font-medium">Read only for you. </span>
      {agent.cannot_reason ?? "You may not change this agent."}
    </Alert>
    </div>
  );
}

/** An organisation admin who edits a common agent diverges from the catalogue: say it, and offer the fork. */
export function CatalogueWarning({ agent }: { agent: Pick<Agent, "scope" | "can"> }) {
  if (agent.scope !== "platform" || agent.can?.publish === false) return null;
  return (
    <p className="text-xs text-ink-muted" data-testid="diverges">
      A common agent: a new version diverges from the catalogue for every project that does not pin it. Prefer a fork.
    </p>
  );
}

/** The fork is behind its source: nothing moves until someone chooses to. */
export function UpstreamBanner({ agent }: { agent: Pick<Agent, "forked_from"> }) {
  const up = agent.forked_from;
  if (!up) return null;
  if (up.available === false) {
    return (
      <div data-testid="upstream">
      <Alert tone="neutral">
        Forked from <code>{up.slug}</code> v{up.version}; the source is deleted or not visible to you.
      </Alert>
    </div>
    );
  }
  return (
    <div data-testid="upstream">
      <Alert tone={up.behind ? "warn" : "neutral"}>
      Forked from{" "}
      <Link href={`/agents/${up.slug}`} className="underline">
        {up.slug}
      </Link>{" "}
      v{up.version}.
      {up.behind
        ? ` The source has moved on to v${up.latest_version}: compare it in its versions tab; this fork never follows it by itself.`
        : " It is up to date with its source."}
    </Alert>
    </div>
  );
}

/** Fork: a new slug, a scope among those the server allows, a project, the version to copy. */
export function ForkDialog({ org, agent, open, onClose }: { org: string; agent: Agent; open: boolean; onClose: () => void }) {
  const router = useRouter();
  const client = useQueryClient();
  const { me } = useSession();
  const scopes = (agent.can?.fork_scopes ?? []) as MakeScope[];
  const [scope, setScope] = useState<MakeScope | "">("");
  const [project, setProject] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState(String(agent.latest_version));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), enabled: open && scopes.includes("project") });
  const chosen: MakeScope = scope || scopes[0] || "personal";
  const projectSlug = project || (projects.data?.items ?? [])[0]?.slug || "";
  const proposed = forkSlug(agent.slug, chosen, projectSlug, me?.email);
  const shownSlug = slugTouched ? slug : proposed;

  async function fork() {
    setError(null);
    setBusy(true);
    try {
      const made = await api.forkAgent(org, agent.slug, {
        slug: shownSlug,
        display_name: name.trim() || null,
        scope: chosen,
        project: chosen === "project" ? projectSlug : null,
        version: Number(version),
      });
      await client.invalidateQueries({ queryKey: ["agents", org] });
      onClose();
      router.push(`/agents/${made.slug}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer open={open} onClose={onClose} title={`Fork ${agent.display_name}`}>
      <form
        className="space-y-4"
        aria-label="Fork"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) void fork();
        }}
      >
        <p className="text-sm text-ink-muted">
          A fork copies one version of this agent into a scope of yours. The source never changes; the fork says where it
          came from and tells you when the source publishes a newer version.
        </p>
        <Field label="Scope" hint={scopeHint(chosen)}>
          {(props) => (
            <Select {...props} value={chosen} onChange={(e) => setScope(e.target.value as MakeScope)}>
              {scopes.map((s) => (
                <option key={s} value={s}>
                  {scopeLabel(s)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {chosen === "project" && (
          <Field label="Project">
            {(props) => (
              <Select {...props} value={projectSlug} onChange={(e) => setProject(e.target.value)}>
                {(projects.data?.items ?? []).map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.slug}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Field label="New slug" hint="Slugs are unique in the organisation: a fork takes a new one.">
          {(props) => (
            <Input
              {...props}
              value={shownSlug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(e.target.value);
              }}
              required
            />
          )}
        </Field>
        <Field label="Display name" hint="Optional: the source's name by default.">
          {(props) => <Input {...props} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Source version">
          {(props) => (
            <Select {...props} value={version} onChange={(e) => setVersion(e.target.value)}>
              {Array.from({ length: agent.latest_version }, (_, i) => agent.latest_version - i).map((v) => (
                <option key={v} value={v}>
                  v{v}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="flex gap-2">
          <Button type="submit" tone="primary" disabled={busy || !shownSlug}>
            Fork
          </Button>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
        </div>
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Drawer>
  );
}

/** Change scope: promote a personal or project agent. The server says who may (`can.manage`). */
export function ScopePanel({ org, agent }: { org: string; agent: Agent }) {
  const client = useQueryClient();
  const scope = (agent.scope ?? "org") as AgentScope;
  const targets = (["org", "project", "personal"] as MakeScope[]).filter((s) => s !== scope);
  const [to, setTo] = useState<MakeScope>(targets[0]!);
  const [project, setProject] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), enabled: to === "project" });
  const projectSlug = project || (projects.data?.items ?? [])[0]?.slug || "";
  if (scope === "platform" || agent.can?.manage !== true) return null;

  async function change() {
    setError(null);
    setDone(null);
    try {
      const moved = await api.changeAgentScope(org, agent.slug, { scope: to, project: to === "project" ? projectSlug : null });
      setDone(`Now ${scopeLabel((moved.scope ?? to) as AgentScope, moved.project ?? null).toLowerCase()}.`);
      await client.invalidateQueries({ queryKey: ["agent", org, agent.slug] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <div className="space-y-3 text-sm" data-testid="scope-panel">
      <p className="text-xs text-ink-muted">
        {scopeLabel(scope, agent.project)}. {scopeHint(scope)} Moving it up makes it available to more people; the server refuses a
        move that would strand a pin or a workflow.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Move to">
          {(props) => (
            <Select {...props} value={to} onChange={(e) => setTo(e.target.value as MakeScope)}>
              {targets.map((s) => (
                <option key={s} value={s}>
                  {scopeLabel(s)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {to === "project" && (
          <Field label="Project">
            {(props) => (
              <Select {...props} value={projectSlug} onChange={(e) => setProject(e.target.value)}>
                {(projects.data?.items ?? []).map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.slug}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Button size="sm" onClick={() => void change()} disabled={to === "project" && !projectSlug}>
          Change scope
        </Button>
      </div>
      {done && <p role="status">{done}</p>}
      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  );
}
