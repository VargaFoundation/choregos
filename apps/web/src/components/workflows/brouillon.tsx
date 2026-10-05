// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { ApiError, api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowOperation } from "@/lib/types";

type Etat = {
  yaml: string;
  resultat: WorkflowEditResult;
  /** Les inverses des gestes, du plus ancien au plus récent : annuler joue le dernier. */
  annulations: WorkflowOperation[][];
};

export type Brouillon = ReturnType<typeof useBrouillonInterne>;

/**
 * Le brouillon d'un workflow : les gestes faits sur la carte ou dans la vue processus, pas encore
 * publiés. Chaque geste passe par `POST /workflows/edit` (S16-11) ; son inverse s'empile, et
 * « annuler » le rejoue. Publier envoie le texte, avec la version lue : si une autre a été publiée
 * entre-temps, l'API refuse (409) au lieu d'écraser.
 */
function useBrouillonInterne(slug: string, name: string) {
  const { definition, validation } = useWorkflow(slug, name);
  const client = useQueryClient();
  const [etat, setEtat] = useState<Etat | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  const base = definition.data?.yaml ?? "";
  const yaml = etat?.yaml ?? base;

  async function appliquer(...ops: WorkflowOperation[]): Promise<boolean> {
    // Avant la lecture, il n'y a pas de texte où greffer : un geste partirait sur un document vide.
    if (!definition.data) return false;
    setErreur(null);
    setOccupe(true);
    try {
      const resultat = await api.editWorkflow(yaml, ops);
      setEtat({ yaml: resultat.yaml, resultat, annulations: [...(etat?.annulations ?? []), resultat.inverse] });
      return true;
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "edit refused");
      return false;
    } finally {
      setOccupe(false);
    }
  }

  async function annuler() {
    if (!etat) return;
    const inverse = etat.annulations.at(-1);
    if (!inverse) return;
    setOccupe(true);
    try {
      const resultat = await api.editWorkflow(etat.yaml, inverse);
      const restantes = etat.annulations.slice(0, -1);
      setEtat(restantes.length === 0 ? null : { yaml: resultat.yaml, resultat, annulations: restantes });
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "undo refused");
    } finally {
      setOccupe(false);
    }
  }

  async function publier(): Promise<string | null> {
    if (!etat || !definition.data) return null;
    setOccupe(true);
    try {
      const publiee = await api.putWorkflowNamed(slug, name, etat.yaml, definition.data.version);
      setEtat(null);
      await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
      await client.invalidateQueries({ queryKey: ["workflow-versions", slug, name] });
      return `${publiee.name} v${publiee.version} published`;
    } catch (cause) {
      setErreur(
        cause instanceof ApiError && cause.status === 409
          ? "someone published a newer version meanwhile: discard and start again from it"
          : cause instanceof Error
            ? cause.message
            : "publication refused",
      );
      return null;
    } finally {
      setOccupe(false);
    }
  }

  const resultat = etat?.resultat;
  return {
    definition,
    yaml,
    enAttente: etat?.annulations.length ?? 0,
    graph: resultat?.graph ?? validation.data?.graph,
    process: resultat?.process ?? validation.data?.process,
    valide: resultat ? resultat.valid : (validation.data?.valid ?? true),
    erreurs: resultat?.errors ?? [],
    dernierDiff: resultat?.diff ?? "",
    avis: resultat?.notices ?? [],
    erreur,
    occupe,
    appliquer,
    annuler,
    abandonner: () => setEtat(null),
    publier,
  };
}

const Contexte = createContext<Brouillon | null>(null);

export function BrouillonProvider({ slug, name, children }: { slug: string; name: string; children: ReactNode }) {
  const brouillon = useBrouillonInterne(slug, name);
  return <Contexte.Provider value={brouillon}>{children}</Contexte.Provider>;
}

export function useBrouillon(): Brouillon {
  const brouillon = useContext(Contexte);
  if (!brouillon) throw new Error("useBrouillon hors de BrouillonProvider");
  return brouillon;
}
