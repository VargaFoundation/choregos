// SPDX-License-Identifier: Apache-2.0
/**
 * A JSON Schema object as rows a person edits (ADR 0112 §3): the work item's fields (`metadata.inputs`)
 * and a human task's form. A property this editor cannot describe (an array, a nested object, a
 * constraint it has no input for) is kept as it was, untouched, and shown as such.
 */
export type FieldType = "string" | "number" | "integer" | "boolean" | "date";

export type FieldRow = {
  name: string;
  title: string;
  type: FieldType;
  required: boolean;
  /** The original property, when this editor cannot describe it: written back unchanged. */
  kept?: Record<string, unknown>;
};

export const FIELD_NAME = /^[a-z][a-z0-9_]{0,63}$/;
const SIMPLE_KEYS = new Set(["type", "title", "format"]);

type Schema = { type?: string; required?: string[]; properties?: Record<string, Record<string, unknown>> } & Record<string, unknown>;

export function schemaToRows(schema: unknown): FieldRow[] {
  const s = (schema ?? {}) as Schema;
  const required = new Set(s.required ?? []);
  return Object.entries(s.properties ?? {}).map(([name, property]) => {
    const p = property ?? {};
    const simple = Object.keys(p).every((k) => SIMPLE_KEYS.has(k));
    const type: FieldType | undefined =
      p.type === "string" && p.format === "date"
        ? "date"
        : p.type === "string" && p.format === undefined
          ? "string"
          : p.type === "number" || p.type === "integer" || p.type === "boolean"
            ? (p.type as FieldType)
            : undefined;
    const title = typeof p.title === "string" ? p.title : "";
    return simple && type
      ? { name, title, type, required: required.has(name) }
      : { name, title, type: "string", required: required.has(name), kept: p };
  });
}

export function rowsToSchema(rows: FieldRow[], base?: unknown): Record<string, unknown> | null {
  const kept = rows.filter((r) => FIELD_NAME.test(r.name));
  if (kept.length === 0) return null;
  // Keys of the object this editor does not handle (`additionalProperties`…) are carried over.
  const rest = Object.fromEntries(
    Object.entries((base ?? {}) as Schema).filter(([key]) => !["properties", "required", "type"].includes(key)),
  );
  const properties = Object.fromEntries(
    kept.map((r) => {
      if (r.kept) return [r.name, r.kept];
      const property: Record<string, unknown> =
        r.type === "date" ? { type: "string", format: "date" } : { type: r.type };
      if (r.title.trim()) property.title = r.title.trim();
      return [r.name, property];
    }),
  );
  const required = kept.filter((r) => r.required).map((r) => r.name);
  return { ...rest, type: "object", ...(required.length ? { required } : {}), properties };
}

/** The work item's fields after a task's form adds its own: a form may only fill declared fields. */
export function withFields(inputs: unknown, form: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!form) return (inputs as Record<string, unknown>) ?? null;
  const current = (inputs ?? { type: "object", properties: {} }) as Schema;
  const properties = { ...(current.properties ?? {}) };
  for (const [name, property] of Object.entries((form as Schema).properties ?? {})) {
    if (!(name in properties)) properties[name] = property;
  }
  return { ...current, type: "object", properties };
}
