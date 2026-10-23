// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, useSyncExternalStore } from "react";
import { OutilsDeLivraison, rangerLesConnecteurs } from "@/components/connecteurs-org";
import { resumeDeLaDecouverte } from "@/components/decouverte";
import { egressFlag, useEgressGuard } from "@/components/egress/egress-provider";
import { Glossaire } from "@/components/glossaire";
import { ProcedurePanel } from "@/components/procedures/procedure-panel";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import { EMPTY_DRAFT, SecretField, bodyOfDrafts, problemOfDraft, type SecretDraft } from "@/components/secret-field";
import { Badge, Button, Card, Empty, ErrorNote, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { connectorTarget, presetOfMcpUrl } from "@/lib/procedures";
import { useSession } from "@/lib/session";
import { estUneSorteDuProjet, libelleDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorOperation, ConnectorSecretState, OperationPatch, OrgConnector } from "@/lib/types";
import { Confirmation } from "@/components/geste-confirme";

const POLITIQUES = ["allowed", "approval", "forbidden"] as const;
const TON = { allowed: "ok", approval: "warn", forbidden: "danger" } as const;
/** Du plus libre au plus strict : passer à un rang plus bas assouplit, et se confirme (S23-07). */
const RIGUEUR = { allowed: 0, approval: 1, forbidden: 2 } as const;
const CE_QUE_CA_PERMET = {
  allowed: "agents run it without asking anyone",
  approval: "agents run it once a person approves",
  forbidden: "nobody runs it",
} as const;

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
  // Landing from the catalogue (ADR 0065): the connector just installed is marked, with what its
  // first discovery found.
  const arrivee = useArrivee();
  const { metier, mcp } = rangerLesConnecteurs(instances.data ?? []);
  const lecture = instances.error ? (
    <ErrorNote>{String(instances.error)}</ErrorNote>
  ) : !instances.data ? (
    <p className="text-sm text-ink-muted">Reading…</p>
  ) : null;
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Installed connectors</h1>
      <p className="max-w-3xl text-sm text-ink-muted">
        Connectors this organisation has installed. Projects enable them; project delivery tools (tracker, scm, ci, cd,
        notify) are configured in each project.
      </p>
      <aside
        className="max-w-3xl rounded border border-line bg-surface px-3 py-2 text-sm"
        aria-label="Add from the catalogues"
        data-testid="add-from-catalogues"
      >
        <strong>Add from the catalogues</strong>
        <span className="text-ink-muted"> — </span>
        <Link href="/catalogue/mcp-servers" className="underline">
          Browse MCP servers
        </Link>
        <span className="text-ink-muted"> · </span>
        <Link href="/catalogue/connectors" className="underline">
          Browse connector types
        </Link>
      </aside>
      {arrivee && (
        <p className="max-w-3xl text-sm" role="status" data-testid="installed-from-catalogue">
          Installed <strong data-donnee>{arrivee.name}</strong> from the catalogue.
          {arrivee.discovery ? ` Tools: ${arrivee.discovery}.` : ""} Open the tools your agents need below.
        </p>
      )}
      <div className="space-y-3">
        <p className="max-w-3xl text-sm text-ink-muted">
          Each operation of an installed connector is allowed, needs an approval (a governed action), or is forbidden
          — and opens to the project groups you name (none: every project). A project can tighten an operation, never
          widen it.
        </p>
        <Glossaire ici="connectors" ouvert />
      </div>
      <section className="space-y-3" aria-labelledby="business" data-testid="section-metier">
        <h2 id="business" className="text-lg font-semibold">
          Business systems (API) ({metier.length})
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Systems your agents act on through the operations their type declares — a directory, a device fleet, a
          carrier.
        </p>
        {lecture ??
          (metier.length === 0 ? (
            <Empty title="No business system yet">Declare one below.</Empty>
          ) : (
            metier.map((instance) => <Instance key={instance.name} org={org} instance={instance} />)
          ))}
      </section>
      <section className="space-y-3" aria-labelledby="mcp" data-testid="section-mcp">
        <h2 id="mcp" className="text-lg font-semibold">
          MCP servers ({mcp.length})
        </h2>
        <p className="max-w-3xl text-sm text-ink-muted">
          Tool servers Choregos calls over MCP: discover lists their tools, and each new tool is born closed. A
          supplier&apos;s agent served over MCP is declared here — to Choregos it is a tool server. To let <em>your</em>{" "}
          assistant call Choregos, see{" "}
          <Link href="/integrations" className="underline">
            Integrations
          </Link>
          .
        </p>
        {lecture ??
          (mcp.length === 0 ? (
            <Empty
              title="No MCP server yet"
              action={
                <Link href="/catalogue/mcp-servers" className="text-sm underline">
                  Browse the MCP catalogue
                </Link>
              }
            >
              Pick one from the catalogue, or declare one below, type “MCP server”.
            </Empty>
          ) : (
            mcp.map((instance) => (
              <Instance key={instance.name} org={org} instance={instance} highlighted={instance.name === arrivee?.name} />
            ))
          ))}
      </section>
      <OutilsDeLivraison org={org} />
      <Suspense>
        <Declarer org={org} />
      </Suspense>
    </div>
  );
}

