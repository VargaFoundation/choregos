// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useWorkflow } from "@/components/workflows/use-workflow";
import { ApiError, api } from "@/lib/api";
import type { WorkflowEditResult, WorkflowOperation, WorkflowValidation } from "@/lib/types";

/** Un geste à défaire : une opération typée (son inverse, rejoué par l'API) ou une session de frappe (le texte d'avant). */
type Geste = { inverse: WorkflowOperation[] } | { texteAvant: string };

type Etat = {
  yaml: string;
  /** Ce que l'API dit du texte : le résultat d'une édition typée, ou la validation de la frappe. */
  resultat: WorkflowValidation | WorkflowEditResult | null;
  diff: string;
  /** Du plus ancien au plus récent : annuler défait le dernier. */
  gestes: Geste[];
  /** La version sur laquelle le brouillon est parti : c'est elle que la publication annonce. */
  versionDeBase: number;
  /** Vrai tant que la frappe continue la même session : une session se défait d'un coup. */
  frappeEnCours: boolean;
};

export type EtatDeValidation = { etat: "a_jour" } | { etat: "en_cours" } | { etat: "injoignable"; message: string };

/** Le délai entre la dernière frappe et la validation : assez pour ne pas valider chaque lettre. */
export const DELAI_DE_VALIDATION_MS = 400;

export type Brouillon = ReturnType<typeof useBrouillonInterne>;

/**
 * Le brouillon d'un workflow, SEUL état des trois vues (S21-06) : la frappe dans l'onglet YAML, les
 * gestes de la carte et de la vue processus écrivent dans le même texte, et une seule publication
 * l'envoie. Avant, l'onglet YAML tenait son propre texte : deux boutons « publish », et le brouillon
 * de la carte, publié après lui, l'écrasait sans 409 — il relisait la version au moment de publier.
 *
 * Un geste typé passe par `POST /workflows/edit` (S16-11) et empile son inverse ; une session de
 * frappe empile le texte d'avant. La validation suit la frappe (débouncée, annulée si une autre la
 * dépasse) ; publier exige un texte validé, valide, et annonce la version de DÉPART : si une autre a
 * été publiée entre-temps, l'API refuse (409) et le texte reste.
 */
