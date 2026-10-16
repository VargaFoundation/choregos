// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConnectorCard } from "@/components/project-settings/connector-card";
import { estUneReference } from "@/components/secret-field";
import { Button, ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { ConnectorDto, ProjectRequirement } from "@/lib/types";

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

/** Cards to show first: what a workflow needs and nothing provides (S40-03b). */
export function manquantEnPremier(lignes: string[], parSorte: Map<string, ProjectRequirement>, configures: ConnectorDto[]): string[] {
  const manque = (sorte: string) => !configures.some((c) => c.kind === sorte) && !!parSorte.get(sorte) && !parSorte.get(sorte)?.default_type;
  return [...lignes.filter(manque), ...lignes.filter((sorte) => !manque(sorte))];
}

export function Connecteurs({ slug }: { slug: string }) {
  const client = useQueryClient();
  const exigences = useQuery({ queryKey: ["requirements", slug], queryFn: () => api.projectRequirements(slug) });
  const connecteurs = useQuery({ queryKey: ["connectors", slug], queryFn: () => api.connectors(slug) });
  const types = useQuery({ queryKey: ["connector-types"], queryFn: () => api.connectorTypes() });
  const executeurs = useQuery({ queryKey: ["executors"], queryFn: () => api.executors() });
  const [ajout, setAjout] = useState("");
  const [ajoutees, setAjoutees] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  if (exigences.error) return <ErrorNote>{String(exigences.error)}</ErrorNote>;
  const actuelDe = (sorte: string) => (connecteurs.data ?? []).find((c) => c.kind === sorte);
  const parSorte = new Map((exigences.data ?? []).map((e) => [e.capability, e]));
  // A connector added by hand and then saved is a configured one: one row, not two.
  const lignes = manquantEnPremier(
    [...new Set([...lignesDeConnecteurs(exigences.data ?? [], connecteurs.data ?? []), ...ajoutees])],
    parSorte,
    connecteurs.data ?? [],
  );
  // The runtimes this deployment runs (`GET /platform/executors`): a project cannot pick another one.
  // An empty answer means the deployment did not say, not that nothing runs.
  const permis = new Set<string>((executeurs.data ?? []).filter((e) => e.enabled).map((e) => e.kind));
  const typesDe = (sorte: string) =>
    (types.data ?? []).filter(
      (t) => t.kind === sorte && (sorte !== "runtime" || permis.size === 0 || permis.has(t.type) || actuelDe(sorte)?.type === t.type),
    );
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
    <div>
      <div className="space-y-3">
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
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="connecteurs">
          {lignes.map((sorte) => (
            <ConnectorCard
              key={sorte}
              sorte={sorte}
              exigence={parSorte.get(sorte)}
              actuel={(connecteurs.data ?? []).find((c) => c.kind === sorte)}
              types={typesDe(sorte)}
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
    </div>
  );
}
