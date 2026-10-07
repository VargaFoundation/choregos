// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import { Button, Card, ErrorNote, StateBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
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

/** Une référence de secret, pas une valeur : `schéma:reste` (`env:JIRA_TOKEN`). */
export function estUneReference(valeur: string): boolean {
  return /^[a-z][a-z0-9-]{1,31}:.+$/.test(valeur);
}

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
  const parSorte = new Map((exigences.data ?? []).map((e) => [e.capability, e]));
  const lignes = [...lignesDeConnecteurs(exigences.data ?? [], connecteurs.data ?? []), ...ajoutees];
  const autres = [...new Set((types.data ?? []).map((t) => t.kind))].filter((k) => !lignes.includes(k)).sort();

  async function agir(action: () => Promise<unknown>, succes: string) {
    setErreur(null);
    setMessage(null);
    try {
      await action();
      setMessage(succes);
      await client.invalidateQueries({ queryKey: ["connectors", slug] });
      await client.invalidateQueries({ queryKey: ["requirements", slug] });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "refused");
    }
  }

  return (
    <Card title="connectors" className="lg:col-span-2">
      <div className="space-y-3">
        <p className="text-sm text-ink-muted">
          What this project&apos;s workflows need, and why. A secret is never typed here: name it by reference
          (<code>env:NAME</code>), the platform reads it when the connector is used. Systems the whole organisation
          shares — a directory, MCP servers — are declared in{" "}
          <Link href="/admin/connectors" className="underline">
            admin › connectors
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
              onSave={(corps) => agir(() => api.putConnector(slug, sorte, corps), `${sorte} connector saved`)}
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
              <span className="text-xs text-ink-muted">add a connector</span>
              <select
                className="rounded border border-line bg-surface px-2 py-1.5 text-sm"
                value={ajout}
                onChange={(event) => setAjout(event.target.value)}
              >
                <option value="">—</option>
                {autres.map((sorte) => (
                  <option key={sorte} value={sorte}>
                    {sorte}
                  </option>
                ))}
              </select>
            </label>
            <Button size="sm" type="submit" disabled={!ajout}>
              add
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
  onTest,
}: {
  sorte: string;
  exigence?: ProjectRequirement;
  actuel?: ConnectorDto;
  types: ConnectorType[];
  onSave: (corps: { type: string; config: Record<string, unknown>; secret_refs: Record<string, string> }) => Promise<void>;
  onTest: () => Promise<void>;
}) {
  const [ouvert, setOuvert] = useState(false);
  return (
    <li className="rounded border border-line p-3" data-testid={`connecteur-${sorte}`}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="w-44">
          <span className="font-medium">{sorte}</span>
          {libelleDeSorte(sorte) !== sorte && <span className="ml-2 text-xs text-ink-muted">{libelleDeSorte(sorte)}</span>}
        </span>
        <span className="font-mono text-xs">
          {actuel ? (
            actuel.type
          ) : exigence?.default_type ? (
            <span className="text-ink-muted">platform default: {exigence.default_type}</span>
          ) : (
            <span className="text-ink-muted">— not configured</span>
          )}
        </span>
        {actuel && (
          <StateBadge
            state={actuel.status}
            display={actuel.status}
            kind={actuel.status === "ok" ? "terminal" : actuel.status === "error" ? "blocked" : "wait"}
          />
        )}
        {actuel?.last_check_at && <span className="text-xs text-ink-muted">tested {shortDate(actuel.last_check_at)}</span>}
        <span className="ml-auto inline-flex gap-2">
          {actuel && (
            <Button size="sm" onClick={() => void onTest()}>
              test
            </Button>
          )}
          <Button size="sm" onClick={() => setOuvert(!ouvert)}>
            {ouvert ? "close" : actuel ? "edit" : "configure"}
          </Button>
        </span>
      </div>
      {exigence && exigence.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-6 text-xs text-ink-muted">
          {exigence.reasons.slice(0, 3).map((raison) => (
            <li key={raison}>{raison}</li>
          ))}
          {exigence.reasons.length > 3 && <li>and {exigence.reasons.length - 3} more</li>}
        </ul>
      )}
      {ouvert && (
        <Formulaire
          types={types}
          actuel={actuel}
          onSave={async (corps) => {
            await onSave(corps);
            setOuvert(false);
          }}
        />
      )}
    </li>
  );
}

/** Le type, sa configuration tirée de son schéma, et ses secrets en références. */
function Formulaire({
  types,
  actuel,
  onSave,
}: {
  types: ConnectorType[];
  actuel?: ConnectorDto;
  onSave: (corps: { type: string; config: Record<string, unknown>; secret_refs: Record<string, string> }) => Promise<void>;
}) {
  const [type, setType] = useState(actuel?.type ?? types[0]?.type ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(actuel?.config ?? {});
  const [references, setReferences] = useState<Record<string, string>>(actuel?.secret_refs ?? {});
  const choisi = types.find((t) => t.type === type);
  const schema = (choisi?.config_schema ?? { type: "object", properties: {} }) as JsonSchema;
  const manquants = champsManquants(schema, config);
  const illisibles = Object.entries(references).filter(([, v]) => v && !estUneReference(v));
  return (
    <form
      className="mt-3 space-y-3 border-t border-line pt-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        const refs = Object.fromEntries(Object.entries(references).filter(([, v]) => v));
        void onSave({ type, config, secret_refs: refs });
      }}
    >
      <label className="block space-y-1">
        <span className="text-xs text-ink-muted">implementation</span>
        <select
          className="w-full rounded border border-line bg-surface px-2 py-1.5"
          value={type}
          onChange={(event) => {
            setType(event.target.value);
            setConfig({});
            setReferences({});
          }}
        >
          {types.map((t) => (
            <option key={t.type} value={t.type}>
              {t.display}
            </option>
          ))}
        </select>
      </label>
      <SchemaForm schema={schema} value={config} onChange={setConfig} />
      {(choisi?.secret_fields ?? []).length > 0 && (
        <fieldset className="space-y-2">
          <legend className="text-xs text-ink-muted">secrets — by reference, never the value</legend>
          {(choisi?.secret_fields ?? []).map((champ) => (
            <label key={champ} className="block space-y-1">
              <span className="text-xs text-ink-muted">{champ.replaceAll("_", " ")}</span>
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
              {illisibles.map(([champ]) => champ).join(", ")}: a reference is expected (env:NAME), not a value
            </p>
          )}
        </fieldset>
      )}
      <div className="flex justify-end">
        <Button size="sm" tone="primary" type="submit" disabled={!type || manquants.length > 0 || illisibles.length > 0}>
          save
        </Button>
      </div>
    </form>
  );
}
