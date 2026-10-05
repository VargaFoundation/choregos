// SPDX-License-Identifier: Apache-2.0
import type { WorkflowOperation } from "@/lib/types";

/**
 * Les gestes de la carte et de la vue processus, en opérations typées (S16-11). La console ne
 * réécrit jamais le YAML : elle demande une greffe à l'API, qui rend le texte, le diff et l'inverse.
 */
export const operations = {
  libelle: (etat: string, libelle: string): WorkflowOperation => ({ op: "set_state", name: etat, field: "display", value: libelle }),
  genre: (etat: string, genre: string): WorkflowOperation => ({ op: "set_state", name: etat, field: "kind", value: genre }),
  renommer: (de: string, vers: string): WorkflowOperation => ({ op: "rename_state", from: de, to: vers }),
  retirerLEtat: (etat: string): WorkflowOperation => ({ op: "remove_state", name: etat }),
  ajouterUnEtat: (nom: string, libelle: string, genre?: string): WorkflowOperation => ({
    op: "add_state",
    name: nom,
    spec: genre ? { display: libelle, kind: genre } : { display: libelle },
  }),
  ajouterUneTransition: (de: string, vers: string, par: string, id?: string): WorkflowOperation => ({
    op: "add_transition",
    transition: { ...(id ? { id } : {}), from: de, to: vers, by: par },
  }),
  retirerLaTransition: (id: string): WorkflowOperation => ({ op: "remove_transition", id }),
  acteur: (transition: string, acteur: string): WorkflowOperation => ({ op: "set_transition", id: transition, field: "by", value: acteur }),
  delai: (transition: string, heures: number | null): WorkflowOperation =>
    heures === null
      ? { op: "set_transition", id: transition, field: "timeout_hours", unset: true }
      : { op: "set_transition", id: transition, field: "timeout_hours", value: heures },
  ajouterUneGarantie: (transition: string, garantie: string): WorkflowOperation => ({ op: "add_gate", transition, gate: garantie }),
  retirerUneGarantie: (transition: string, garantie: string): WorkflowOperation => ({ op: "remove_gate", transition, name: garantie }),
};

/** Des suggestions, pas une liste fermée : l'API valide, et dit une garantie inconnue. */
export const GARANTIES_COURANTES = [
  "ci_green",
  "scope_respected",
  "review_approved",
  "evidence_present",
  "evidence_facts",
  "outputs_present",
  "no_secrets",
  "diff_size_max",
  "tool_called",
];
