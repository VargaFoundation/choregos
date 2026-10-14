// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { AgentCatalogue } from "@/components/agents/catalogue";
import { LifecycleBadges } from "@/components/agents/lifecycle";
import { resumeDeLaVersion } from "@/components/agents/registre";
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
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent, usd } from "@/lib/format";
import { useSession } from "@/lib/session";

// The builder is the heaviest form of the console, and most visits never open it (S32-01).
const AgentBuilder = dynamic(() => import("@/components/agents/builder").then((m) => m.AgentBuilder), {
  loading: () => <p className="text-sm text-ink-muted">Loading the builder…</p>,
});

/**
 * The organisation's agents (ADR 0033): the registry, and nothing else (S30-04, plan item 7). A new
 * agent and the catalogue open in a drawer; AI clients and their tokens live on the AI clients'
 * page. Each column comes from one request: the registry, and the metrics of every agent at once.
 */
export default function AgentsPage() {
  const { org } = useSession();
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const metrics = useQuery({ queryKey: ["agent-metrics", org], queryFn: () => api.agentMetricsList(org) });
  const [drawer, setDrawer] = useState<"new" | "catalogue" | null>(null);
  const [done, setDone] = useState<string | null>(null);
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
          <Button tone="primary" onClick={() => setDrawer("new")}>
            New agent
          </Button>
        </div>
      </div>
      <Glossaire ici="agents" />
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
              {agents.data.map((agent) => {
                const m = parAgent.get(agent.slug);
                return (
                  <TR key={agent.slug} interactive data-testid={`agent-${agent.slug}`}>
                    <TD>
                      <Link href={`/agents/${agent.slug}`} className="font-medium hover:underline">
                        {agent.display_name}
                      </Link>
                      <span className="ml-2 font-mono text-xs text-ink-muted">{agent.slug}</span>
                      {agent.kind === "external" && (
                        <Badge tone="accent" className="ml-2">
                          External
                        </Badge>
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
      <Drawer open={drawer === "new"} onClose={() => setDrawer(null)} title="New agent">
        <AgentBuilder
          org={org}
          mode={{ kind: "create" }}
          onDone={(message) => {
            setDone(message);
            setDrawer(null);
          }}
        />
      </Drawer>
      {done && (
        <p className="text-sm" role="status">
          {done}
        </p>
      )}
    </div>
  );
}
