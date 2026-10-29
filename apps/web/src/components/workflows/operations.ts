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
  // ADR 0112: actors, every transition field, the workflow's own fields.
  addActor: (name: string, spec: Record<string, unknown>): WorkflowOperation => ({ op: "add_actor", name, spec }),
  removeActor: (name: string): WorkflowOperation => ({ op: "remove_actor", name }),
  /** A transition field; a nested value (a task, an action, a fan-out) is written as indented lines. */
  transitionField: (id: string, field: string, value: unknown): WorkflowOperation =>
    value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)
      ? { op: "set_transition", id, field, unset: true }
      : { op: "set_transition", id, field, value, style: nested(value) ? "block" : "flow" },
  workflowField: (field: "description" | "inputs" | "catalogue" | "initial", value: unknown): WorkflowOperation =>
    value === null || value === undefined || value === ""
      ? { op: "set_workflow", field, unset: true }
      : { op: "set_workflow", field, value, style: nested(value) ? "block" : "flow" },
};

/** An object, or a list holding one: written as indented lines; a list of names stays on one line. */
function nested(value: unknown): boolean {
  if (Array.isArray(value)) return value.some((v) => v !== null && typeof v === "object");
  return value !== null && typeof value === "object";
}

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
  "docs_updated",
  "tool_called",
];
