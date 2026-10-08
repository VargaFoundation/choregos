// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@varga/design-system";
import { estUneReference } from "@/components/connecteurs";
import { OutilsDeLivraison, rangerLesConnecteurs } from "@/components/connecteurs-org";
import { resumeDeLaDecouverte } from "@/components/decouverte";
import { Glossaire } from "@/components/glossaire";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { estUneSorteDuProjet, libelleDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorOperation, OperationPatch, OrgConnector } from "@/lib/types";

const POLITIQUES = ["allowed", "approval", "forbidden"] as const;
const TON = { allowed: "ok", approval: "warn", forbidden: "danger" } as const;

/**
 * Les connecteurs (ADR 0034), rangés par ce qu'ils SONT (revue du 07/10 : « ça mélange des agents,
 * des MCPs et des connecteurs API, c'est flou ») : les systèmes métier de l'organisation, qui déclarent
 * leurs opérations ; ses serveurs MCP, dont on découvre les outils ; et, en lecture, les outils de
 * livraison de chaque projet, qui se règlent dans le projet. Seul l'administrateur de l'organisation
 * décide des deux premiers ; un projet ne fait que resserrer.
 */
export default function OrgConnectorsPage() {
  const { org } = useSession();
  const instances = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  const { metier, mcp } = rangerLesConnecteurs(instances.data ?? []);
  const lecture = instances.error ? (
    <ErrorNote>{String(instances.error)}</ErrorNote>
  ) : !instances.data ? (
    <p className="text-sm text-ink-muted">reading…</p>
  ) : null;
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <p className="max-w-3xl text-sm text-ink-muted">
          A connector is a system Choregos reaches out to. Those of the organisation are declared once and shared by its
          projects: each operation is allowed, needs an approval (a governed action), or is forbidden — and opens to the
          project groups you name (none: every project). A project can tighten an operation, never widen it.
        </p>
        <Glossaire ici="connectors" ouvert />
      </div>
      <section className="space-y-3" aria-labelledby="business" data-testid="section-metier">
        <h2 id="business" className="text-lg font-semibold">
          business systems (API)
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Systems your agents act on through the operations their type declares — a directory, a device fleet, a carrier.
        </p>
        {lecture ??
          (metier.length === 0 ? (
            <Empty title="no business system yet">declare one below.</Empty>
          ) : (
            metier.map((instance) => <Instance key={instance.name} org={org} instance={instance} />)
          ))}
      </section>
      <section className="space-y-3" aria-labelledby="mcp" data-testid="section-mcp">
        <h2 id="mcp" className="text-lg font-semibold">
          MCP servers
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Tool servers Choregos calls over MCP: discover lists their tools, and each new tool is born closed. A
          supplier&apos;s agent served over MCP is declared here — to Choregos it is a tool server. To let{" "}
          <em>your</em> assistant call Choregos, see{" "}
          <Link href="/integrations" className="underline">
            AI clients
          </Link>
          .
        </p>
        {lecture ??
          (mcp.length === 0 ? (
            <Empty title="no MCP server yet">declare one below, type “MCP server”.</Empty>
          ) : (
            mcp.map((instance) => <Instance key={instance.name} org={org} instance={instance} />)
          ))}
      </section>
      <OutilsDeLivraison org={org} />
      <Declarer org={org} />
    </div>
  );
}

