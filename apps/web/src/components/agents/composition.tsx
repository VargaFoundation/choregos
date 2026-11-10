// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, Card } from "@/components/ui";
import { currentVersion, versionOf } from "@/lib/agent-versions";
import { describe, effectiveBindings } from "@/lib/memories";
import { skillHref } from "@/lib/skill-slug";
import type { Agent, AgentAccess } from "@/lib/types";
import { INHERITED, inheritsMemory, missingComponents, toolSources } from "./spec-summary";

/**
 * The five components of the current version in one card on the Overview tab (S60-07, ADR 0127): an agent
 * is read first here, and a page that showed only a name and tags made it look as if it had no model,
 * tools or knowledge. It reads the version's spec (same helpers as the Definition tab); the model's
 * resolution comes from an Access answer only when one is already in the cache, never from a new call.
 */

const LABEL: Record<string, string> = {
  model: "model",
  "tools.catalogue": "catalogue tools",
  "tools.extensions": "extensions",
  "tools.web": "web tools",
  memories: "memory",
};

function Line({ label, testid, children }: { label: string; testid: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className="text-sm" data-testid={testid}>
        {children}
      </dd>
    </>
  );
}

export function AgentComposition({ org, agent }: { org: string; agent: Agent }) {
  const client = useQueryClient();
  const spec = versionOf(agent, currentVersion(agent))?.spec;
  if (!spec) return null;
  const missing = missingComponents(spec);
  const servers = (spec.mcp_servers ?? []).filter((s) => s.connector);
  const skills = spec.skills ?? [];
  const bases = spec.knowledge ?? [];
  const bindings = effectiveBindings(spec);
  const cached = client
    .getQueriesData<AgentAccess>({ queryKey: ["agent-access", org, agent.slug] })
    .map(([, data]) => data)
    .find((data): data is AgentAccess => data != null && data.version === currentVersion(agent));
  const base = `/agents/${agent.slug}`;
  return (
    <Card title="Composition" data-testid="agent-composition">
      <div className="space-y-3">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span>
            Version <span className="font-mono">v{currentVersion(agent)}</span>
          </span>
          {missing.length === 0 ? (
            <Badge tone="ok" data-testid="composition-complete">
              complete
            </Badge>
          ) : (
            <Badge tone="warn" data-testid="composition-inherits">
              {`inherits: ${missing.map((m) => LABEL[m] ?? m).join(", ")}`}
            </Badge>
          )}
        </p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
          <Line label="Model" testid="composition-model">
            {spec.model ? (
              <>
                <span className="font-mono">{spec.model}</span> <span className="text-xs text-ink-muted">requested</span>
                {cached?.model.resolved && (
                  <span className="block text-xs text-ink-muted">
                    resolves to <span className="font-mono">{cached.model.resolved}</span> in {cached.project}
                  </span>
                )}
              </>
            ) : (
              INHERITED
            )}
          </Line>
          <Line label="Tools" testid="composition-tools">
            <ul className="space-y-0.5">
              {toolSources(spec).map((source) => (
                <li key={source.id} data-testid={`composition-tools-${source.id}`}>
                  {source.label}:{" "}
                  {source.named === null ? INHERITED : source.named.length === 0 ? "none (closed)" : `${source.named.length} named`}
                </li>
              ))}
              <li data-testid="composition-tools-mcp">
                MCP servers: {servers.length === 0 ? "none" : `${servers.length} (${servers.map((s) => s.connector).join(", ")})`}
              </li>
            </ul>
          </Line>
          <Line label="Knowledge" testid="composition-knowledge">
            {bases.length === 0 ? "No knowledge base" : bases.map((k) => k.slug).join(", ")}
          </Line>
          <Line label="Skills" testid="composition-skills">
            {skills.length === 0
              ? "No skill"
              : skills.map((s) => (
                  <Link key={s.slug} href={skillHref(s.slug)} className="mr-2 underline">
                    {s.slug}
                    {s.version ? `@${s.version}` : ""}
                  </Link>
                ))}
          </Line>
          <Line label="Memory" testid="composition-memory">
            {inheritsMemory(spec)
              ? INHERITED
              : bindings.length === 0
                ? "None: it reads and writes nothing"
                : bindings.map((b) => describe(b)).join(", ")}
          </Line>
        </dl>
        <p className="flex flex-wrap gap-x-4 text-sm">
          <Link href={`${base}?tab=definition`} className="underline" data-testid="composition-definition-link">
            Open the definition
          </Link>
          <Link href={`${base}?tab=access`} className="underline" data-testid="composition-access-link">
            What it reaches in a project
          </Link>
        </p>
      </div>
    </Card>
  );
}