const NO_SEARCH = () => "";
const subscribeToLocation = (notify: () => void) => {
  window.addEventListener("popstate", notify);
  return () => window.removeEventListener("popstate", notify);
};

/** The connector the catalogue just installed, and what its first discovery found (empty on the server). */
function useArrivee(): { name: string; discovery: string } | null {
  const search = useSyncExternalStore(subscribeToLocation, () => window.location.search, NO_SEARCH);
  const params = new URLSearchParams(search);
  const name = params.get("name");
  return name ? { name, discovery: params.get("discovery") ?? "" } : null;
}

function Instance({ org, instance, highlighted }: { org: string; instance: OrgConnector; highlighted?: boolean }) {
  const client = useQueryClient();
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
  const type = (types.data ?? []).find((t) => t.kind === instance.kind && t.type === instance.type);
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
          Discover
        </Button>
      )}
    </span>
  );
  return (
    <Card
      title={`${instance.name} — ${instance.type}`}
      titreDonnee
      action={action}
      className={highlighted ? "border-accent" : undefined}
      aria-current={highlighted ? "true" : undefined}
      data-testid={`connector-${instance.name}`}
    >
      <Availability org={org} instance={instance} />
      {diff && (
        <p className="mb-2 text-sm" role="status">
          {diff}
        </p>
      )}
      {erreur && <ErrorNote>{erreur}</ErrorNote>}
      <Secrets org={org} instance={instance} fields={type?.secret_fields ?? []} schema={type?.config_schema} />
      {(instance.operations ?? []).length === 0 ? (
        <p className="text-sm text-ink-muted">
          {instance.kind === "mcp"
            ? "No tool known yet: discover asks the server; each new tool is born closed."
            : "This type declares no operation an agent can call."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid={`operations-${instance.name}`}>
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="py-1 font-normal">Operation</th>
                <th className="py-1 font-normal">Access</th>
                <th className="py-1 font-normal">Policy</th>
                <th className="py-1 font-normal">Project groups</th>
                <th className="py-1 font-normal">Approver group</th>
                <th className="py-1 font-normal">Result cap</th>
              </tr>
            </thead>
            <tbody>
              {(instance.operations ?? []).map((operation) => (
                <Operation key={operation.name} org={org} connecteur={instance.name} operation={operation} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/**
 * The secrets of a declared connector, and its settings (ADR 0058). A secret is typed once: the
 * console only knows that it is set, when and by whom, and offers to replace or clear it. The
 * settings (its configuration) are edited in place.
 */
function Secrets({
  org,
  instance,
  fields,
  schema,
}: {
  org: string;
  instance: OrgConnector;
  fields: string[];
  schema?: unknown;
}) {
  const client = useQueryClient();
  const guard = useEgressGuard();
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<Record<string, unknown>>(instance.config ?? {});
  const [erreur, setErreur] = useState<string | null>(null);
  const names = [...new Set([...fields, ...(instance.secrets ?? []).map((s) => s.field)])];
  const refresh = () => client.invalidateQueries({ queryKey: ["org-connectors", org] });
  const jsonSchema = (schema ?? { type: "object", properties: {} }) as JsonSchema;
  if (names.length === 0 && Object.keys(jsonSchema.properties ?? {}).length === 0) return null;
  async function saveConfig() {
    setErreur(null);
    try {
      await guard((allow) => api.updateOrgConnector(org, instance.name, { config, ...egressFlag(allow) }), {
        gesture: `Change the settings of ${instance.name}`,
        origin: `org-connector:${instance.name}`,
        reason: `the ${instance.type} connector ${instance.name}`,
      });
      setEditing(false);
      await refresh();
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <div className="mb-3 space-y-2 border-b border-line pb-3" data-testid={`secrets-${instance.name}`}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">Secrets and settings</h3>
        <Button size="sm" onClick={() => setEditing(!editing)}>
          {editing ? "Close settings" : "Edit settings"}
        </Button>
        <ProcedurePanel
          target={connectorTarget(instance.kind, instance.type)}
          preset={instance.kind === "mcp" ? presetOfMcpUrl(config.url) : undefined}
          autoOpen={false}
        />
      </div>
      {editing && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void saveConfig();
          }}
        >
          <SchemaForm schema={jsonSchema} value={config} onChange={setConfig} />
          <Button size="sm" tone="primary" type="submit" disabled={champsManquants(jsonSchema, config).length > 0}>
            Save settings
          </Button>
          {erreur && <ErrorNote>{erreur}</ErrorNote>}
        </form>
      )}
      {names.map((field) => {
        const state = (instance.secrets ?? []).find((s) => s.field === field);
        return (
          <ExistingOrNew key={field} org={org} instance={instance} field={field} state={state} onDone={refresh} />
        );
      })}
    </div>
  );
}

/** A configured field: replace or clear. A field never set: a first value, typed once. */
function ExistingOrNew({
  org,
  instance,
  field,
  state,
  onDone,
}: {
  org: string;
  instance: OrgConnector;
  field: string;
  state: ConnectorSecretState | undefined;
  onDone: () => Promise<unknown>;
}) {
  const save = async (draft: SecretDraft) => {
    if (draft.mode === "value") await api.setOrgConnectorSecret(org, instance.name, field, draft.text);
    else await api.updateOrgConnector(org, instance.name, { secret_refs: { [field]: draft.text } });
    await onDone();
  };
  const clear = async () => {
    await api.clearOrgConnectorSecret(org, instance.name, field);
    await onDone();
  };
  return <SecretField field={field} state={state} onSave={save} onClear={clear} />;
}

function Operation({ org, connecteur, operation }: { org: string; connecteur: string; operation: ConnectorOperation }) {
  const client = useQueryClient();
  const [groupes, setGroupes] = useState((operation.groups ?? []).join(", "));
  const [approbateurs, setApprobateurs] = useState(operation.approver_group ?? "");
  const [erreur, setErreur] = useState<string | null>(null);
  const [assouplir, setAssouplir] = useState<OperationPatch["policy"] | null>(null);
  const [cap, setCap] = useState(operation.result_max_bytes?.toString() ?? "");
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
        {operation.description && (
          <p className="text-xs text-ink-muted" data-donnee>
            {operation.description}
          </p>
        )}
        {erreur && <ErrorNote>{erreur}</ErrorNote>}
      </td>
      <td>
        <Badge tone={operation.access === "write" ? "warn" : "neutral"}>{operation.access}</Badge>
      </td>
      <td>
        <label className="sr-only" htmlFor={`politique-${connecteur}-${operation.name}`}>
          Policy of {operation.name}
        </label>
        <Select
          id={`politique-${connecteur}-${operation.name}`}
          className="w-auto"
          value={assouplir ?? operation.policy}
          onChange={(event) => {
            const choisie = event.target.value as keyof typeof RIGUEUR;
            const actuelle = operation.policy as keyof typeof RIGUEUR;
            // Resserrer s'applique tout de suite ; assouplir ouvre une écriture à des agents : on le dit d'abord.
            if (RIGUEUR[choisie] < (RIGUEUR[actuelle] ?? 2)) setAssouplir(choisie);
            else {
              setAssouplir(null);
              void changer({ policy: choisie });
            }
          }}
        >
          {POLITIQUES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </Select>
        <span className="ml-2">
          <Badge tone={TON[operation.policy as keyof typeof TON] ?? "neutral"}>{operation.policy}</Badge>
        </span>
        {assouplir && (
          <div className="mt-2 max-w-sm">
            <Confirmation
              ton="primary"
              question={`Loosen ${operation.name} to ${assouplir}? ${CE_QUE_CA_PERMET[assouplir as keyof typeof CE_QUE_CA_PERMET] ?? ""}, in every project it applies to.`}
              confirmer={`Set ${assouplir}`}
              action={async () => {
                await api.updateConnectorOperation(org, connecteur, operation.name, { policy: assouplir });
                await client.invalidateQueries({ queryKey: ["org-connectors", org] });
              }}
              onFait={() => setAssouplir(null)}
              onAnnuler={() => setAssouplir(null)}
            />
          </div>
        )}
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
            Project groups of {operation.name}
          </label>
          <Input
            id={`groupes-${connecteur}-${operation.name}`}
            className="w-40"
            placeholder="Every project"
            value={groupes}
            onChange={(event) => setGroupes(event.target.value)}
          />
          <Button size="sm" type="submit" disabled={lus.join(",") === (operation.groups ?? []).join(",")}>
            Set
          </Button>
        </form>
      </td>
      <td>
        {operation.access === "write" ? (
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              // Empty clears it: the project's owners decide again.
              void changer({ approver_group: approbateurs.trim() || null });
            }}
          >
            <label className="sr-only" htmlFor={`approbateurs-${connecteur}-${operation.name}`}>
              Approver group of {operation.name}
            </label>
            <Input
              id={`approbateurs-${connecteur}-${operation.name}`}
              className="w-40"
              placeholder="Project owners"
              value={approbateurs}
              onChange={(event) => setApprobateurs(event.target.value)}
            />
            <Button size="sm" type="submit" disabled={approbateurs.trim() === (operation.approver_group ?? "")}>
              Set
            </Button>
          </form>
        ) : (
          <span className="text-xs text-ink-muted">A read: nobody approves it</span>
        )}
      </td>
      <td>
        <ResultCap
          id={`cap-${connecteur}-${operation.name}`}
          operation={operation.name}
          value={cap}
          stored={operation.result_max_bytes ?? null}
          onChange={setCap}
          onSubmit={(bytes) => void changer({ result_max_bytes: bytes })}
        />
      </td>
    </tr>
  );
}

/** The least a cap may be; the platform's ceiling is the API's to enforce, with its reason (S40-17). */
const MIN_RESULT_CAP = 1024;

/**
 * How much of one result of an operation the agent reads, in bytes (S40-17b). Empty returns it to the
 * platform's default; below 1024 is not sent.
 */
function ResultCap({
  id,
  operation,
  value,
  stored,
  onChange,
  onSubmit,
}: {
  id: string;
  operation: string;
  value: string;
  stored: number | null;
  onChange: (value: string) => void;
  onSubmit: (bytes: number | null) => void;
}) {
  const text = value.trim();
  const bytes = text === "" ? null : Number(text);
  const valid = bytes === null || (Number.isInteger(bytes) && bytes >= MIN_RESULT_CAP);
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSubmit(bytes);
      }}
    >
      <label className="sr-only" htmlFor={id}>
        Result cap of {operation}
      </label>
      <Input
        id={id}
        className="w-32"
        inputMode="numeric"
        placeholder="Platform default"
        aria-invalid={!valid}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <Button size="sm" type="submit" disabled={!valid || bytes === stored}>
        Set
      </Button>
      {!valid && <span className="text-xs text-ink-muted">at least {MIN_RESULT_CAP} bytes</span>}
    </form>
  );
}

