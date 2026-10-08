// SPDX-License-Identifier: Apache-2.0
"use client";

import dynamic from "next/dynamic";
import { useRef } from "react";
import { Card, Empty, ErrorNote } from "@/components/ui";
import type { PoigneeDeLEditeur } from "@/components/yaml-editor";
import { useBrouillon } from "@/components/workflows/brouillon";

// L'éditeur (CodeMirror) n'est chargé qu'à l'ouverture de l'onglet : la page n'en paie pas le poids avant.
const YamlEditor = dynamic(() => import("@/components/yaml-editor").then((m) => m.YamlEditor), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-muted">loading the editor…</p>,
});

/**
 * Le YAML du workflow — une VUE du brouillon commun (S21-06) : ce qu'on tape ici est ce que la carte
 * et la vue processus montrent, et la barre du brouillon le publie, d'un seul bouton pour les trois
 * vues. La validation suit la frappe ; c'est celle du serveur, le code de l'orchestrateur.
 */
export default function YamlPage() {
  const brouillon = useBrouillon();
  const editeur = useRef<PoigneeDeLEditeur>(null);
  const { definition, validation } = brouillon;
  if (definition.error) return <ErrorNote>{String(definition.error)}</ErrorNote>;
  if (!definition.data) return <Empty>reading the workflow…</Empty>;
  const name = definition.data.name;
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Card title={`${name} — YAML`}>
        <YamlEditor
          ref={editeur}
          label={`YAML of ${name}`}
          value={brouillon.yaml}
          onChange={brouillon.ecrire}
          readOnly={brouillon.occupe}
          issues={brouillon.erreurs}
          warnings={brouillon.avertissements}
        />
        <p className="mt-2 text-xs text-ink-muted">
          Changes typed here, on the map or in the process view are one draft: the bar above publishes it.
        </p>
      </Card>
      <Card title="validation" className="self-start">
        <div data-testid="validation-yaml" className="space-y-2 text-sm">
          {validation.etat === "en_cours" ? (
            <p className="text-ink-muted">validating…</p>
          ) : validation.etat === "injoignable" ? (
            <ErrorNote>
              could not validate: {validation.message}.{" "}
              <button type="button" className="underline" onClick={brouillon.revalider}>
                retry
              </button>
            </ErrorNote>
          ) : brouillon.erreurs.length === 0 ? (
            <p className="text-ok">✓ valid workflow</p>
          ) : null}
          {brouillon.erreurs.map((issue, index) => (
            <ErrorNote key={index}>
              {issue.line ? (
                <button type="button" className="underline" onClick={() => editeur.current?.allerALaLigne(issue.line!)}>
                  line {issue.line}
                </button>
              ) : null}
              {issue.line ? " — " : ""}
              {issue.message}
              {issue.path ? ` (${issue.path})` : ""}
            </ErrorNote>
          ))}
          {brouillon.avertissements.map((issue, index) => (
            <p key={index} className="rounded border border-line border-l-2 border-l-warn bg-surface px-3 py-2 text-warn">
              {issue.message}
            </p>
          ))}
        </div>
      </Card>
    </div>
  );
}
