// SPDX-License-Identifier: Apache-2.0
import type { WorkflowOperation } from "@/lib/types";

/**
 * The gestures of the map and of the process view, as typed operations (S16-11). The console never
 * rewrites the YAML: it asks the API for a graft, which returns the text, the diff and the inverse.
 */
// The keys below are the console's gesture names, shared with the fixture
// `tests/fixtures/gestes-de-la-console.json` that `packages/core/tests/test_edition.py` reads too:
// they are renamed together with that file (S45-29). // lang: fr-ok alias:external
export const operations = {
  libelle: (state: string, label: string): WorkflowOperation => ({ op: "set_state", name: state, field: "display", value: label }),
  genre: (state: string, kind: string): WorkflowOperation => ({ op: "set_state", name: state, field: "kind", value: kind }),
  renommer: (from: string, to: string): WorkflowOperation => ({ op: "rename_state", from, to }),
  retirerLEtat: (state: string): WorkflowOperation => ({ op: "remove_state", name: state }),
  ajouterUnEtat: (name: string, label: string, kind?: string): WorkflowOperation => ({
    op: "add_state",
    name: name,
    spec: kind ? { display: label, kind: kind } : { display: label },
  }),
  ajouterUneTransition: (from: string, to: string, by: string, id?: string): WorkflowOperation => ({
    op: "add_transition",
    transition: { ...(id ? { id } : {}), from: from, to: to, by: by },
  }),
  retirerLaTransition: (id: string): WorkflowOperation => ({ op: "remove_transition", id }),
  acteur: (transition: string, actor: string): WorkflowOperation => ({ op: "set_transition", id: transition, field: "by", value: actor }),
  delai: (transition: string, hours: number | null): WorkflowOperation =>
    hours === null
      ? { op: "set_transition", id: transition, field: "timeout_hours", unset: true }
      : { op: "set_transition", id: transition, field: "timeout_hours", value: hours },
  ajouterUneGarantie: (transition: string, gate: string): WorkflowOperation => ({ op: "add_gate", transition, gate: gate }),
  retirerUneGarantie: (transition: string, gate: string): WorkflowOperation => ({ op: "remove_gate", transition, name: gate }),
  // ADR 0112: actors, every transition field, the workflow's own fields.
  addActor: (name: string, spec: Record<string, unknown>): WorkflowOperation => ({ op: "add_actor", name, spec }),
  removeActor: (name: string): WorkflowOperation => ({ op: "remove_actor", name }),
  /** An actor's field (a decision's questions, its state, its timeout, ADR 0121); a nested value is written as lines. */
  actorField: (name: string, field: string, value: unknown): WorkflowOperation =>
    value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)
      ? { op: "set_actor", name, field, unset: true }
      : { op: "set_actor", name, field, value, style: nested(value) ? "block" : "flow" },
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

/** Suggestions, not a closed list: the API validates, and reports an unknown gate. */
export const COMMON_GATES = [
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
