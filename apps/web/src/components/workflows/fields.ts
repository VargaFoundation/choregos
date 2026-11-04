// SPDX-License-Identifier: Apache-2.0
/**
 * A request's fields, read from its workflow's `metadata.inputs`, a JSON Schema (ADR 0031,
 * S16-04). The "new request" form is deduced from it; the API validates what it sends, and stays the judge.
 */
export type Field = {
  name: string;
  label: string;
  hint?: string;
  required: boolean;
  control: "text" | "date" | "number" | "integer" | "boolean" | "choice" | "list";
  choices?: string[];
};

type Property = {
  type?: string;
  format?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  items?: { type?: string };
};

export function fieldsFromSchema(schema: Record<string, unknown> | undefined): Field[] {
  const properties = (schema?.properties ?? {}) as Record<string, Property>;
  const required = new Set(Array.isArray(schema?.required) ? (schema.required as string[]) : []);
  return Object.entries(properties).map(([name, property]) => {
    const control: Field["control"] = property.enum
      ? "choice"
      : property.type === "boolean"
        ? "boolean"
        : property.type === "integer"
          ? "integer"
          : property.type === "number"
            ? "number"
            : property.type === "array"
              ? "list"
              : property.format === "date"
                ? "date"
                : "text";
    return {
      name,
      label: property.title ?? name.replaceAll("_", " "),
      hint: property.description,
      required: required.has(name),
      control,
      choices: property.enum?.map(String),
    };
  });
}

/**
 * The titles a workflow gives its fields in `metadata.inputs`, by field name (S55-14): a ticket's page
 * labels its request's fields with them. A field without a title is left out — its name, humanised,
 * stands in.
 */
export function fieldTitles(schema: unknown): Record<string, string> {
  const properties = (schema as { properties?: unknown } | null | undefined)?.properties;
  if (typeof properties !== "object" || properties === null) return {};
  const titles: Record<string, string> = {};
  for (const [name, property] of Object.entries(properties as Record<string, Property | null>)) {
    const title = property?.title;
    if (typeof title === "string" && title.trim()) titles[name] = title.trim();
  }
  return titles;
}

/** What was typed, typed as the schema expects; an empty field is not sent. */
export function valuesForApi(fields: Field[], entries: Record<string, string | boolean>): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    const entry = entries[field.name];
    if (entry === undefined || entry === "") continue;
    if (field.control === "boolean") values[field.name] = Boolean(entry);
    else if (field.control === "integer") values[field.name] = Number.parseInt(String(entry), 10);
    else if (field.control === "number") values[field.name] = Number(entry);
    else if (field.control === "list")
      values[field.name] = String(entry)
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    else values[field.name] = String(entry);
  }
  return values;
}
