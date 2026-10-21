// SPDX-License-Identifier: Apache-2.0
import { STAGE_ROLES } from "@/components/model-catalogue";

/**
 * The structured form of the organisation's policy ceiling (S42-08): the fields, their minimums, the
 * way a ceiling is read into form values and a form is written back into a ceiling. It mirrors
 * `packages/core/src/choregos_core/policy_ceiling.py` (`extra="forbid"` everywhere).
 *
 * A local type, not the generated `PolicyCeiling`: that one lacks `monthly_org_usd` and `network`
 * (contract drift, reported in the S42-08 PR; the contracts are not changed here).
 */
export type CeilingSection = "budgets" | "attempts" | "scope" | "findings";
export type CeilingShape = Partial<Record<CeilingSection, Record<string, unknown>>> & Record<string, unknown>;

export const SIZES = ["S", "M", "L", "XL"] as const;
/** The rows of a per-role table: `default` caps every role the ceiling does not name. */
export const BUDGET_ROLES = ["default", ...STAGE_ROLES] as const;

/**
 * What the platform applies when neither the ceiling nor a project says otherwise. Shown as
 * placeholders. `tests/docs/test_ceiling_form_defaults.py` compares it with the policy schema and the
 * Python constants, so it cannot drift.
 */
export const PLATFORM_DEFAULTS = {
  ticket_usd: { S: 8, M: 25, L: 60, XL: 120 },
  stage_usd: { S: 3, M: 8, L: 18, XL: 30 },
  max_turns: 80,
  max_minutes: 60,
  attempts_default_max: 2,
  dod_iterations: 3,
  auto_grant_max_files: 3,
  max_diff_files: 60,
  max_diff_lines: 3000,
  findings_max_per_run: 5,
} as const;

type Kind = "int" | "usd";
export type FieldSpec = { path: string[]; kind: Kind; min: number; exclusive?: boolean };

const key = (path: readonly string[]) => path.join(".");

function fieldsOf(): FieldSpec[] {
  const out: FieldSpec[] = [];
  for (const size of SIZES) out.push({ path: ["budgets", "ticket_usd", size], kind: "usd", min: 0, exclusive: true });
  for (const role of BUDGET_ROLES)
    for (const size of SIZES) out.push({ path: ["budgets", "stage_usd", role, size], kind: "usd", min: 0, exclusive: true });
  for (const table of ["max_turns", "max_minutes"])
    for (const role of BUDGET_ROLES) out.push({ path: ["budgets", table, role], kind: "int", min: 1 });
  out.push({ path: ["budgets", "tool_calls_per_run"], kind: "int", min: 1 });
  out.push({ path: ["budgets", "daily_project_usd"], kind: "usd", min: 0, exclusive: true });
  out.push({ path: ["budgets", "monthly_org_usd"], kind: "usd", min: 0, exclusive: true });
  out.push({ path: ["attempts", "default_max"], kind: "int", min: 1 });
  for (const role of STAGE_ROLES) out.push({ path: ["attempts", "by_role", role], kind: "int", min: 1 });
  out.push({ path: ["attempts", "dod_iterations"], kind: "int", min: 0 });
  out.push({ path: ["scope", "auto_grant_max_files"], kind: "int", min: 0 });
  out.push({ path: ["scope", "max_diff_files"], kind: "int", min: 1 });
  out.push({ path: ["scope", "max_diff_lines"], kind: "int", min: 1 });
  out.push({ path: ["findings", "max_per_run"], kind: "int", min: 0 });
  return out;
}

export const FIELDS: readonly FieldSpec[] = fieldsOf();
const SPEC_BY_KEY = new Map(FIELDS.map((f) => [key(f.path), f]));

export const fieldKey = key;
export const isFormField = (k: string) => SPEC_BY_KEY.has(k);
const SECTIONS: readonly CeilingSection[] = ["budgets", "attempts", "scope", "findings"];

function at(root: unknown, path: readonly string[]): unknown {
  let node = root;
  for (const part of path) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

/** The text each form field shows for a stored ceiling; a field the ceiling does not cap is "". */
export function toFormValues(ceiling: Record<string, unknown> | null | undefined): Record<string, string> {
  const values: Record<string, string> = {};
  for (const f of FIELDS) {
    const value = at(ceiling, f.path);
    values[key(f.path)] = typeof value === "number" ? String(value) : "";
  }
  return values;
}

/** The message under a field whose text breaks its minimum; `null` when it is empty (no cap) or fine. */
export function problemWith(spec: FieldSpec, text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return "Enter a number";
  if (spec.kind === "int" && !Number.isInteger(n)) return "Enter a whole number";
  if (spec.exclusive ? n <= spec.min : n < spec.min) return spec.exclusive ? `Must be more than ${spec.min}` : `Must be at least ${spec.min}`;
  return null;
}

/** Field-level errors, by field key; empty when the form can be saved. */
export function validate(values: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of FIELDS) {
    const message = problemWith(f, values[key(f.path)] ?? "");
    if (message) errors[key(f.path)] = message;
  }
  return errors;
}

function setAt(root: Record<string, unknown>, path: readonly string[], value: number | undefined) {
  const parents: Record<string, unknown>[] = [root];
  for (const part of path.slice(0, -1)) {
    const next = parents[parents.length - 1]![part];
    if (next === null || typeof next !== "object" || Array.isArray(next)) {
      if (value === undefined) return;
      parents[parents.length - 1]![part] = {};
    }
    parents.push(parents[parents.length - 1]![part] as Record<string, unknown>);
  }
  const last = path[path.length - 1]!;
  if (value === undefined) delete parents[parents.length - 1]![last];
  else parents[parents.length - 1]![last] = value;
  // Drop the tables a deletion left empty.
  for (let i = path.length - 1; i >= 2; i--) {
    if (Object.keys(parents[i]!).length === 0) delete parents[i - 1]![path[i - 1]!];
  }
}

/**
 * The ceiling to save: `fresh` (the ceiling as it is stored now) with the four form-owned sections'
 * fields replaced by the form's values. An empty field is omitted; `network` and every key the form
 * does not own (a role it has no row for, an unknown section) stay as they are.
 */
export function mergeFormInto(fresh: Record<string, unknown> | null | undefined, values: Record<string, string>): Record<string, unknown> {
  const merged = structuredClone(fresh ?? {}) as Record<string, unknown>;
  for (const f of FIELDS) {
    const text = (values[key(f.path)] ?? "").trim();
    const section = f.path[0]!;
    if (text === "" && merged[section] === undefined) continue;
    if (merged[section] === undefined || merged[section] === null || typeof merged[section] !== "object") merged[section] = {};
    setAt(merged, f.path, text === "" ? undefined : Number(text));
  }
  for (const section of SECTIONS) {
    const body = merged[section];
    if (body && typeof body === "object" && Object.keys(body).length === 0) delete merged[section];
  }
  return merged;
}

/**
 * Where a server 422 points in the form. The API answers `errors: [{loc: ["ceiling", "budgets",
 * "ticket_usd", "M"], msg}]`; a `loc` that is a form field gives a field-level message, the others
 * come back as general messages.
 */
export function mapServerErrors(errors: unknown[] | undefined): { fields: Record<string, string>; general: string[] } {
  const fields: Record<string, string> = {};
  const general: string[] = [];
  for (const entry of errors ?? []) {
    const { loc, msg } = (entry ?? {}) as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const path = loc.map(String).filter((part, i) => !(i === 0 && part === "ceiling"));
    if (isFormField(key(path))) fields[key(path)] = msg;
    else general.push(`${path.join(".")}: ${msg}`);
  }
  return { fields, general };
}
