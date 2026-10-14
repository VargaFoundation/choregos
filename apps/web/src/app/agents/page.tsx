// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  Input,
  Lead,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import { percent, usd } from "@/lib/format";
import { useSession } from "@/lib/session";

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
        <NouvelAgent org={org} />
      </Drawer>
    </div>
  );
}

/** Un agent interne : ses instructions et ses réglages font sa version 1. */
function NouvelAgent({ org }: { org: string }) {
  const client = useQueryClient();
  const [slug, setSlug] = useState("");
  const [nom, setNom] = useState("");
  const [instructions, setInstructions] = useState("");
  const [modele, setModele] = useState("");
  const [budget, setBudget] = useState("");
  const [erreur, setErreur] = useState<string | null>(null);
  const [fait, setFait] = useState<string | null>(null);
  async function creer() {
    setErreur(null);
    try {
      const agent = await api.createAgent(org, {
        slug,
        kind: "internal",
        display_name: nom || slug,
        spec: {
          instructions,
          ...(modele ? { model: modele } : {}),
          ...(budget ? { budget: { daily_usd: Number(budget) } } : {}),
        },
      });
      setFait(`${agent.display_name} registered as v${agent.latest_version}`);
      setSlug("");
      setNom("");
      setInstructions("");
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "registration refused");
    }
  }
  return (
    <div>
      <form
        className="grid gap-3 md:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void creer();
        }}
      >
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Slug</span>
          <Input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            pattern="[a-z0-9][a-z0-9-]*"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Name</span>
          <Input value={nom} onChange={(e) => setNom(e.target.value)} />
        </label>
        <label className="space-y-1 md:col-span-2">
          <span className="text-xs text-ink-muted">Instructions (what the frame of the platform does not say)</span>
          <Textarea
            className="w-auto font-mono text-xs"
            rows={6}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Model (empty: the project&apos;s profile)</span>
          <Input
            value={modele}
            onChange={(e) => setModele(e.target.value)}
            placeholder="profile:standard"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">Daily budget (USD, empty: none)</span>
          <Input
            type="number"
            min={0}
            step="0.5"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-3 md:col-span-2">
          <Button type="submit" tone="accent" disabled={!slug}>
            Register
          </Button>
          {fait && (
            <span className="text-sm" role="status">
              {fait}
            </span>
          )}
        </div>
        {erreur && (
          <div className="md:col-span-2">
            <ErrorNote>{erreur}</ErrorNote>
          </div>
        )}
      </form>
    </div>
  );
}