function useBrouillonInterne(slug: string, name: string) {
  const { definition, validation } = useWorkflow(slug, name);
  const client = useQueryClient();
  const [etat, setEtat] = useState<Etat | null>(null);
  const [validationDuTexte, setValidationDuTexte] = useState<EtatDeValidation>({ etat: "a_jour" });
  const [erreur, setErreur] = useState<string | null>(null);
  const [occupe, setOccupe] = useState(false);
  const [conflit409, setConflit409] = useState(false);
  const base = definition.data?.yaml ?? "";
  const yaml = etat?.yaml ?? base;

  // Les gestes lisent le texte COURANT, même lancés depuis une fermeture plus ancienne.
  const texteCourant = useRef(yaml);
  useEffect(() => {
    texteCourant.current = yaml;
  }, [yaml]);
  // Chaque validation porte un numéro : seule la dernière demandée a le droit d'écrire son verdict.
  const sequence = useRef(0);
  const minuterie = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enVol = useRef<AbortController | null>(null);

  const arreterLaValidation = useCallback(() => {
    sequence.current += 1;
    if (minuterie.current) clearTimeout(minuterie.current);
    minuterie.current = null;
    enVol.current?.abort();
    enVol.current = null;
  }, []);

  const valider = useCallback(
    (texte: string) => {
      arreterLaValidation();
      const numero = sequence.current;
      setValidationDuTexte({ etat: "en_cours" });
      minuterie.current = setTimeout(() => {
        const controle = new AbortController();
        enVol.current = controle;
        api
          .validateWorkflow(texte, controle.signal)
          .then((resultat) => {
            if (numero !== sequence.current) return;
            setEtat((courant) => (courant && courant.yaml === texte ? { ...courant, resultat } : courant));
            setValidationDuTexte({ etat: "a_jour" });
          })
          .catch((cause: unknown) => {
            if (numero !== sequence.current || controle.signal.aborted) return;
            const message = cause instanceof Error ? cause.message : "the server did not answer";
            setValidationDuTexte({ etat: "injoignable", message });
          });
      }, DELAI_DE_VALIDATION_MS);
    },
    [arreterLaValidation],
  );

  useEffect(() => arreterLaValidation, [arreterLaValidation]);

  // Quitter la page avec un brouillon non publié : le navigateur demande confirmation.
  useEffect(() => {
    if (!etat) return;
    const retenir = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", retenir);
    return () => window.removeEventListener("beforeunload", retenir);
  }, [etat]);

  /** La frappe de l'onglet YAML. */
  function ecrire(texte: string) {
    if (!definition.data) return;
    const versionLue = definition.data.version;
    setErreur(null);
    setEtat((courant) => {
      if (!courant) {
        if (texte === base) return null;
        return {
          yaml: texte,
          resultat: null,
          diff: "",
          gestes: [{ texteAvant: base }],
          versionDeBase: versionLue,
          frappeEnCours: true,
        };
      }
      const gestes = courant.frappeEnCours ? courant.gestes : [...courant.gestes, { texteAvant: courant.yaml }];
      return { ...courant, yaml: texte, gestes, frappeEnCours: true, diff: "" };
    });
    valider(texte);
  }

  async function appliquer(...ops: WorkflowOperation[]): Promise<boolean> {
    // Avant la lecture, il n'y a pas de texte où greffer : un geste partirait sur un document vide.
    if (!definition.data) return false;
    const versionLue = definition.data.version;
    setErreur(null);
    setOccupe(true);
    try {
      const resultat = await api.editWorkflow(texteCourant.current, ops);
      // Le verdict de l'édition vaut validation : une validation de frappe encore en vol est périmée.
      arreterLaValidation();
      setValidationDuTexte({ etat: "a_jour" });
      setEtat((courant) => ({
        yaml: resultat.yaml,
        resultat,
        diff: resultat.diff,
        gestes: [...(courant?.gestes ?? []), { inverse: resultat.inverse }],
        versionDeBase: courant?.versionDeBase ?? versionLue,
        frappeEnCours: false,
      }));
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
    const dernier = etat.gestes.at(-1);
    if (!dernier) return;
    const restants = etat.gestes.slice(0, -1);
    if ("texteAvant" in dernier) {
      if (restants.length === 0) {
        arreterLaValidation();
        setValidationDuTexte({ etat: "a_jour" });
        setEtat(null);
        return;
      }
      setEtat({ ...etat, yaml: dernier.texteAvant, gestes: restants, frappeEnCours: false, diff: "" });
      valider(dernier.texteAvant);
      return;
    }
    setOccupe(true);
    try {
      const resultat = await api.editWorkflow(etat.yaml, dernier.inverse);
      arreterLaValidation();
      setValidationDuTexte({ etat: "a_jour" });
      setEtat(
        restants.length === 0
          ? null
          : { ...etat, yaml: resultat.yaml, resultat, diff: resultat.diff, gestes: restants, frappeEnCours: false },
      );
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "undo refused");
    } finally {
      setOccupe(false);
    }
  }

  async function envoyer(versionAnnoncee: number): Promise<string | null> {
    if (!etat) return null;
    setOccupe(true);
    setErreur(null);
    try {
      const publiee = await api.putWorkflowNamed(slug, name, etat.yaml, versionAnnoncee);
      arreterLaValidation();
      setEtat(null);
      setConflit409(false);
      await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      await client.invalidateQueries({ queryKey: ["workflows", slug] });
      await client.invalidateQueries({ queryKey: ["workflow-versions", slug, name] });
      return `${publiee.name} v${publiee.version} published — running items finish on their version`;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setConflit409(true);
        await client.invalidateQueries({ queryKey: ["workflow", slug, name] });
      } else {
        setErreur(cause instanceof Error ? cause.message : "publication refused");
      }
      return null;
    } finally {
      setOccupe(false);
    }
  }

  /** Publier : la version de DÉPART du brouillon, jamais celle relue depuis. */
  function publier() {
    return etat ? envoyer(etat.versionDeBase) : Promise.resolve(null);
  }

  /** Publier par-dessus la version la plus récente : un choix explicite, l'autre version reste dans l'historique. */
  async function ecraser() {
    const relue = await definition.refetch();
    const derniere = relue.data?.version;
    return derniere === undefined ? null : envoyer(derniere);
  }

  /** Abandonner le brouillon et repartir de la version publiée. */
  async function recharger() {
    arreterLaValidation();
    setValidationDuTexte({ etat: "a_jour" });
    setEtat(null);
    setConflit409(false);
    setErreur(null);
    await definition.refetch();
  }

  // Le dernier graphe d'un texte VALIDE : quand la frappe rend le YAML illisible, la carte garde le
  // précédent (grisé) plutôt que de disparaître.
  const resultat = etat?.resultat ?? null;
  const publie = validation.data;
  const courant = etat ? resultat : (publie ?? null);
  const [dernierValide, setDernierValide] = useState<WorkflowValidation | null>(null);
  if (courant?.valid && courant.graph && courant !== dernierValide) setDernierValide(courant);
  const lisible = Boolean(courant?.graph && courant.process);
  const affiche = lisible ? courant : dernierValide;

  const versionPubliee = definition.data?.version;
  const conflit =
    etat && versionPubliee !== undefined && (conflit409 || versionPubliee !== etat.versionDeBase)
      ? { versionLue: etat.versionDeBase, versionPubliee }
      : null;
  const valide = etat ? Boolean(resultat?.valid) : (publie?.valid ?? true);

  return {
    definition,
    yaml,
    enAttente: etat?.gestes.length ?? 0,
    graph: affiche?.graph ?? undefined,
    process: affiche?.process ?? undefined,
    /** Vrai quand la carte montre le dernier texte valide, pas le texte courant. */
    grapheObsolete: Boolean(etat) && !lisible && Boolean(dernierValide),
    valide,
    erreurs: courant?.errors ?? [],
    avertissements: courant?.warnings ?? [],
    dernierDiff: etat?.diff ?? "",
    avis: resultat && "notices" in resultat ? ((resultat as WorkflowEditResult).notices ?? []) : [],
    validation: validationDuTexte,
    publiable: Boolean(etat) && validationDuTexte.etat === "a_jour" && valide && !occupe && Boolean(resultat),
    conflit,
    erreur,
    occupe,
    ecrire,
    appliquer,
    annuler,
    abandonner: () => void recharger(),
    publier,
    ecraser,
    recharger,
    revalider: () => valider(yaml),
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
