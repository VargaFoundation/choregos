// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AgentCatalogue } from "@/components/agents/catalogue";
import { LifecycleBadges } from "@/components/agents/lifecycle";
import { ScopeBadge, ScopeFilter } from "@/components/agents/governance";
import { resumeDeLaVersion, scopeLabel, type AgentScope } from "@/components/agents/registre";
import { TagFilter, TagList, withTags } from "@/components/agents/tags";
import { Glossaire } from "@/components/glossaire";
import {
  Badge,
  Button,
  Card,
  Drawer,
  Empty,
  ErrorNote,
  Heading,
  Lead,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  buttonClasses,
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent, usd } from "@/lib/format";
import { useSession } from "@/lib/session";

/**
 * The organisation's agents (ADR 0033): the registry, and nothing else (S30-04, plan item 7). A new
 * agent has its own page (`/agents/new`, S39-05) and the catalogue opens in a drawer; AI clients and their tokens live on the AI clients'
 * page. Each column comes from one request: the registry, and the metrics of every agent at once.
 */
export default function AgentsPage() {
  return (
    <Suspense>
      <Agents />
    </Suspense>
  );
}

function Agents() {
  const { org } = useSession();
  // `/agents?catalogue=1`: the way back from the new-agent page's "add one from the catalogue".
  const search = useSearchParams();
  const router = useRouter();
  const catalogue = search.get("catalogue") === "1";
  // `/agents?tag=madr&tag=design`: a filtered list is a link (S40-10); an agent must carry every tag.
  const tags = search.getAll("tag");
  function selectTags(next: string[]) {
    const query = new URLSearchParams(search.toString());
    query.delete("tag");
    for (const tag of next) query.append("tag", tag);
    const text = query.toString();
    router.replace(text ? `/agents?${text}` : "/agents");
  }
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const metrics = useQuery({ queryKey: ["agent-metrics", org], queryFn: () => api.agentMetricsList(org) });
  const [scope, setScope] = useState<AgentScope | null>(null);
  const [drawer, setDrawer] = useState<"catalogue" | null>(catalogue ? "catalogue" : null);
  const shown = withTags(
    (agents.data ?? []).filter((a) => scope === null || (a.scope ?? "org") === scope),
    tags,
  );
  const parAgent = new Map((metrics.data ?? []).map((m) => [m.agent, m]));
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <Heading as="h1" size="xl">
            Agents
          </Heading>
          <Lead>
            An agent is an object of the organisation: its instructions, model, skills and tools live in versions that
            never change; a project pins one and may only tighten it.
          </Lead>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setDrawer("catalogue")}>Add from catalogue</Button>
          <Link href="/agents/new" className={buttonClasses("primary")}>
            New agent
          </Link>
        </div>
      </div>
      <Glossaire ici="agents" />
      {agents.data && agents.data.length > 0 && <ScopeFilter agents={agents.data} value={scope} onChange={setScope} />}
      {agents.data && <TagFilter agents={agents.data} value={tags} onChange={selectTags} />}
      <Card title="Registry" padding="none" className="overflow-hidden">
        {agents.error ? (
          <div className="p-5">
            <ErrorNote>{String(agents.error)}</ErrorNote>
          </div>
        ) : !agents.data ? (
          <p className="p-5 text-sm text-ink-muted">Reading the registry…</p>
        ) : agents.data.length === 0 ? (
          <div className="p-5">
            <Empty title="No agent yet">Create one, or add one from the catalogue.</Empty>
          </div>
        ) : shown.length === 0 ? (
          <div className="p-5">
            {tags.length > 0 ? (
              <Empty title={`No agent tagged ${tags.join(" + ")}`}>
                <button type="button" className="underline" onClick={() => selectTags([])}>
                  Clear the tags
                </button>
                {scope !== null && " or pick another scope."}
              </Empty>
            ) : (
              <Empty title={`No ${scopeLabel(scope ?? undefined).toLowerCase()} agent`}>Pick another scope, or clear the filter.</Empty>
            )}
          </div>
        ) : (
          <Table data-testid="registre">
            <THead>
              <TR>
                <TH>Agent</TH>
                <TH>Owner</TH>
                <TH>Status</TH>
                <TH>Version</TH>
                <TH align="right">Runs (30 days)</TH>
                <TH align="right">Cost (30 days)</TH>
                <TH align="right">Success rate</TH>
              </TR>
            </THead>
            <TBody>
              {shown.map((agent) => {
                const m = parAgent.get(agent.slug);
                return (
                  <TR key={agent.slug} interactive data-testid={`agent-${agent.slug}`}>
                    <TD>
                      <Link href={`/agents/${agent.slug}`} className="font-medium hover:underline">
                        {agent.display_name}
                      </Link>
                      <span className="ml-2 font-mono text-xs text-ink-muted">{agent.slug}</span>
                      <span className="ml-2">
                        <ScopeBadge agent={agent} />
                      </span>
                      {agent.kind !== "internal" && (
                        <Badge tone="accent" className="ml-2">
                          {agent.kind === "remote" ? "Remote · A2A" : "External"}
                        </Badge>
                      )}
                      {(agent.tags ?? []).length > 0 && (
                        <div className="mt-1">
                          <TagList tags={agent.tags} />
                        </div>
                      )}
                    </TD>
                    <TD className="text-xs text-ink-muted">{agent.owner ?? "—"}</TD>
                    <TD>
                      <Badge tone={agent.status === "active" ? "ok" : "danger"}>{agent.status}</Badge>
                      <LifecycleBadges agent={agent} />
                    </TD>
                    <TD className="font-mono">
                      v{agent.latest_version}
                      <span className="ml-2 font-sans text-xs text-ink-muted">
                        {resumeDeLaVersion(agent.versions?.find((v) => v.version === agent.latest_version)?.spec)}
                      </span>
                    </TD>
                    <TD align="right" className="font-mono">
                      {m ? m.runs : "—"}
                    </TD>
                    <TD align="right" className="font-mono">
                      {m ? usd(m.cost_usd) : "—"}
                    </TD>
                    <TD align="right" className="font-mono">
                      {m?.success_rate != null ? percent(m.success_rate) : "—"}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </Card>
      <Drawer open={drawer === "catalogue"} onClose={() => setDrawer(null)} title="Add from catalogue">
        <AgentCatalogue org={org} />
      </Drawer>
    </div>
  );
}
