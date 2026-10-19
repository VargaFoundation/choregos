// SPDX-License-Identifier: Apache-2.0
import { describe as describeBinding, effectiveBindings, labelOf, type Binding } from "@/lib/memories";
import type { AgentSpec } from "@/lib/types";

/**
 * The difference between two specs, field by field (S40-12, ADR 0076). Pure: the console has both specs
 * already (`Agent.versions`), so no endpoint computes it. A line diff of the JSON would show a skill that
 * moved as two changes; this one names skills by slug, connectors by name, knowledge bases by slug and
 * eval cases by name, shows instructions as a line diff, and shows any field it does not know as JSON.
 */

export type LineChange = { kind: "same" | "added" | "removed"; text: string };
export type ChangeGroup = "instructions" | "model" | "capabilities" | "limits" | "evals" | "other";
export type SpecChange = {
  group: ChangeGroup;
  field: string;
  kind: "added" | "removed" | "changed";
  label: string;
  before?: string;
  after?: string;
  /** For a list entry that changed: what moved inside it (`+search_*  −get_*`). */
  detail?: string;
  /** For instructions: the changed lines, with their neighbours. */
  lines?: LineChange[];
  /** Instructions too long to compare line by line: both texts are given whole. */
  tooLong?: boolean;
};

/** Over this many lines on a side, instructions are not compared line by line. */
export const MAX_DIFF_LINES = 2000;

export const GROUP_LABELS: Record<ChangeGroup, string> = {
  instructions: "Instructions",
  model: "Model and runtime",
  capabilities: "Capabilities",
  limits: "Limits and budget",
  evals: "Eval suite",
  other: "Other fields",
};
export const GROUP_ORDER: ChangeGroup[] = ["instructions", "model", "capabilities", "limits", "evals", "other"];

const KNOWN = new Set(["instructions", "model", "backend", "limits", "budget", "skills", "mcp_servers", "knowledge", "memory", "memories", "evals"]);

/** A spec with absent, null and default values made the same, so none of them shows as a change. */
export function normalizeSpec(spec: AgentSpec | null | undefined): Record<string, unknown> {
  const s = (spec ?? {}) as Record<string, unknown> & AgentSpec;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(s)) if (!KNOWN.has(key) && value !== null && value !== undefined) out[key] = value;
  out.instructions = s.instructions ?? "";
  out.model = s.model || null;
  out.backend = s.backend || null;
  out.limits = { max_turns: s.limits?.max_turns ?? null, max_minutes: s.limits?.max_minutes ?? null };
  out.budget = {
    run_usd: s.budget?.run_usd ?? null,
    daily_usd: s.budget?.daily_usd ?? null,
    monthly_usd: s.budget?.monthly_usd ?? null,
  };
  out.skills = s.skills ?? [];
  out.mcp_servers = (s.mcp_servers ?? []).filter((m) => m.connector);
  out.knowledge = s.knowledge ?? [];
  // `memories`, or what the legacy `memory` means: a version published either way reads the same (S40-11).
  out.memories = effectiveBindings(s);
  out.evals = (s.evals ?? []).filter((c) => c.name?.trim());
  return out;
}

/** A line diff by longest common subsequence, after trimming the lines both sides share. */
export function lineDiff(before: string, after: string): LineChange[] {
  const a = before === "" ? [] : before.split("\n");
  const b = after === "" ? [] : after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const x = a.slice(start, endA);
  const y = b.slice(start, endB);
  const table = Array.from({ length: x.length + 1 }, () => new Int32Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      table[i]![j] = x[i] === y[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
  const middle: LineChange[] = [];
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) {
      middle.push({ kind: "same", text: x[i]! });
      i++;
      j++;
    } else if (table[i + 1]![j]! >= table[i]![j + 1]!) middle.push({ kind: "removed", text: x[i++]! });
    else middle.push({ kind: "added", text: y[j++]! });
  }
  while (i < x.length) middle.push({ kind: "removed", text: x[i++]! });
  while (j < y.length) middle.push({ kind: "added", text: y[j++]! });
  return [
    ...a.slice(0, start).map((text): LineChange => ({ kind: "same", text })),
    ...middle,
    ...a.slice(endA).map((text): LineChange => ({ kind: "same", text })),
  ];
}

const show = (value: unknown, none: string): string =>
  value === null || value === undefined || value === "" ? none : typeof value === "object" ? JSON.stringify(value) : String(value);

function scalar(
  out: SpecChange[],
  group: ChangeGroup,
  field: string,
  label: string,
  before: unknown,
  after: unknown,
  none: string,
  format: (v: unknown) => string = (v) => String(v),
) {
  if (before === after) return;
  const fmt = (v: unknown) => (v === null || v === undefined ? none : format(v));
  out.push({
    group,
    field,
    label,
    kind: before == null ? "added" : after == null ? "removed" : "changed",
    before: fmt(before),
    after: fmt(after),
  });
}

function keyed<T>(
  out: SpecChange[],
  group: ChangeGroup,
  field: string,
  before: T[],
  after: T[],
  key: (item: T) => string,
  label: (item: T) => string,
  describe: (item: T) => string,
  changed: (a: T, b: T) => { detail?: string } | null,
) {
  const was = new Map(before.map((item) => [key(item), item]));
  const now = new Map(after.map((item) => [key(item), item]));
  for (const [k, item] of was)
    if (!now.has(k)) out.push({ group, field, kind: "removed", label: label(item), before: describe(item) });
  for (const [k, item] of now) {
    const old = was.get(k);
    if (!old) out.push({ group, field, kind: "added", label: label(item), after: describe(item) });
    else {
      const change = changed(old, item);
      if (change) out.push({ group, field, kind: "changed", label: label(item), before: describe(old), after: describe(item), ...change });
    }
  }
}

