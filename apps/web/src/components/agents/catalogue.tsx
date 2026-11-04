// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { TagList } from "@/components/agents/tags";
import { estClient, extrait, gateUrl } from "@/lib/integrations";
import type { AgentCatalogueConnection, AgentCatalogueEntry } from "@/lib/types";

/** The state of an entry in the organisation, in plain words: what the button will do. */
export function entryState(entry: AgentCatalogueEntry): { libelle: string; action: "install" | "update" | null } {
  if (entry.own_agent) return { libelle: "Your organisation has its own agent with this name", action: null };
  if (!entry.installed) return { libelle: "Not installed", action: "install" };
  if (entry.update_available)
    return { libelle: `Installed v${entry.installed_version} — an update is available`, action: "update" };
  return { libelle: `Installed v${entry.installed_version}`, action: null };
}

/**
 * The platform's catalogue (ADR 0040): the review of 7 Oct found only the HR
 * coordinators in `/agents`. The agents ready to install, one per role; then the assistants that connect
 * in one click, and those that cannot yet here, with the reason.
 */
export function AgentCatalogue({ org }: { org: string }) {
  const catalogue = useQuery({ queryKey: ["agent-catalogue", org], queryFn: () => api.agentCatalogue(org) });
  if (catalogue.error) return <ErrorNote>{String(catalogue.error)}</ErrorNote>;
  const internes = (catalogue.data ?? []).filter((e) => e.kind === "internal");
  return (
      <div>
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Agents the platform ships, one per role. Installing makes one an agent of your organisation, version 1: it
          uses your project&apos;s model and runtime, and you can tune it by publishing your own versions. A workflow
          that names one installs it by itself.
        </p>
        {!catalogue.data ? (
          <p className="text-sm text-ink-muted">Reading the catalogue…</p>
        ) : internes.length === 0 ? (
          <Empty title="Empty catalogue">This deployment ships no agent.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="catalogue">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">Agent</th>
                  <th className="py-1 font-normal">Role</th>
                  <th className="py-1 font-normal">What it does</th>
                  <th className="py-1 font-normal">In your organisation</th>
                  <th className="py-1 font-normal">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {internes.map((entry) => (
                  <CatalogueRow key={entry.slug} org={org} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
  );
}

/**
 * The assistants that connect in one click (ADR 0040): on the AI clients' page since S30-04 — the
 * agents' page is the registry, and everything about clients and tokens lives in one place.
 */
export function AiClientCatalogue({ org }: { org: string }) {
  const catalogue = useQuery({ queryKey: ["agent-catalogue", org], queryFn: () => api.agentCatalogue(org) });
  if (catalogue.error) return <ErrorNote>{String(catalogue.error)}</ErrorNote>;
  const clients = (catalogue.data ?? []).filter((e) => e.kind === "external");
  return (
      <Card title="Connect an AI client">
        <p className="mb-3 max-w-3xl text-sm text-ink-muted">
          Assistants that reach in through the MCP door — they never decide. Connecting one here registers it as an
          external agent: its calls carry the agent&apos;s name and limits, within your rights, and you get its
          configuration once. To connect one simply as yourself, use the steps above.
        </p>
        <ul className="space-y-2" data-testid="catalogue-clients">
          {clients.map((entry) => (
            <ClientDuCatalogue key={entry.slug} org={org} entry={entry} />
          ))}
        </ul>
      </Card>
  );
}

function CatalogueRow({ org, entry }: { org: string; entry: AgentCatalogueEntry }) {
  const client = useQueryClient();
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  const state = entryState(entry);
  async function agir() {
    setErreur(null);
    setOccupe(true);
    try {
      await api.installCatalogueAgent(org, entry.slug, state.action === "update");
      await client.invalidateQueries({ queryKey: ["agent-catalogue", org] });
      await client.invalidateQueries({ queryKey: ["agents", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    } finally {
      setOccupe(false);
    }
  }
  return (
    <tr className="border-t border-line align-top" data-testid={`catalogue-${entry.slug}`}>
      <td className="py-2 pr-2">
        {entry.installed ? (
          <Link href={`/agents/${entry.slug}`} className="font-medium hover:underline">
            {entry.display_name}
          </Link>
        ) : (
          <span className="font-medium">{entry.display_name}</span>
        )}
        {entry.skills && entry.skills.length > 0 && (
          <span className="block text-xs text-ink-muted">Carries {entry.skills.join(", ")}</span>
        )}
        <TagList tags={entry.tags} />
      </td>
      <td className="py-2 pr-2">
        <Badge tone="neutral">{entry.role}</Badge>
      </td>
      <td className="py-2 pr-2 text-xs text-ink-muted">{entry.summary}</td>
      <td className="py-2 pr-2 text-xs">{state.libelle}</td>
      <td className="py-2 text-right">
        {state.action && (
          <Button size="sm" onClick={() => void agir()} disabled={occupe}>
            {state.action === "update" ? "Update" : "Install"}
          </Button>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </td>
    </tr>
  );
}

function ClientDuCatalogue({ org, entry }: { org: string; entry: AgentCatalogueEntry }) {
  const client = useQueryClient();
  const infos = useQuery({ queryKey: ["integrations"], queryFn: () => api.integrations(), staleTime: Infinity });
  const [lecture, setLecture] = useState(false);
  const [connexion, setConnexion] = useState<AgentCatalogueConnection | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  async function connecter() {
    setErreur(null);
    try {
      setConnexion(await api.connectCatalogueClient(org, entry.slug, lecture));
      await client.invalidateQueries({ queryKey: ["agents", org] });
      await client.invalidateQueries({ queryKey: ["tokens"] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "connection refused");
    }
  }
  const id = entry.client ?? entry.slug;
  const url = infos.data ? gateUrl(infos.data.mcp_url) : "…";
  const code = connexion?.token && estClient(id) ? extrait(id, url, connexion.token.token) : null;
  return (
    <li className="space-y-2 rounded border border-line p-3" data-testid={`client-catalogue-${entry.slug}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{entry.display_name}</span>
        <span className="text-xs text-ink-muted">
          {entry.reach === "cloud" ? "Calls from its vendor's cloud" : "Calls from your machine"}
        </span>
        {entry.offered ? (
          <span className="ml-auto inline-flex items-center gap-3">
            {entry.reach !== "cloud" && (
              <label className="inline-flex items-center gap-1 text-xs text-ink-muted">
                <input type="checkbox" checked={lecture} onChange={(e) => setLecture(e.target.checked)} />
                Read only
              </label>
            )}
            <Button size="sm" onClick={() => void connecter()} disabled={Boolean(connexion)}>
              {connexion ? "Connected" : "Connect"}
            </Button>
          </span>
        ) : (
          <span className="ml-auto">
            <Badge tone="neutral">Not available here</Badge>
          </span>
        )}
      </div>
      {!entry.offered && entry.unavailable_reason && (
        <p className="text-xs text-ink-muted">Why: {entry.unavailable_reason}</p>
      )}
      {connexion?.oauth_client_id && (
        <p className="text-sm" role="status">
          Registered as an external agent: add Choregos in {entry.display_name} with the address {url} — it signs you
          in.
        </p>
      )}
      {code && (
        <div className="space-y-1" data-testid={`extrait-${entry.slug}`}>
          <p className="text-xs text-ink-muted">{code.where} — the token is shown once; it is not kept here.</p>
          <pre className="overflow-x-auto rounded border border-line bg-surface-muted p-2 text-xs">{code.code}</pre>
        </div>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
    </li>
  );
}
