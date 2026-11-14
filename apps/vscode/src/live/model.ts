// SPDX-License-Identifier: Apache-2.0
/**
 * What is in flight, as the editor holds it (ADR 0155 §6): the entries of `GET /orgs/{org}/activity`, kept
 * up to date by its stream, and the tree the Live view draws from them — project → work item → entry.
 *
 * The platform names what an entry is; the model only groups and orders. Sub-agents are not derived here
 * (ADR 0154 §5): until the platform serves them (G5), an entry has no children.
 */
import type { ActivityEntry, ActivityList } from "@choregos/contracts/api";
import { stateOf, type State } from "./states";

/** How long a finished entry stays in the tree, as the platform lists them (`FINISHED_WINDOW`). */
export const FINISHED_WINDOW_MS = 60 * 60 * 1000;

export interface EntryNode {
  kind: "entry";
  key: string;
  entry: ActivityEntry;
}

export interface GroupNode {
  kind: "project" | "work-item";
  key: string;
  label: string;
  /** The workflow of a work item, when its entries say it. */
  detail: string | undefined;
  state: State | undefined;
  children: LiveNode[];
}

export type LiveNode = EntryNode | GroupNode;

/** The label of a project group whose entries name no project: a person's own space (ADR 0134). */
export const PERSONAL = "Personal space";

const PRECEDENCE: State[] = ["running", "queued", "waiting", "failed", "stopped", "done"];

function inFlight(entry: ActivityEntry): boolean {
  const state = stateOf(entry);
  return !entry.ended_at && (state === "running" || state === "queued");
}

function aggregate(entries: ActivityEntry[]): State | undefined {
  const states = new Set(entries.map((e) => stateOf(e)));
  return PRECEDENCE.find((state) => states.has(state));
}

export function entryLabel(entry: ActivityEntry): string {
  const what =
    entry.kind === "run"
      ? (entry.stage_role ?? "run")
      : entry.kind === "session"
        ? "session"
        : "conversation turn";
  const who = entry.agent_slug ?? entry.assistant ?? entry.model;
  return who ? `${what} · ${who}` : what;
}

export class LiveModel {
  private entries = new Map<string, ActivityEntry>();

  /** Replaces everything with a fresh list: what the stream missed while it was away is not guessed. */
  load(list: ActivityList): void {
    this.entries = new Map();
    for (const entry of [...list.finished, ...list.in_flight]) this.entries.set(key(entry), entry);
  }

  /** An `activity.upsert` or `activity.ended` message; anything else changes nothing. */
  apply(event: string, data: string): boolean {
    if (event !== "activity.upsert" && event !== "activity.ended") return false;
    let entry: ActivityEntry;
    try {
      entry = JSON.parse(data) as ActivityEntry;
    } catch {
      return false;
    }
    if (typeof entry.id !== "string" || typeof entry.kind !== "string") return false;
    this.entries.set(key(entry), entry);
    return true;
  }

  get(entryKey: string): ActivityEntry | undefined {
    return this.entries.get(entryKey);
  }

  running(): number {
    return [...this.entries.values()].filter(inFlight).length;
  }

  /** The tree at `now`: projects with something in flight first, then by name; newest entries first. */
  tree(now: Date): GroupNode[] {
    const horizon = now.getTime() - FINISHED_WINDOW_MS;
    const visible = [...this.entries.values()].filter(
      (e) => inFlight(e) || !e.ended_at || Date.parse(e.ended_at) >= horizon,
    );
    const projects = new Map<string, ActivityEntry[]>();
    for (const entry of visible) {
      const project = entry.project_slug ?? PERSONAL;
      projects.set(project, [...(projects.get(project) ?? []), entry]);
    }
    const groups = [...projects.entries()].map(([project, entries]) => projectNode(project, entries));
    return groups.sort(
      (a, b) =>
        Number(b.state === "running" || b.state === "queued") -
          Number(a.state === "running" || a.state === "queued") || a.label.localeCompare(b.label),
    );
  }
}

function key(entry: ActivityEntry): string {
  return `${entry.kind}:${entry.id}`;
}

function newestFirst(a: ActivityEntry, b: ActivityEntry): number {
  return Date.parse(b.started_at) - Date.parse(a.started_at);
}

function projectNode(project: string, entries: ActivityEntry[]): GroupNode {
  const items = new Map<string, ActivityEntry[]>();
  const loose: ActivityEntry[] = [];
  for (const entry of entries) {
    if (entry.work_item_id) items.set(entry.work_item_id, [...(items.get(entry.work_item_id) ?? []), entry]);
    else loose.push(entry);
  }
  const children: LiveNode[] = [...items.entries()]
    .map(([id, runs]) => workItemNode(project, id, runs.sort(newestFirst)))
    .sort((a, b) => newestFirst(first(a), first(b)));
  for (const entry of loose.sort(newestFirst)) children.push({ kind: "entry", key: key(entry), entry });
  return {
    kind: "project",
    key: `project:${project}`,
    label: project,
    detail: undefined,
    state: aggregate(entries),
    children,
  };
}

function workItemNode(project: string, id: string, runs: ActivityEntry[]): GroupNode {
  const named = runs.find((r) => r.work_item_key);
  const workflow = runs.find((r) => r.workflow)?.workflow ?? undefined;
  return {
    kind: "work-item",
    key: `work-item:${project}:${id}`,
    label: named?.work_item_key ?? id,
    detail: workflow,
    state: aggregate(runs),
    children: runs.map((entry) => ({ kind: "entry", key: key(entry), entry })),
  };
}

function first(group: GroupNode): ActivityEntry {
  const node = group.children[0];
  if (node?.kind !== "entry") throw new Error("a work item group holds entries");
  return node.entry;
}