/** What changed from `before` to `after`, grouped as the read view groups a version. */
export function diffSpecs(before: AgentSpec | null | undefined, after: AgentSpec | null | undefined): SpecChange[] {
  const a = normalizeSpec(before) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const b = normalizeSpec(after) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const out: SpecChange[] = [];

  if (a.instructions !== b.instructions) {
    const tooLong = String(a.instructions).split("\n").length > MAX_DIFF_LINES || String(b.instructions).split("\n").length > MAX_DIFF_LINES;
    out.push({
      group: "instructions",
      field: "instructions",
      label: "Instructions",
      kind: a.instructions === "" ? "added" : b.instructions === "" ? "removed" : "changed",
      ...(tooLong
        ? { tooLong: true, before: String(a.instructions), after: String(b.instructions) }
        : { lines: lineDiff(String(a.instructions), String(b.instructions)) }),
    });
  }
  scalar(out, "model", "model", "Model", a.model, b.model, "the project's profile");
  scalar(out, "model", "backend", "Backend", a.backend, b.backend, "the project's backend");
  scalar(out, "limits", "limits.max_turns", "Max turns", a.limits.max_turns, b.limits.max_turns, "the role's default");
  scalar(out, "limits", "limits.max_minutes", "Max minutes", a.limits.max_minutes, b.limits.max_minutes, "the role's default");
  const usd = (v: unknown) => `$${v}`;
  scalar(out, "limits", "budget.run_usd", "Budget per run", a.budget.run_usd, b.budget.run_usd, "none", usd);
  scalar(out, "limits", "budget.daily_usd", "Budget per day", a.budget.daily_usd, b.budget.daily_usd, "none", usd);
  scalar(out, "limits", "budget.monthly_usd", "Budget per month", a.budget.monthly_usd, b.budget.monthly_usd, "none", usd);
  keyed<Binding>(
    out,
    "capabilities",
    "memories",
    a.memories,
    b.memories,
    labelOf,
    (m) => `Memory ${describeBinding(m)}`,
    (m) => (m.mode === "write" ? "read and written" : "read"),
    (x, y) => (x.mode === y.mode ? null : {}),
  );
  const common = (bindings: Binding[], other: Binding[]) =>
    bindings.map(labelOf).filter((l) => other.some((o) => labelOf(o) === l));
  if (common(a.memories, b.memories).join() !== common(b.memories, a.memories).join())
    out.push({
      group: "capabilities",
      field: "memories",
      label: "Memory order",
      kind: "changed",
      before: a.memories.map(labelOf).join(", "),
      after: b.memories.map(labelOf).join(", "),
      detail: "the earlier memory wins a subject two share",
    });

  type Skill = { slug: string; version?: number | null };
  keyed<Skill>(
    out,
    "capabilities",
    "skills",
    a.skills,
    b.skills,
    (s) => s.slug,
    (s) => `Skill ${s.slug}`,
    (s) => (s.version ? `version ${s.version}` : "latest version"),
    (x, y) => ((x.version ?? null) === (y.version ?? null) ? null : {}),
  );
  type Server = { connector: string; tools?: string[] };
  keyed<Server>(
    out,
    "capabilities",
    "mcp_servers",
    a.mcp_servers,
    b.mcp_servers,
    (s) => s.connector,
    (s) => `Tools of ${s.connector}`,
    (s) => ((s.tools ?? []).length ? (s.tools ?? []).join(", ") : "every tool"),
    (x, y) => {
      const was = new Set(x.tools ?? []);
      const now = new Set(y.tools ?? []);
      const plus = [...now].filter((t) => !was.has(t)).map((t) => `+${t}`);
      const minus = [...was].filter((t) => !now.has(t)).map((t) => `−${t}`);
      return plus.length || minus.length ? { detail: [...plus, ...minus].join("  ") } : null;
    },
  );
  keyed<{ slug: string }>(
    out,
    "capabilities",
    "knowledge",
    a.knowledge,
    b.knowledge,
    (k) => k.slug,
    (k) => `Knowledge base ${k.slug}`,
    () => "read at each run",
    () => null,
  );
  type Case = { name: string };
  keyed<Case>(
    out,
    "evals",
    "evals",
    a.evals,
    b.evals,
    (c) => c.name,
    (c) => `Case ${c.name}`,
    (c) => JSON.stringify(c),
    (x, y) => (JSON.stringify(x) === JSON.stringify(y) ? null : { detail: "its input or its assertions" }),
  );
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (KNOWN.has(key)) continue;
    const was = key in a ? JSON.stringify(a[key]) : undefined;
    const now = key in b ? JSON.stringify(b[key]) : undefined;
    if (was === now) continue;
    out.push({
      group: "other",
      field: key,
      label: `Field ${key}`,
      kind: was === undefined ? "added" : now === undefined ? "removed" : "changed",
      before: was === undefined ? undefined : show(JSON.parse(was), "none"),
      after: now === undefined ? undefined : show(JSON.parse(now), "none"),
    });
  }
  return out;
}
