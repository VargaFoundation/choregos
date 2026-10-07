// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@varga/design-system";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { estClient, extrait, urlDeLaPorte } from "@/lib/integrations";
import type { AgentCatalogueConnection, AgentCatalogueEntry } from "@/lib/types";

/** L'état d'une entrée dans l'organisation, en clair : ce que le bouton fera. */
export function etatDeLEntree(entree: AgentCatalogueEntry): { libelle: string; action: "install" | "update" | null } {
  if (entree.own_agent) return { libelle: "your organisation has its own agent with this name", action: null };
  if (!entree.installed) return { libelle: "not installed", action: "install" };
  if (entree.update_available) return { libelle: `installed v${entree.installed_version} — an update is available`, action: "update" };
  return { libelle: `installed v${entree.installed_version}`, action: null };
}

/**
 * Le catalogue de la plateforme (ADR 0040) — la revue du 07/10 ne trouvait dans `/agents` que les
 * coordinateurs RH. Les agents prêts à installer, un par rôle ; puis les assistants qui se connectent
 * en un clic, et ceux qui ne le peuvent pas encore ici, avec la raison.
 */
export function CatalogueDAgents({ org }: { org: string }) {
  const catalogue = useQuery({ queryKey: ["agent-catalogue", org], queryFn: () => api.agentCatalogue(org) });
  if (catalogue.error) return <ErrorNote>{String(catalogue.error)}</ErrorNote>;
  const entrees = catalogue.data ?? [];
  const internes = entrees.filter((e) => e.kind === "internal");
  const clients = entrees.filter((e) => e.kind === "external");
  return (
    <>
      <Card title="catalogue — ready-made agents">
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Agents the platform ships, one per role. Installing makes one an agent of your organisation, version 1: it uses
          your project&apos;s model and runtime, and you can tune it by publishing your own versions. A workflow that names
          one installs it by itself.
        </p>
        {!catalogue.data ? (
          <p className="text-sm text-ink-muted">reading the catalogue…</p>
        ) : internes.length === 0 ? (
          <Empty title="empty catalogue">this deployment ships no agent.</Empty>
        ) : (
          <table className="w-full text-sm" data-testid="catalogue">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="py-1 font-normal">agent</th>
                <th className="py-1 font-normal">role</th>
                <th className="py-1 font-normal">what it does</th>
                <th className="py-1 font-normal">in your organisation</th>
                <th className="py-1 font-normal" />
              </tr>
            </thead>
            <tbody>
              {internes.map((entree) => (
                <LigneDuCatalogue key={entree.slug} org={org} entree={entree} />
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="connect an AI client">
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Assistants that reach in through the MCP gate, with your rights, never more — they never decide. Connecting one
          registers it as an external agent and gives you its configuration, once.{" "}
          <Link href="/integrations" className="underline">
            all clients and their set-up
          </Link>
        </p>
        <ul className="space-y-2" data-testid="clients-du-catalogue">
          {clients.map((entree) => (
            <ClientDuCatalogue key={entree.slug} org={org} entree={entree} />
          ))}
        </ul>
      </Card>
    </>
  );
}

function LigneDuCatalogue({ org, entree }: { org: string; entree: AgentCatalogueEntry }) {
  const client = useQueryClient();
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  const etat = etatDeLEntree(entree);
  async function agir() {
    setErreur(null);
    setOccupe(true);
    try {
      await api.installCatalogueAgent(org, entree.slug, etat.action === "update");
      await client.invalidateQueries({ queryKey: ["agent-catalogue", org] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    } finally {
      setOccupe(false);
    }
  }
  return (
    <tr className="border-t border-line align-top" data-testid={`catalogue-${entree.slug}`}>
      <td className="py-2 pr-2">
        {entree.installed ? (
          <Link href={`/agents/${entree.slug}`} className="font-medium hover:underline">
            {entree.display_name}
          </Link>
        ) : (
          <span className="font-medium">{entree.display_name}</span>
        )}
        {entree.skills && entree.skills.length > 0 && (
          <span className="block text-xs text-ink-muted">carries {entree.skills.join(", ")}</span>
        )}
      </td>
      <td className="py-2 pr-2">
        <Badge tone="neutral">{entree.role}</Badge>
      </td>
      <td className="py-2 pr-2 text-xs text-ink-muted">{entree.summary}</td>
      <td className="py-2 pr-2 text-xs">{etat.libelle}</td>
      <td className="py-2 text-right">
        {etat.action && (
          <Button size="sm" onClick={() => void agir()} disabled={occupe}>
            {etat.action}
          </Button>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </td>
    </tr>
  );
}

function ClientDuCatalogue({ org, entree }: { org: string; entree: AgentCatalogueEntry }) {
  const client = useQueryClient();
  const infos = useQuery({ queryKey: ["integrations"], queryFn: () => api.integrations(), staleTime: Infinity });
  const [lecture, setLecture] = useState(false);
  const [connexion, setConnexion] = useState<AgentCatalogueConnection | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  async function connecter() {
    setErreur(null);
    try {
      setConnexion(await api.connectCatalogueClient(org, entree.slug, lecture));
      await client.invalidateQueries({ queryKey: ["agents", org] });
      await client.invalidateQueries({ queryKey: ["me-tokens"] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "connection refused");
    }
  }
  const id = entree.client ?? entree.slug;
  const url = infos.data ? urlDeLaPorte(infos.data.mcp_url) : "…";
  const code = connexion?.token && estClient(id) ? extrait(id, url, connexion.token.token) : null;
  return (
    <li className="space-y-2 rounded border border-line p-3" data-testid={`client-catalogue-${entree.slug}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{entree.display_name}</span>
        <span className="text-xs text-ink-muted">
          {entree.reach === "cloud" ? "calls from its vendor's cloud" : "calls from your machine"}
        </span>
        {entree.offered ? (
          <span className="ml-auto inline-flex items-center gap-3">
            {entree.reach !== "cloud" && (
              <label className="inline-flex items-center gap-1 text-xs text-ink-muted">
                <input type="checkbox" checked={lecture} onChange={(e) => setLecture(e.target.checked)} />
                read only
              </label>
            )}
            <Button size="sm" onClick={() => void connecter()} disabled={Boolean(connexion)}>
              {connexion ? "connected" : "connect"}
            </Button>
          </span>
        ) : (
          <span className="ml-auto">
            <Badge tone="neutral">not available here</Badge>
          </span>
        )}
      </div>
      {!entree.offered && entree.unavailable_reason && (
        <p className="text-xs text-ink-muted">why: {entree.unavailable_reason}</p>
      )}
      {connexion?.oauth_client_id && (
        <p className="text-sm" role="status">
          Registered as an external agent: add Choregos in {entree.display_name} with the address {url} — it signs you in.
        </p>
      )}
      {code && (
        <div className="space-y-1" data-testid={`extrait-${entree.slug}`}>
          <p className="text-xs text-ink-muted">
            {code.where} — the token is shown once; it is not kept here.
          </p>
          <pre className="overflow-x-auto rounded border border-line bg-surface-muted p-2 text-xs">{code.code}</pre>
        </div>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}