function Instance({ org, instance }: { org: string; instance: OrgConnector }) {
  const client = useQueryClient();
  const [diff, setDiff] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  async function decouvrir() {
    setErreur(null);
    setDiff(null);
    try {
      setDiff(resumeDeLaDecouverte(await api.discoverConnectorOperations(org, instance.name)));
      await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "discovery failed");
    }
  }
  const action = (
    <span className="inline-flex items-center gap-2">
      <Badge tone="neutral">{libelleDeSorte(instance.kind)}</Badge>
      {instance.kind === "mcp" && (
        <Button size="sm" onClick={() => void decouvrir()}>
          discover
        </Button>
      )}
    </span>
  );
  return (
    <Card title={`${instance.name} — ${instance.type}`} action={action}>
      {diff && (
        <p className="mb-2 text-sm" role="status">
          {diff}
        </p>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
      {(instance.operations ?? []).length === 0 ? (
        <p className="text-sm text-ink-muted">
          {instance.kind === "mcp"
            ? "no tool known yet: discover asks the server; each new tool is born closed."
            : "this type declares no operation an agent can call."}
        </p>
      ) : (
        <table className="w-full text-sm" data-testid={`operations-${instance.name}`}>
          <thead className="text-left text-xs text-ink-muted">
            <tr>
              <th className="py-1 font-normal">operation</th>
              <th className="py-1 font-normal">access</th>
              <th className="py-1 font-normal">policy</th>
              <th className="py-1 font-normal">project groups</th>
            </tr>
          </thead>
          <tbody>
            {(instance.operations ?? []).map((operation) => (
              <Operation key={operation.name} org={org} connecteur={instance.name} operation={operation} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Operation({ org, connecteur, operation }: { org: string; connecteur: string; operation: ConnectorOperation }) {
  const client = useQueryClient();
  const [groupes, setGroupes] = useState((operation.groups ?? []).join(", "));
  const [erreur, setErreur] = useState<string | null>(null);
  async function changer(corps: OperationPatch) {
    setErreur(null);
    try {
      await api.updateConnectorOperation(org, connecteur, operation.name, corps);
      await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  const lus = groupes
    .split(",")
    .map((g) => g.trim())
    .filter(Boolean);
  return (
    <tr className="border-t border-line">
      <td className="py-2">
        <code>{operation.name}</code>
        {operation.description && <p className="text-xs text-ink-muted">{operation.description}</p>}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </td>
      <td>
        <Badge tone={operation.access === "write" ? "warn" : "neutral"}>{operation.access}</Badge>
      </td>
      <td>
        <label className="sr-only" htmlFor={`politique-${connecteur}-${operation.name}`}>
          policy of {operation.name}
        </label>
        <select
          id={`politique-${connecteur}-${operation.name}`}
          className="rounded border border-line bg-surface px-2 py-1 text-sm"
          value={operation.policy}
          onChange={(event) => void changer({ policy: event.target.value as OperationPatch["policy"] })}
        >
          {POLITIQUES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <span className="ml-2">
          <Badge tone={TON[operation.policy as keyof typeof TON] ?? "neutral"}>{operation.policy}</Badge>
        </span>
      </td>
      <td>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void changer({ groups: lus });
          }}
        >
          <label className="sr-only" htmlFor={`groupes-${connecteur}-${operation.name}`}>
            project groups of {operation.name}
          </label>
          <input
            id={`groupes-${connecteur}-${operation.name}`}
            className="w-40 rounded border border-line bg-surface px-2 py-1 text-xs"
            placeholder="every project"
            value={groupes}
            onChange={(event) => setGroupes(event.target.value)}
          />
          <Button size="sm" type="submit" disabled={lus.join(",") === (operation.groups ?? []).join(",")}>
            set
          </Button>
        </form>
      </td>
    </tr>
  );
}

/** Déclarer une instance : son type (ceux qui exposent des opérations d'abord), sa configuration, ses secrets. */
function Declarer({ org }: { org: string }) {
  const client = useQueryClient();
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
  const [nom, setNom] = useState("");
  const [choix, setChoix] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [references, setReferences] = useState<Record<string, string>>({});
  const [erreur, setErreur] = useState<string | null>(null);
  // Les outils de livraison d'un projet se règlent dans le projet : ils ne se déclarent pas ici.
  const candidats = (types.data ?? []).filter((t) => !estUneSorteDuProjet(t.kind));
  const familles = [
    { titre: "business systems (API)", types: candidats.filter((t) => t.kind !== "mcp") },
    { titre: "MCP servers", types: candidats.filter((t) => t.kind === "mcp") },
  ].filter((famille) => famille.types.length > 0);
  const type = candidats.find((t) => `${t.kind}/${t.type}` === choix);
  const schema = (type?.config_schema ?? { type: "object", properties: {} }) as JsonSchema;
  const illisibles = Object.values(references).filter((v) => v && !estUneReference(v));
  async function declarer() {
    if (!type) return;
    setErreur(null);
    try {
      await api.createOrgConnector(org, {
        name: nom,
        type: type.type,
        kind: type.kind,
        config,
        secret_refs: Object.fromEntries(Object.entries(references).filter(([, v]) => v)),
      });
      setNom("");
      setChoix("");
      setConfig({});
      setReferences({});
      await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <Card title="declare a connector">
      {candidats.length === 0 ? (
        <p className="text-sm text-ink-muted">
          no connector type exposes operations yet: a plugin (or the <code>mcp</code> type) brings them.
        </p>
      ) : (
        <form
          className="space-y-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void declarer();
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">name</span>
              <input
                className="w-full rounded border border-line bg-surface px-2 py-1.5"
                value={nom}
                onChange={(e) => setNom(e.target.value)}
                placeholder="entra-acme"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">type</span>
              <select
                className="w-full rounded border border-line bg-surface px-2 py-1.5"
                value={choix}
                onChange={(e) => {
                  setChoix(e.target.value);
                  setConfig({});
                  setReferences({});
                }}
              >
                <option value="">—</option>
                {familles.map((famille) => (
                  <optgroup key={famille.titre} label={famille.titre}>
                    {famille.types.map((t) => (
                      <option key={`${t.kind}/${t.type}`} value={`${t.kind}/${t.type}`}>
                        {t.display} — {libelleDeSorte(t.kind)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>
          {type && <SchemaForm schema={schema} value={config} onChange={setConfig} />}
          {(type?.secret_fields ?? []).map((champ) => (
            <label key={champ} className="block space-y-1">
              <span className="text-xs text-ink-muted">{champ.replaceAll("_", " ")} (by reference)</span>
              <input
                className="w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs"
                placeholder="env:NAME"
                value={references[champ] ?? ""}
                onChange={(event) => setReferences({ ...references, [champ]: event.target.value })}
              />
            </label>
          ))}
          {illisibles.length > 0 && (
            <p className="text-xs text-danger" role="alert">
              a reference is expected (env:NAME), not a value
            </p>
          )}
          {erreur && <ErrorNote>{erreur}</ErrorNote>}
          <Button
            type="submit"
            tone="accent"
            disabled={!nom || !type || champsManquants(schema, config).length > 0 || illisibles.length > 0}
          >
            declare
          </Button>
        </form>
      )}
    </Card>
  );
}
