// SPDX-License-Identifier: Apache-2.0
// lang: fr-ok-file alias:internal — former path of ./draft, removed by S45-29
"use client";

import { DraftProvider, useDraft } from "@/components/workflows/draft";

export { DraftProvider as BrouillonProvider };

/** The draft under its former French shape: what the pages in `src/app` read until they are renamed. */
export function useBrouillon() {
  const d = useDraft();
  const validation =
    d.validation.state === "unreachable"
      ? { etat: "injoignable" as const, message: d.validation.message }
      : { etat: d.validation.state === "running" ? ("en_cours" as const) : ("a_jour" as const) };
  return {
    definition: d.definition,
    yaml: d.yaml,
    enAttente: d.pending,
    repris: d.resumed,
    graph: d.graph,
    process: d.process,
    document: d.document,
    grapheObsolete: d.graphStale,
    valide: d.valid,
    erreurs: d.errors,
    avertissements: d.warnings,
    dernierDiff: d.lastDiff,
    avis: d.notices,
    validation,
    publiable: d.publishable,
    conflit: d.conflict ? { versionLue: d.conflict.readVersion, versionPubliee: d.conflict.publishedVersion } : null,
    erreur: d.error,
    occupe: d.busy,
    ecrire: d.write,
    appliquer: d.apply,
    annuler: d.undo,
    abandonner: d.abandon,
    publier: d.publish,
    ecraser: d.overwrite,
    recharger: d.reload,
    revalider: d.revalidate,
  };
}
