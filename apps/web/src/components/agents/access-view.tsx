// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { Alert, Badge, Card, Empty, ErrorNote, Field, LEGENDE, ReadState, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { reachability } from "@/lib/outposts";
import type { AgentAccess, AgentAccessItem, AgentAccessOrigin, AgentAccessOutpost, AgentAccessTool, AgentComponent } from "@/lib/types";

/**
 * What an agent reaches in one project, in the five components of its definition (ADR 0127, S60-03):
 * instructions, model, tools, knowledge, memory. The API resolves it (`GET /agents/{slug}/access`); this
 * only says it, never derives it. Each line carries where it comes from, in words, not by colour alone.
 */

const ORIGIN: Record<AgentAccessOrigin, { label: string; tone: "ok" | "neutral" | "accent" | "danger" }> = {
  agent: { label: "agent", tone: "accent" },
  project: { label: "inherited from the project", tone: "neutral" },
  frame: { label: "platform frame", tone: "neutral" },
  refused: { label: "refused", tone: "danger" },
};

const COMPONENT_LABEL: Record<AgentComponent, string> = {
  instructions: "instructions",
  model: "model",
  tools: "tools",
  knowledge: "knowledge",
  memory: "memory",
};

const SOURCES: Array<{ id: AgentAccessTool["source"]; label: string }> = [
  { id: "frame", label: "Platform frame" },
  { id: "catalogue", label: "Catalogue" },
  { id: "extension", label: "Extensions" },
  { id: "connector", label: "Connectors" },
  { id: "web", label: "Web" },
];

export function OriginBadge({ origin }: { origin: AgentAccessOrigin }) {
  const o = ORIGIN[origin];
  return (
    <Badge tone={o.tone} data-testid={`origin-${origin}`}>
      {o.label}
    </Badge>
  );
}

function Why({ reason }: { reason?: string | null }) {
  return reason ? <span className="block text-xs text-ink-muted">{reason}</span> : null;
}

/**
 * Where a connector tool reached through an outpost lives (ADR 0149 §10): the outpost and the alias, and
 * whether it would answer now. Offline, a call fails at once: the person reads it before a run.
 */
function AtOutpost({ outpost }: { outpost: AgentAccessOutpost }) {
  return (
    <span className="flex basis-full flex-wrap items-baseline gap-x-2 text-xs text-ink-muted" data-testid="tool-outpost">
      <span>
        {outpost.name ? (
          <>
            At the outpost <code data-verbatim>{outpost.name}</code>
          </>
        ) : (
          "At no outpost of the organisation"
        )}
        , alias <code data-verbatim>{outpost.endpoint}</code>
      </span>
      {reachability(outpost).map((said) => (
        <Badge key={said.label} tone={said.tone} variant="plain">
          {said.label}
        </Badge>
      ))}
    </span>
  );
}

function ToolRow({ tool }: { tool: AgentAccessTool }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm" data-testid={`tool-${tool.source}-${tool.name}`}>
      <span className="font-mono">{tool.name}</span>
      <OriginBadge origin={tool.origin} />
      {tool.access && <span className="text-xs text-ink-muted">{tool.access}</span>}
      <Why reason={tool.reason} />
      {tool.outpost && <AtOutpost outpost={tool.outpost} />}
    </li>
  );
}

function ItemList({ items, empty, testid }: { items: AgentAccessItem[]; empty: string; testid: string }) {
  if (items.length === 0) return <p className="text-sm text-ink-muted">{empty}</p>;
  return (
    <ul className="space-y-1" data-testid={testid}>
      {items.map((item) => (
        <li key={item.name} className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-mono">{item.name}</span>
          <OriginBadge origin={item.origin} />
          {item.mode && <span className="text-xs text-ink-muted">{item.mode}</span>}
          <Why reason={item.reason} />
        </li>
      ))}
    </ul>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className={LEGENDE}>{title}</h3>
      {children}
    </section>
  );
}

/** The five components of one resolved access. */
export function AccessBody({ access }: { access: AgentAccess }) {
  const byToolSource = (source: AgentAccessTool["source"]) => access.tools.filter((t) => t.source === source);
  const model = access.model;
  return (
    <div className="space-y-4" data-testid="agent-access">
      {access.complete ? (
        <p className="text-xs text-ink-muted" data-testid="access-complete">
          Complete: this version leaves nothing to project {access.project}.
        </p>
      ) : (
        <Alert tone="warn" title="Incomplete: this version leaves some components to the project">
          <p data-testid="access-inherits">
            Inherited from {access.project}: {access.inherits.map((c) => COMPONENT_LABEL[c]).join(", ")}.
          </p>
        </Alert>
      )}
      <Group title="Model">
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm" data-testid="access-model">
          <span className="font-mono">{model.requested}</span>
          <span aria-hidden="true">{"→"}</span>
          <span className="sr-only">resolves to</span>
          <span className="font-mono">{model.resolved ?? "not resolved"}</span>
          <OriginBadge origin={model.origin} />
          <Why reason={model.reason} />
        </p>
      </Group>
      <Group title="Tools">
        <div className="space-y-3" data-testid="access-tools">
          {SOURCES.map(({ id, label }) => {
            const tools = byToolSource(id);
            return (
              <div key={id} data-testid={`source-${id}`}>
                <p className="text-sm font-medium">{label}</p>
                {tools.length === 0 ? (
                  <p className="text-xs text-ink-muted">None.</p>
                ) : (
                  <ul className="space-y-1 pl-3">
                    {tools.map((t) => (
                      <ToolRow key={`${t.source}:${t.name}`} tool={t} />
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </Group>
      <Group title="Knowledge">
        <ItemList items={access.knowledge} empty="No knowledge base." testid="access-knowledge" />
      </Group>
      <Group title="Memory">
        <ItemList items={access.memories} empty="No memory: it reads and writes nothing." testid="access-memories" />
      </Group>
    </div>
  );
}

/**
 * The Access tab's card: pick a project (the one of `?project=`, else the first the person reads), then
 * read the access resolved for it. The choice is in the address, so a link names it.
 */
export function AgentAccessCard({ org, slug, project, version }: { org: string; slug: string; project: string | null; version?: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  const slugs = (projects.data?.items ?? []).map((p) => p.slug);
  const chosen = project && slugs.includes(project) ? project : (slugs[0] ?? null);
  const access = useQuery({
    queryKey: ["agent-access", org, slug, chosen, version ?? null],
    queryFn: () => api.agentAccess(org, slug, chosen!, version),
    enabled: chosen != null,
    retry: false,
  });
  function choose(next: string) {
    const query = new URLSearchParams({ tab: "access", project: next });
    if (version != null) query.set("version", String(version));
    router.replace(`${pathname}?${query}`);
  }
  return (
    <Card title="What it reaches">
      <div className="space-y-4">
        <Field label="Project" hint="What the version reaches depends on the project that runs it.">
          {(props) => (
            <Select {...props} value={chosen ?? ""} disabled={slugs.length === 0} onChange={(e) => choose(e.target.value)}>
              {slugs.length === 0 && <option value="">—</option>}
              {(projects.data?.items ?? []).map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name ? `${p.name} (${p.slug})` : p.slug}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {projects.error ? (
          <ErrorNote>{String(projects.error)}</ErrorNote>
        ) : projects.data && chosen == null ? (
          <Empty>No project you can read: access is resolved in a project.</Empty>
        ) : (
          <>
            <ReadState lecture={access} quoi="what it reaches" />
            {access.data && <AccessBody access={access.data} />}
          </>
        )}
      </div>
    </Card>
  );
}
