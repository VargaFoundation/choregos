// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge, Eyebrow, Heading } from "@varga/design-system";
import { estUnClientMcp, resumeDeLaVersion } from "@/components/agents/registre";
import { CatalogueDAgents } from "@/components/agents/catalogue";
import { ClientsMcp } from "@/components/agents/clients-mcp";
import { Glossaire } from "@/components/glossaire";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const champ = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm";

/**
 * Les agents de l'organisation (ADR 0033) — le point 3 de la revue produit : « c'est censé être une
 * plateforme agentique, où sont les agents ? ». Ceux du registre, internes ou externes, et les
 * clients MCP de l'appelant : un Claude Code qui a appelé la porte se voit ici, et s'enregistre.
 */
export default function AgentsPage() {
  const { org } = useSession();
  const agents = useQuery({ queryKey: ["agents", org], queryFn: () => api.agents(org) });
  const jetons = useQuery({ queryKey: ["tokens"], queryFn: () => api.myTokens() });
  const clients = (jetons.data ?? []).filter(estUnClientMcp);
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Eyebrow>who does the work</Eyebrow>
        <Heading as="h1" size="xl">
          agents
        </Heading>
        <p className="max-w-3xl text-sm text-ink-muted">
          An agent is an object of the organisation: its instructions, model, skills and tools live in versions that
          never change; a project pins one and may only tighten it. An <em>external</em> agent is an assistant — a
          Claude Code, a Cursor — that reaches in through the MCP door and acts with its human&apos;s rights, never
          more.
        </p>
        <Glossaire ici="agents" />
      </div>
      <Card title="registry">
        {agents.error ? (
          <ErrorNote>{String(agents.error)}</ErrorNote>
        ) : !agents.data ? (
          <p className="text-sm text-ink-muted">reading the registry…</p>
        ) : agents.data.length === 0 ? (
          <Empty title="no agent yet">register one below, or from a project&apos;s implicit agents.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="registre">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">agent</th>
                  <th className="py-1 font-normal">kind</th>
                  <th className="py-1 font-normal">status</th>
                  <th className="py-1 font-normal">version</th>
                  <th className="py-1 font-normal">owner</th>
                </tr>
              </thead>
              <tbody>
                {agents.data.map((agent) => (
                  <tr key={agent.slug} className="border-t border-line" data-testid={`agent-${agent.slug}`}>
                    <td className="py-2">
                      <Link href={`/agents/${agent.slug}`} className="font-medium hover:underline">
                        {agent.display_name}
                      </Link>
                      <span className="ml-2 text-xs text-ink-muted">{agent.slug}</span>
                    </td>
                    <td>
                      <Badge tone={agent.kind === "external" ? "accent" : "neutral"}>{agent.kind}</Badge>
                    </td>
                    <td>
                      <Badge tone={agent.status === "active" ? "ok" : "danger"}>{agent.status}</Badge>
                    </td>
                    <td>
                      v{agent.latest_version}
                      <span className="ml-2 text-xs text-ink-muted">
                        {resumeDeLaVersion(agent.versions?.find((v) => v.version === agent.latest_version)?.spec)}
                      </span>
                    </td>
                    <td className="text-xs text-ink-muted">{agent.owner ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <CatalogueDAgents org={org} />
      <ClientsMcp org={org} clients={clients} agents={agents.data ?? []} />
      <NouvelAgent org={org} />
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
    <Card title="new agent">
      <form
        className="grid gap-3 md:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          void creer();
        }}
      >
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">slug</span>
          <input
            className={champ}
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            pattern="[a-z0-9][a-z0-9-]*"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">name</span>
          <input className={champ} value={nom} onChange={(e) => setNom(e.target.value)} />
        </label>
        <label className="space-y-1 md:col-span-2">
          <span className="text-xs text-ink-muted">instructions (what the frame of the platform does not say)</span>
          <textarea
            className={`${champ} font-mono text-xs`}
            rows={6}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">model (empty: the project&apos;s profile)</span>
          <input
            className={champ}
            value={modele}
            onChange={(e) => setModele(e.target.value)}
            placeholder="profile:standard"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-ink-muted">daily budget (USD, empty: none)</span>
          <input
            className={champ}
            type="number"
            min={0}
            step="0.5"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-3 md:col-span-2">
          <Button type="submit" tone="accent" disabled={!slug}>
            register
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
    </Card>
  );
}
