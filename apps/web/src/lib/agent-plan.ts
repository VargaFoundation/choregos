// SPDX-License-Identifier: Apache-2.0
/**
 * The agent's plan, as it declares it in ACP (S25-01).
 *
 * An ACP agent publishes its plan through `session/update` (`sessionUpdate: "plan"`): the WHOLE list
 * of its tasks, every time, which the client replaces ("The Client MUST replace the current plan
 * completely"). The run's journal keeps every revision; this module reads them back, under three
 * rules:
 *
 * - a task is identified by its content: reworded, it is another task (the old one is removed, the
 *   new one added); nothing merges silently;
 * - a task that disappears from the plan is REMOVED, never counted as done, even if the agent had
 *   ticked it before: only the current plan says what is done;
 * - only what the agent wrote in a plan is part of it: no task is guessed from its messages.
 *
 * It is the agent's word, not a finding of the platform.
 */
import type { RunEventDto } from "./types";

export type TaskStatus = "pending" | "in_progress" | "completed";
export type TaskPriority = "high" | "medium" | "low";
export type StatutDeTache = TaskStatus; // lang: fr-ok alias:internal
export type PrioriteDeTache = TaskPriority; // lang: fr-ok alias:internal

export type Task = { contenu: string; priorite: TaskPriority; statut: TaskStatus };
export type Tache = Task; // lang: fr-ok alias:internal

export type PlanRevision = { seq: number; ts: string; taches: Task[] };
export type RevisionDuPlan = PlanRevision; // lang: fr-ok alias:internal

export type PlanTask =
  | (Task & { retiree: false })
  | {
      contenu: string;
      priorite: TaskPriority;
      retiree: true;
      /** The last status the agent gave it. */
      avant: TaskStatus;
      /** The revision (counted from 1) it disappeared from. */
      revision: number;
    };
export type TacheDuPlan = PlanTask; // lang: fr-ok alias:internal

export type AgentPlan = {
  revisions: PlanRevision[];
  /** The current plan, in the agent's order, then the removed tasks, in the order of their removal. */
  taches: PlanTask[];
  /** The done tasks of the current plan, never a removed task. */
  faites: number;
  /** The tasks of the current plan. */
  total: number;
};
export type PlanDeLAgent = AgentPlan; // lang: fr-ok alias:internal

const STATUSES = new Set<TaskStatus>(["pending", "in_progress", "completed"]);
const PRIORITIES = new Set<TaskPriority>(["high", "medium", "low"]);

/** A task's identity: its content, whitespace normalised. Case counts: the agent writes it. */
function taskKey(content: string): string {
  return content.trim().replace(/\s+/g, " ");
}

/**
 * The entries of a plan revision, wherever the agent publishes them: an ACP `plan` update, or the
 * `todowrite` tool of opencode, which carries the same fields (`content`, `status`, `priority`) in
 * the input of its tool call instead of an ACP `plan` update.
 */
function planEntries(update: Record<string, unknown>): unknown[] | null {
  if (update.sessionUpdate === "plan") return Array.isArray(update.entries) ? update.entries : null;
  if (update.sessionUpdate !== "tool_call" && update.sessionUpdate !== "tool_call_update") return null;
  const name = `${update.title ?? ""} ${update.name ?? ""}`;
  const input = update.rawInput as { todos?: unknown } | undefined;
  return /todo/i.test(name) && Array.isArray(input?.todos) ? input.todos : null;
}

/** The tasks of a revision, or nothing if the event is not a plan. A duplicate keeps its first place. */
function tasksOf(event: RunEventDto): Task[] | null {
  if (event.type !== "session/update") return null;
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const update = (payload.update ?? payload) as Record<string, unknown>;
  const entries = planEntries(update);
  if (!entries) return null;
  const seen = new Set<string>();
  const tasks: Task[] = [];
  for (const raw of entries as Record<string, unknown>[]) {
    const content = typeof raw?.content === "string" ? taskKey(raw.content) : "";
    // A cancelled todo leaves the plan: it shows as removed, never as done.
    if (!content || seen.has(content) || raw.status === "cancelled") continue;
    seen.add(content);
    const status = raw.status as TaskStatus;
    const priority = raw.priority as TaskPriority;
    tasks.push({
      contenu: content,
      priorite: PRIORITIES.has(priority) ? priority : "medium",
      statut: STATUSES.has(status) ? status : "pending",
    });
  }
  return tasks;
}

/** A run's agent plan, read back from its journal; `null` if it published none. */
export function agentPlan(events: RunEventDto[]): AgentPlan | null {
  // The stored journal and the live stream overlap: one event per number, in the agent's order.
  const bySeq = new Map<number, RunEventDto>();
  for (const event of events) bySeq.set(event.seq, event);
  const revisions: PlanRevision[] = [];
  for (const event of [...bySeq.values()].sort((a, b) => a.seq - b.seq)) {
    const found = tasksOf(event);
    if (found) revisions.push({ seq: event.seq, ts: event.ts, taches: found });
  }
  const current = revisions.at(-1);
  if (!current) return null;

  // Every task seen, with its last state and the last revision that carried it.
  const seen = new Map<string, { task: Task; last: number }>();
  revisions.forEach((revision, index) => {
    for (const task of revision.taches) seen.set(task.contenu, { task, last: index });
  });
  const present = new Set(current.taches.map((t) => t.contenu));
  const removed: PlanTask[] = [...seen.values()]
    .filter(({ task }) => !present.has(task.contenu))
    .sort((a, b) => a.last - b.last)
    .map(({ task, last }) => ({
      contenu: task.contenu,
      priorite: task.priorite,
      retiree: true,
      avant: task.statut,
      revision: last + 2,
    }));
  return {
    revisions,
    taches: [...current.taches.map((t): PlanTask => ({ ...t, retiree: false })), ...removed],
    faites: current.taches.filter((t) => t.statut === "completed").length,
    total: current.taches.length,
  };
}
export const planDeLAgent = agentPlan; // lang: fr-ok alias:internal
