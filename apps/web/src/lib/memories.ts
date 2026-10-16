// SPDX-License-Identifier: Apache-2.0
/**
 * What the console says about memories and an agent's bindings to them (S40-11, ADR 0075).
 *
 * `effectiveBindings` is the TypeScript twin of `choregos_core.memory_bindings.effective_bindings`:
 * a version without `memories` means what its legacy `memory` field says. A test pins the two on the
 * three legacy values.
 */
import type { AgentMemoryBinding, AgentSpec, MemoryStore } from "./types";

export type Binding = AgentMemoryBinding & { mode: "read" | "write" };

/** The two implicit memories of every project: never rows, never global. */
export const IMPLICIT = {
  shared: { label: "The project's shared memory", hint: "What the project knows, read and written by the runs that use it." },
  own: { label: "Its own memory in the project", hint: "What this agent learnt in the project: no other agent reads it." },
} as const;

export const MAX_BINDINGS = 8;

export const LEGACY_DEFAULT: Binding[] = [{ scope: "project", slug: "shared", mode: "write" }];

/** What a run of this version binds: `memories`, or what the legacy `memory` field means. */
export function effectiveBindings(spec: Pick<AgentSpec, "memories" | "memory">): Binding[] {
  if (spec.memories != null) return spec.memories.map((b) => ({ ...b, mode: b.mode ?? "read" }));
  if (spec.memory === "agent") return [{ scope: "project", slug: "own", mode: "write" }];
  if (spec.memory === "none") return [];
  return [{ scope: "project", slug: "shared", mode: "write" }];
}

export const labelOf = (b: Pick<Binding, "scope" | "slug">) => `${b.scope}:${b.slug}`;

/** A binding in words: the memory's name, with where it lives. */
export function describe(b: Pick<Binding, "scope" | "slug">, known: MemoryStore[] = []): string {
  if (b.scope === "project" && b.slug in IMPLICIT) return IMPLICIT[b.slug as keyof typeof IMPLICIT].label;
  const found = known.find((m) => m.scope === b.scope && m.slug === b.slug);
  const name = found?.display_name ?? b.slug;
  return b.scope === "global" ? `${name} (organisation)` : `${name} (project)`;
}

/** The same list with exactly one binding writing: `index` (or none, with `null`). Choosing write on a row demotes the other. */
export function withWriter(bindings: Binding[], index: number | null): Binding[] {
  return bindings.map((b, i) => ({ ...b, mode: i === index ? "write" : "read" }));
}

export function moveBinding(bindings: Binding[], index: number, by: -1 | 1): Binding[] {
  const target = index + by;
  if (target < 0 || target >= bindings.length) return bindings;
  const out = [...bindings];
  [out[index], out[target]] = [out[target]!, out[index]!];
  return out;
}

export function writer(bindings: Binding[]): Binding | undefined {
  return bindings.find((b) => b.mode === "write");
}

/** Where a run's lessons go, in words (for a global memory they are only proposals). */
export function writesTo(bindings: Binding[], known: MemoryStore[] = []): string {
  const w = writer(bindings);
  if (!w) return "writes nothing";
  if (w.scope === "global") return `proposes to ${describe(w, known).replace(" (organisation)", "")}, which an administrator accepts`;
  if (w.slug === "shared") return "writes to the project's shared memory";
  if (w.slug === "own") return "writes to its own memory in the project";
  return `writes to the project's ${w.slug}`;
}

/** The one-line summary of a folded section: `2 organisation, 1 project · writes to the project's shared memory`. */
export function summaryOf(bindings: Binding[], known: MemoryStore[] = []): string {
  if (bindings.length === 0) return "none · reads and writes nothing";
  const global = bindings.filter((b) => b.scope === "global").length;
  const project = bindings.length - global;
  const counts = [global ? `${global} organisation` : null, project ? `${project} project` : null].filter(Boolean).join(", ");
  return `${counts} · ${writesTo(bindings, known)}`;
}

export function isLegacyDefault(bindings: Binding[]): boolean {
  return bindings.length === 1 && bindings[0]!.scope === "project" && bindings[0]!.slug === "shared" && bindings[0]!.mode === "write";
}

/** A global memory's reach, in words. */
export function reachLabel(m: Pick<MemoryStore, "reach" | "projects">): string {
  if (m.reach === "projects") return `projects ${(m.projects ?? []).join(", ") || "none yet"}`;
  return "every project";
}

/** The projects that name a project memory (`decisions` exists in billing-api, checkout-web), from the organisation's list. */
export function projectsHaving(slug: string, all: MemoryStore[]): string[] {
  return all.filter((m) => m.scope === "project" && m.slug === slug && m.status !== "archived").map((m) => m.project ?? "").filter(Boolean);
}
