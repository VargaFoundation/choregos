// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@varga/design-system";
import { estUneReference } from "@/components/connecteurs";
import { resumeDeLaDecouverte } from "@/components/decouverte";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ConnectorOperation, OperationPatch, OrgConnector } from "@/lib/types";

const POLITIQUES = ["allowed", "approval", "forbidden"] as const;
const TON = { allowed: "ok", approval: "warn", forbidden: "danger" } as const;

/**
 * Les connecteurs de l'organisation (ADR 0034) : un annuaire, un gestionnaire de parc, un serveur
 * MCP — déclarés une fois, nommés ; pour chaque opération, la politique et les groupes de projets
 * qui y ont droit. Seul l'administrateur de l'organisation en décide ; un projet ne fait que
 * resserrer.
 */
export default function OrgConnectorsPage() {
  const { org } = useSession();
  const instances = useQuery({ queryKey: ["org-connectors", org], queryFn: () => api.orgConnectors(org) });
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-ink-muted">
        A connector of the organisation is declared once and shared by its projects. Each operation is allowed,
        needs an approval (a governed action), or is forbidden — and opens to the project groups you name (none:
        every project). A project can tighten an operation, never widen it.
      </p>
      {instances.error ? (
        <ErrorNote>{String(instances.error)}</ErrorNote>
      ) : !instances.data ? (
        <p className="text-sm text-ink-muted">reading…</p>
      ) : instances.data.length === 0 ? (
        <Empty title="no connector yet">declare one below.</Empty>
      ) : (
        instances.data.map((instance) => <Instance key={instance.name} org={org} instance={instance} />)
      )}
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
      <Badge tone="neutral">{instance.kind}</Badge>
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
  const candidats = (types.data ?? []).filter((t) => !["tracker", "scm", "ci", "cd", "runtime", "memory", "gateway"].includes(t.kind));
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
                {candidats.map((t) => (
                  <option key={`${t.kind}/${t.type}`} value={`${t.kind}/${t.type}`}>
                    {t.display} ({t.kind})
                  </option>
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
