// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { slugDeLActeur } from "@/components/agents/registre";
import { Badge, Button, Card, Empty, ErrorNote, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Agent, AgentSpec, ApiToken } from "@/lib/types";

/** Ce qu'un agent externe en lecture seule peut appeler à la porte : lire, chercher, résumer. */
export const OUTILS_EN_LECTURE = ["list_*", "describe_*", "search_*", "get_*", "summarize_*"];

/** La version 1 d'un agent externe : ce que son humain peut, ou seulement lire. */
export function versionDuClient(lecture: boolean): AgentSpec {
  return lecture ? { mcp_servers: [{ connector: "choregos", tools: OUTILS_EN_LECTURE }] } : {};
}

/**
 * Les clients MCP de l'appelant — ses jetons `mcp:*` (ADR 0030) — et l'agent externe que chacun
 * incarne. Un Claude Code qui a appelé la porte apparaît ici ; l'enregistrer en fait un agent du
 * registre, dont les droits sont ceux de son humain, intersectés avec ce que sa version nomme.
 */
export function ClientsMcp({ org, clients, agents }: { org: string; clients: ApiToken[]; agents: Agent[] }) {
  // One request for every external agent's clients (S30-04): it was one per agent.
  const rattachements = useQuery({ queryKey: ["agent-credentials", org], queryFn: () => api.orgAgentCredentials(org) });
  const parSlug = new Map(agents.map((agent) => [agent.slug, agent]));
  const agentDuJeton = new Map<string, Agent>();
  for (const lien of rattachements.data ?? []) {
    const agent = lien.agent ? parSlug.get(lien.agent) : undefined;
    if (agent && lien.token_id && !lien.revoked_at) agentDuJeton.set(lien.token_id, agent);
  }
  return (
    <Card title="Your AI clients">
      <p className="mb-3 text-xs text-ink-muted">
        Your access tokens for the MCP door, and the AI client behind each once it has called. Registering one
        makes it an external agent of the registry. Every token is listed, and revoked, in{" "}
        <Link href="/admin">Admin › My access tokens</Link>.
      </p>
      {clients.length === 0 ? (
        <Empty title="No AI client">Connect Claude Code or another client above: it appears here once it has called.</Empty>
      ) : (
        <ul className="space-y-3" data-testid="clients-mcp">
          {clients.map((jeton) => (
            <ClientMcp key={jeton.id} org={org} jeton={jeton} agent={agentDuJeton.get(jeton.id)} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ClientMcp({ org, jeton, agent }: { org: string; jeton: ApiToken; agent?: Agent }) {
  const client = useQueryClient();
  const [ouvert, setOuvert] = useState(false);
  const [slug, setSlug] = useState(() => slugDeLActeur(jeton.name));
  const [nom, setNom] = useState(jeton.name);
  const [lecture, setLecture] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  async function enregistrer() {
    setErreur(null);
    try {
      await api.createAgent(org, { slug, kind: "external", display_name: nom || slug, spec: versionDuClient(lecture) });
      await api.attachAgentToken(org, slug, jeton.id);
      setOuvert(false);
      await client.invalidateQueries({ queryKey: ["agents", org] });
      await client.invalidateQueries({ queryKey: ["agent-credentials", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "registration refused");
    }
  }
  return (
    <li className="space-y-2 rounded border border-line p-3" data-testid={`client-${jeton.id}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{jeton.name}</span>
        {jeton.last_used_at ? (
          <Badge tone="ok">Connected</Badge>
        ) : (
          <Badge tone="neutral">Never called</Badge>
        )}
        <span className="text-xs text-ink-muted">
          {jeton.last_used_at ? `Last call ${relative(jeton.last_used_at)}` : "No call yet"}
          {jeton.last_client ? ` · ${jeton.last_client}` : ""}
          {` · ${jeton.scopes.join(", ")}`}
          {jeton.project ? ` · ${jeton.project}` : ""}
        </span>
        <span className="ml-auto">
          {agent ? (
            <Link href={`/agents/${agent.slug}`} className="text-sm hover:underline">
              Acts as {agent.display_name}
            </Link>
          ) : (
            <Button size="sm" onClick={() => setOuvert(!ouvert)}>
              {ouvert ? "Cancel" : "Register as an external agent"}
            </Button>
          )}
        </span>
      </div>
      {ouvert && !agent && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void enregistrer();
          }}
        >
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Agent slug</span>
            <Input
              className="w-auto"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Name</span>
            <Input
              className="w-auto"
              value={nom}
              onChange={(e) => setNom(e.target.value)}
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-ink-muted">Tools</span>
            <Select
              className="w-auto"
              value={lecture ? "lecture" : "humain"}
              onChange={(e) => setLecture(e.target.value === "lecture")}
            >
              <option value="humain">What its human may use</option>
              <option value="lecture">Read only</option>
            </Select>
          </label>
          <Button size="sm" type="submit" tone="accent" disabled={!slug}>
            Register
          </Button>
        </form>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}
