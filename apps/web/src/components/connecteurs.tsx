// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import {
  EMPTY_DRAFT,
  SecretField,
  bodyOfDrafts,
  estUneReference,
  problemOfDraft,
  type SecretDraft,
} from "@/components/secret-field";
import { Badge, Button, Card, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { connectorState } from "@/lib/connector-state";
import { libelleDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorDto, ConnectorType, ProjectRequirement } from "@/lib/types";

/** L'ordre dans lequel la console montre les capacités (celui de `choregos_core.dsl.exigences`). */
const ORDRE = ["tracker", "scm", "ci", "cd", "runtime", "gateway", "memory", "notify"];

/**
 * Les lignes à montrer : ce que les workflows exigent, ce qui est déjà configuré — rien d'autre.
 * Un projet RH ne voit ni `scm`, ni `ci`, ni `cd` (ADR 0034) ; « add a connector » ouvre le reste.
 */
export function lignesDeConnecteurs(exigences: ProjectRequirement[], configures: ConnectorDto[]): string[] {
  const sortes = new Set([...exigences.map((e) => e.capability), ...configures.map((c) => c.kind)]);
  return [...sortes].sort((a, b) => {
    const ra = ORDRE.indexOf(a) === -1 ? ORDRE.length : ORDRE.indexOf(a);
    const rb = ORDRE.indexOf(b) === -1 ? ORDRE.length : ORDRE.indexOf(b);
    return ra - rb || a.localeCompare(b);
  });
}

export { estUneReference };

export function Connecteurs({ slug }: { slug: string }) {
  const client = useQueryClient();
  const exigences = useQuery({ queryKey: ["requirements", slug], queryFn: () => api.projectRequirements(slug) });
  const connecteurs = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
  const [ajout, setAjout] = useState("");
  const [ajoutees, setAjoutees] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  if (exigences.error) return <ErrorNote>{String(exigences.error)}</ErrorNote>;
  const actuelDe = (sorte: string) => (connecteurs.data ?? []).find((c) => c.kind === sorte);
  const parSorte = new Map((exigences.data ?? []).map((e) => [e.capability, e]));
  // A connector added by hand and then saved is a configured one: one row, not two.
  const lignes = [...new Set([...lignesDeConnecteurs(exigences.data ?? [], connecteurs.data ?? []), ...ajoutees])];
  const autres = [...new Set((types.data ?? []).map((t) => t.kind))].filter((k) => !lignes.includes(k)).sort();

  async function agir(action: () => Promise<unknown>, succes: string | ((result: unknown) => string)) {
    setErreur(null);
    setMessage(null);
    try {
      const result = await action();
      setMessage(typeof succes === "function" ? succes(result) : succes);
      await client.invalidateQueries({ queryKey: ["connectors", slug] });
      await client.invalidateQueries({ queryKey: ["requirements", slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card title="Connectors" className="lg:col-span-2">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          What this project&apos;s workflows need, and why. A secret is typed once and stored encrypted: the console
          then only shows that it is set and when it last changed. An operator may name it by reference
          (<code>env:NAME</code>) instead. Systems the whole organisation
          shares — a directory, MCP servers — are declared in{" "}
          <Link href="/admin/connectors" className="underline">
            Admin › connectors
          </Link>
          .
        </p>
        {(message || erreur) && (
          <div>
            {message && (
              <p className="text-sm text-ok" role="status">
                {message}
              </p>
            )}
            {erreur && <ErrorNote>{erreur}</ErrorNote>}
          </div>
        )}
        <ul className="space-y-2" data-testid="connecteurs">
          {lignes.map((sorte) => (
            <Ligne
              key={sorte}
              sorte={sorte}
              exigence={parSorte.get(sorte)}
              actuel={(connecteurs.data ?? []).find((c) => c.kind === sorte)}
              types={(types.data ?? []).filter((t) => t.kind === sorte)}
              onSave={(corps) =>
                agir(
                  () => api.putConnector(slug, sorte, corps),
                  (saved) => {
                    const { status, last_error } = saved as ConnectorDto;
                    if (status === "ok") return `${sorte} connector saved: connected`;
                    return `${sorte} connector saved: ${status === "error" ? `failing, ${last_error ?? "the test failed"}` : "not tested yet"}`;
                  },
                )
              }
              onSecret={(field, draft) =>
                agir(async () => {
                  if (draft.mode === "value") await api.setConnectorSecret(slug, sorte, field, draft.text);
                  else {
                    const refs = { ...(actuelDe(sorte)?.secret_refs ?? {}), [field]: draft.text };
                    await api.putConnector(slug, sorte, {
                      type: actuelDe(sorte)?.type,
                      config: actuelDe(sorte)?.config ?? {},
                      secret_refs: refs,
                    });
                  }
                }, `${sorte}: ${field.replaceAll("_", " ")} saved`)
              }
              onClearSecret={(field) =>
                agir(() => api.clearConnectorSecret(slug, sorte, field), `${sorte}: ${field.replaceAll("_", " ")} cleared`)
              }
              onTest={() =>
                agir(async () => {
                  const resultat = await api.testConnector(slug, sorte);
                  if (!resultat.ok) {
                    throw new Error(
                      `${sorte}: ` + resultat.checks.map((c) => `${c.name} ${c.ok ? "✓" : `✗ ${c.detail ?? ""}`}`).join(", "),
                    );
                  }
                }, `${sorte}: connection OK`)
              }
            />
          ))}
        </ul>
        {autres.length > 0 && (
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (ajout) setAjoutees([...ajoutees, ajout]);
              setAjout("");
            }}
          >
            <label className="space-y-1">
              <span className="text-xs text-ink-muted">Add a connector</span>
              <Select
                className="w-auto"
                value={ajout}
                onChange={(event) => setAjout(event.target.value)}
              >
                <option value="">—</option>
                {autres.map((sorte) => (
                  <option key={sorte} value={sorte}>
                    {sorte}
                  </option>
                ))}
              </Select>
            </label>
            <Button size="sm" type="submit" disabled={!ajout}>
              Add
            </Button>
          </form>
        )}
      </div>
    </Card>
  );
}

function Ligne({
  sorte,
  exigence,
  actuel,
  types,
  onSave,
  onSecret,
  onClearSecret,
  onTest,
}: {
  sorte: string;
  exigence?: ProjectRequirement;
  actuel?: ConnectorDto;
  types: ConnectorType[];
  onSave: (corps: ConnectorBody) => Promise<void>;
  onSecret: (field: string, draft: SecretDraft) => Promise<void>;
  onClearSecret: (field: string) => Promise<void>;
  onTest: () => Promise<void>;
}) {
  const [ouvert, setOuvert] = useState(false);
  const etat = connectorState({ connector: actuel, requirement: exigence, required: exigence !== undefined });
  return (
    <li className="rounded border border-line p-3" data-testid={`connecteur-${sorte}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="w-44">
          <span className="font-medium">{sorte}</span>
          {libelleDeSorte(sorte) !== sorte && <span className="ml-2 text-xs text-ink-muted">{libelleDeSorte(sorte)}</span>}
        </span>
        <span className="font-mono text-xs">{actuel ? actuel.type : null}</span>
        {etat && (
          <>
            <Badge tone={etat.tone} data-testid={`etat-${sorte}`}>
              {etat.label}
            </Badge>
            {etat.standIn && (
              <Badge tone="warn" dot={false}>
                Demo stand-in
              </Badge>
            )}
            {etat.detail && etat.action !== "connect" && <span className="text-xs text-ink-muted">{etat.detail}</span>}
          </>
        )}
        <span className="ml-auto inline-flex gap-2">
          {actuel && (
            <Button size="sm" onClick={() => void onTest()}>
              Test
            </Button>
          )}
          <Button size="sm" onClick={() => setOuvert(!ouvert)}>
            {ouvert ? "Close" : actuel ? "Edit" : "Configure"}
          </Button>
        </span>
      </div>
      {exigence && exigence.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-6 text-xs text-ink-muted">
          {exigence.reasons.slice(0, 3).map((raison) => (
            <li key={raison}>{raison}</li>
          ))}
          {exigence.reasons.length > 3 && <li>And {exigence.reasons.length - 3} more</li>}
        </ul>
      )}
      {ouvert && (
        <Formulaire
          types={types}
          actuel={actuel}
          onSecret={onSecret}
          onClearSecret={onClearSecret}
          onSave={async (corps) => {
            await onSave(corps);
            setOuvert(false);
          }}
        />
      )}
    </li>
  );
}

type ConnectorBody = {
  type: string;
  config: Record<string, unknown>;
  secret_refs: Record<string, string>;
  secret_values: Record<string, string>;
};

/**
 * The type, its configuration drawn from its schema, and its secrets (ADR 0058). A secret that is
 * already set is replaced or cleared on the spot (its own call, the value never comes back); one
 * that is not is typed here and goes with Save. The references already held are sent again, since a
 * save replaces them; a stored value the body does not name is kept by the API.
 */
function Formulaire({
  types,
  actuel,
  onSave,
  onSecret,
  onClearSecret,
}: {
  types: ConnectorType[];
  actuel?: ConnectorDto;
  onSave: (corps: ConnectorBody) => Promise<void>;
  onSecret: (field: string, draft: SecretDraft) => Promise<void>;
  onClearSecret: (field: string) => Promise<void>;
}) {
  const [type, setType] = useState(actuel?.type ?? types[0]?.type ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(actuel?.config ?? {});
  const [secrets, setSecrets] = useState<Record<string, SecretDraft>>({});
  const choisi = types.find((t) => t.type === type);
  const schema = (choisi?.config_schema ?? { type: "object", properties: {} }) as JsonSchema;
  const manquants = champsManquants(schema, config);
  const illisibles = Object.values(secrets).filter((draft) => problemOfDraft(draft) !== null);
  const memeType = actuel?.type === type;
  const etats = memeType ? (actuel?.secrets ?? []) : [];
  return (
    <form
      className="mt-3 space-y-3 border-t border-line pt-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        const kept = Object.fromEntries(
          etats.filter((s) => s.source === "reference" && s.reference).map((s) => [s.field, s.reference as string]),
        );
        const { secret_values, secret_refs } = bodyOfDrafts(secrets);
        void onSave({ type, config, secret_refs: { ...kept, ...secret_refs }, secret_values });
      }}
    >
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">Implementation</span>
        <Select
          className="w-full"
          value={type}
          onChange={(event) => {
            setType(event.target.value);
            setConfig({});
            setSecrets({});
          }}
        >
          {types.map((t) => (
            <option key={t.type} value={t.type}>
              {t.display}
            </option>
          ))}
        </Select>
      </label>
      <SchemaForm schema={schema} value={config} onChange={setConfig} />
      {(choisi?.secret_fields ?? []).length > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-xs text-ink-muted">Secrets — typed once, never shown again</legend>
          {(choisi?.secret_fields ?? []).map((champ) => {
            const etat = etats.find((s) => s.field === champ);
            return etat ? (
              <SecretField
                key={champ}
                field={champ}
                state={etat}
                onSave={(draft) => onSecret(champ, draft)}
                onClear={() => onClearSecret(champ)}
              />
            ) : (
              <SecretField
                key={champ}
                field={champ}
                draft={secrets[champ] ?? EMPTY_DRAFT}
                onChange={(draft) => setSecrets({ ...secrets, [champ]: draft })}
              />
            );
          })}
        </fieldset>
      )}
      <div className="flex justify-end">
        <Button size="sm" tone="primary" type="submit" disabled={!type || manquants.length > 0 || illisibles.length > 0}>
          Save
        </Button>
      </div>
    </form>
  );
}