/** Déclarer une instance : son type (ceux qui exposent des opérations d'abord), sa configuration, ses secrets. */
function Declarer({ org }: { org: string }) {
  const client = useQueryClient();
  const guard = useEgressGuard();
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
  const [nom, setNom] = useState("");
  // `?type=documents/sharepoint` preselects the type (the knowledge form links here).
  const [choix, setChoix] = useState(useSearchParams().get("type") ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState<Record<string, SecretDraft>>({});
  const [erreur, setErreur] = useState<string | null>(null);
  // Les outils de livraison d'un projet se règlent dans le projet : ils ne se déclarent pas ici.
  const candidats = (types.data ?? []).filter((t) => !estUneSorteDuProjet(t.kind));
  const familles = [
    { titre: "Business systems (API)", types: candidats.filter((t) => t.kind !== "mcp") },
    { titre: "MCP servers", types: candidats.filter((t) => t.kind === "mcp") },
  ].filter((famille) => famille.types.length > 0);
  const type = candidats.find((t) => `${t.kind}/${t.type}` === choix);
  const schema = (type?.config_schema ?? { type: "object", properties: {} }) as JsonSchema;
  const illisibles = Object.values(secrets).filter((draft) => problemOfDraft(draft) !== null);
  async function declarer() {
    if (!type) return;
    setErreur(null);
    try {
      await guard(
        (allow) =>
          api.createOrgConnector(org, {
            name: nom,
            type: type.type,
            kind: type.kind,
            config,
            ...bodyOfDrafts(secrets),
            ...egressFlag(allow),
          }),
        {
          gesture: `Declare the ${type.type} connector ${nom}`,
          origin: `org-connector:${nom}`,
          reason: `the ${type.type} connector ${nom}`,
        },
      );
      setNom("");
      setChoix("");
      setConfig({});
      setSecrets({});
      await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }
  return (
    <Card title="Declare a connector">
      {candidats.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No connector type exposes operations yet: a plugin (or the <code>mcp</code> type) brings them.
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
              <span className="text-xs text-ink-muted">Name</span>
              <Input
                className="w-full"
                value={nom}
                onChange={(e) => setNom(e.target.value)}
                placeholder="entra-acme"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">Type</span>
              <Select
                className="w-full"
                value={choix}
                onChange={(e) => {
                  setChoix(e.target.value);
                  setConfig({});
                  setSecrets({});
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
              </Select>
            </label>
          </div>
          {type && (
            <div className="flex items-center gap-2">
              <ProcedurePanel
                target={connectorTarget(type.kind, type.type)}
                preset={type.kind === "mcp" ? presetOfMcpUrl(config.url) : undefined}
              />
            </div>
          )}
          {type && <SchemaForm schema={schema} value={config} onChange={setConfig} />}
          {(type?.secret_fields ?? []).map((champ) => (
            <SecretField
              key={champ}
              field={champ}
              draft={secrets[champ] ?? EMPTY_DRAFT}
              onChange={(draft) => setSecrets({ ...secrets, [champ]: draft })}
            />
          ))}
          {erreur && <ErrorNote>{erreur}</ErrorNote>}
          <Button
            type="submit"
            tone="accent"
            disabled={!nom || !type || champsManquants(schema, config).length > 0 || illisibles.length > 0}
          >
            Declare
          </Button>
        </form>
      )}
    </Card>
  );
}

/**
 * Which projects reach the connector (ADR 0074). Restricting it turns it off for every project until
 * each is enabled, so the switch asks first; going back to every project deletes nothing.
 */
function Availability({ org, instance }: { org: string; instance: OrgConnector }) {
  const client = useQueryClient();
  const current = instance.availability ?? "all_projects";
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function set(next: "all_projects" | "selected_projects") {
    await api.updateOrgConnector(org, instance.name, { availability: next });
    await client.invalidateQueries({ queryKey: ["org-connectors", org] });
    await client.invalidateQueries({ queryKey: ["mcp-catalogue", org] });
  }
  return (
    <div className="mb-3 space-y-2 text-sm">
      <label className="flex items-center gap-2">
        <span className="text-xs text-ink-muted">Available to</span>
        <Select
          value={current}
          onChange={(event) => {
            setError(null);
            if (event.target.value === "selected_projects") setAsking(true);
            else void set("all_projects").catch((cause) => setError(cause instanceof Error ? cause.message : "refused"));
          }}
        >
          <option value="all_projects">Every project</option>
          <option value="selected_projects">Selected projects</option>
        </Select>
        {current === "selected_projects" && (
          <span className="text-xs text-ink-muted" data-testid={`on-in-${instance.name}`}>
            {(instance.enabled_projects ?? []).length === 0
              ? "on in no project yet"
              : `on in ${(instance.enabled_projects ?? []).join(", ")}`}
          </span>
        )}
      </label>
      {error && <ErrorNote>{error}</ErrorNote>}
      {asking && (
        <Confirmation
          question={
            <>
              Every project loses <strong data-donnee>{instance.name}</strong> until it is enabled for it.
            </>
          }
          confirmer="Restrict to selected projects"
          action={() => set("selected_projects")}
          onFait={() => setAsking(false)}
          onAnnuler={() => setAsking(false)}
        />
      )}
    </div>
  );
}
