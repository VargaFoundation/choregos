// SPDX-License-Identifier: Apache-2.0
import type { Workflow, WorkflowActor } from "@choregos/contracts/schemas";

/** The workflow as the engine read it (`document` of a validation, ADR 0112). */
export type WorkflowDocument = Workflow;
export type { WorkflowActor };

/** What the map's forms read of a transition; the contract types it as a union the generator cannot name. */
export type ReadTransition = {
  id?: string;
  from: string;
  to: string;
  by?: string;
  via?: string;
  train?: { env?: string; approval?: string } | null;
  gates?: Array<string | { name: string; params?: Record<string, unknown> }>;
  inputs?: string[];
  outputs?: string[];
  on_fail?: { to?: string; max_attempts?: number; escalate_to?: string } | null;
  on_reject?: string | null;
  does?: string | null;
  timeout_hours?: number | null;
  task?: { title?: string; instructions?: string; form?: Record<string, unknown>; attest?: string } | null;
  action?: { kind?: string; title?: string; justification?: string; params?: Record<string, unknown>; effects?: unknown[] } | null;
  spawn?: Record<string, unknown> | null;
};

export function transitions(document: WorkflowDocument | undefined): ReadTransition[] {
  return ((document?.transitions ?? []) as ReadTransition[]).filter((t) => t && typeof t === "object");
}

export function transition(document: WorkflowDocument | undefined, id: string | undefined): ReadTransition | undefined {
  return id ? transitions(document).find((t) => t.id === id) : undefined;
}

/** `agent`, `human`, `system`, `job`, with what a reader needs to tell them apart. */
export function describeActor(actor: WorkflowActor): string {
  const a = actor as Record<string, unknown>;
  switch (a.type) {
    case "agent":
      return `agent ${a.agent ? `\`${String(a.agent)}\`` : "(playbook)"}, role ${String(a.role)}`;
    case "human":
      return `people of @${String(a.group)}${a.sla_hours ? `, within ${String(a.sla_hours)} h` : ""}`;
    case "job":
      return `job ${String(a.role)}${a.image ? ` on ${String(a.image)}` : ""}`;
    default:
      return "the platform";
  }
}
