// SPDX-License-Identifier: Apache-2.0
import type { Workflow, WorkflowActor, WorkflowDecisionActor, WorkflowDecisionQuestion } from "@choregos/contracts/schemas";

/** The workflow as the engine read it (`document` of a validation, ADR 0112). */
export type WorkflowDocument = Workflow;
export type { WorkflowActor, WorkflowDecisionActor, WorkflowDecisionQuestion };

/** The model a decision step asks when it names none (ADR 0121, the contract's default). */
export const DEFAULT_DECISION_MODEL = "platform/decide";
/** How long a decision step waits for its answer when it says nothing (the contract's default). */
export const DEFAULT_DECISION_TIMEOUT_SECONDS = 15;
/** The suffix of the output that carries a question's probability. */
export const CONFIDENCE_SUFFIX = "_confidence";

/** The decision actor a step names, when it is one: its questions are read from the workflow itself. */
export function decisionActorOf(document: WorkflowDocument | undefined, name: string | undefined): WorkflowDecisionActor | undefined {
  const actor = name ? document?.actors[name] : undefined;
  return actor && actor.type === "decision" ? actor : undefined;
}

/** What a decision step writes, in order: each answer, then its probability — what a `when` compares. */
export function decisionOutputs(actor: Pick<WorkflowDecisionActor, "questions">): string[] {
  return Object.keys(actor.questions ?? {}).flatMap((name) => [name, `${name}${CONFIDENCE_SUFFIX}`]);
}

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

/** `agent`, `human`, `system`, `job`, `decision`, with what a reader needs to tell them apart. */
export function describeActor(actor: WorkflowActor): string {
  const a = actor as Record<string, unknown>;
  switch (a.type) {
    case "decision": {
      const count = Object.keys((a.questions as Record<string, unknown> | undefined) ?? {}).length;
      return `decision \`${String(a.role)}\` by ${String(a.model ?? DEFAULT_DECISION_MODEL)}, ${count} question${count === 1 ? "" : "s"}`;
    }
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
