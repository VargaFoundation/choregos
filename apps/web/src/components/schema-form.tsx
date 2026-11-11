// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId, useState } from "react";
import { DataHandlingField } from "@/components/data-handling-field";
import { GitHubRepoField } from "@/components/github-repo-field";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { safeAsset } from "@/lib/served-theme";

/**
 * A form drawn from a JSON Schema: that of an administration section declared by an
 * extension (ADR 0032), tomorrow that of a connector (ADR 0034). Built here rather than with `@rjsf`:
 * the bundle budget, and only the cases that are used: an object of scalars, of choices, of lists,
 * of fixed objects (nested fields), of closed lists (`items.enum`: checkboxes), of typed lists (integers,
 * URIs), of multi-line texts (`format: "multiline"`: a PEM certificate) and of mappings
 * (`type: "object"` of strings: an IdP group -> a role group), one per line.
 *
 * The API remains the judge: this form refuses only what is missing, it does not revalidate the schema.
 */
export type JsonSchema = {
  type?: string;
  title?: string;
  description?: string;
  format?: string;
  enum?: unknown[];
  items?: JsonSchema;
  additionalProperties?: JsonSchema | boolean;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
  /** `format: "asset"` (S42-13): where the chosen file is POSTed, what it may be, and how large. */
  "x-upload"?: string;
  "x-accept"?: string[];
  "x-max-bytes"?: number;
};

type Values = Record<string, unknown>;

/** The required fields left empty: enough to disable "save" before the API. */
export function missingFields(schema: JsonSchema, values: Values): string[] {
  return (schema.required ?? []).filter((name) => {
    const value = values[name];
    if (value === undefined || value === null || value === "") return true;
    if (Array.isArray(value)) return value.length === 0;
    return typeof value === "object" && Object.keys(value).length === 0;
  });
}

/** The schema's default values, to seed an empty form. */
export function defaultValues(schema: JsonSchema): Values {
  const values: Values = {};
  for (const [name, propriete] of Object.entries(schema.properties ?? {})) {
    if (propriete.default !== undefined) values[name] = propriete.default;
  }
  return values;
}

export function SchemaForm({
  schema,
  value,
  onChange,
  disabled,
  org,
  connectorType,
}: {
  schema: JsonSchema;
  value: Values;
  onChange: (value: Values) => void;
  disabled?: boolean;
  /** Fills `{org}` in an asset field's `x-upload`; without it such a field cannot upload. */
  org?: string;
  /** The `web_search` backend type being configured: gives `data_handling` its structured editor (S40-01j). */
  connectorType?: string;
}) {
  const base = useId();
  const requis = new Set(schema.required ?? []);
  const proprietes = Object.entries(schema.properties ?? {});
  if (proprietes.length === 0) return null;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {proprietes.map(([name, propriete]) => (
        <Field
          key={name}
          id={`${base}-${name}`}
          name={name}
          propriete={propriete}
          requis={requis.has(name)}
          value={value[name]}
          disabled={disabled}
          org={org}
          connectorType={connectorType}
          onChange={(next) => {
            const suivantes = { ...value };
            if (next === undefined) delete suivantes[name];
            else suivantes[name] = next;
            onChange(suivantes);
          }}
        />
      ))}
    </div>
  );
}

function Field({
  id,
  name,
  propriete,
  requis,
  value,
  disabled,
  org,
  connectorType,
  onChange,
}: {
  id: string;
  name: string;
  propriete: JsonSchema;
  requis: boolean;
  value: unknown;
  disabled?: boolean;
  org?: string;
  connectorType?: string;
  onChange: (value: unknown) => void;
}) {
  const libelle = propriete.title ?? name.replaceAll("_", " ");
  const aide = propriete.description ? `${id}-aide` : undefined;
  const label = (
    <label htmlFor={id} className="text-xs text-ink-muted">
      {libelle}
      {requis && <span aria-hidden> *</span>}
      {requis && <span className="sr-only"> (required)</span>}
    </label>
  );
  const indication = propriete.description && (
    <p id={aide} className="text-xs text-ink-muted">
      {propriete.description}
    </p>
  );

  if (propriete.type === "boolean") {
    return (
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          disabled={disabled}
          aria-describedby={aide}
          onChange={(event) => onChange(event.target.checked)}
        />
        {label}
        {indication}
      </div>
    );
  }
  let control;
  if (propriete.enum) {
    control = (
      <Select
        id={id}
        value={value === undefined ? "" : String(value)}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      >
        <option value="">—</option>
        {propriete.enum.map((choice) => (
          <option key={String(choice)} value={String(choice)}>
            {String(choice)}
          </option>
        ))}
      </Select>
    );
  } else if (propriete.format === "github-repo") {
    // A repository picked from what the Choregos GitHub App sees, never an installation id (ADR 0073).
    control = (
      <GitHubRepoField
        id={id}
        value={typeof value === "string" ? value : ""}
        disabled={disabled}
        describedBy={aide}
        onChange={onChange}
      />
    );
  } else if (propriete.format === "asset") {
    control = (
      <AssetField
        id={id}
        propriete={propriete}
        value={value}
        disabled={disabled}
        describedBy={aide}
        org={org}
        onChange={onChange}
      />
    );
  } else if (name === "data_handling" && connectorType && propriete.properties?.terms_ref) {
    control = <DataHandlingField id={id} type={connectorType} value={value} disabled={disabled} onChange={onChange} />;
  } else if (propriete.type === "object" && propriete.properties) {
    // A fixed object (named properties): its own fields, not a `key = value` mapping.
    control = (
      <fieldset className="space-y-2 rounded-control border border-line p-2" aria-describedby={aide}>
        <SchemaForm
          schema={propriete}
          value={value && typeof value === "object" && !Array.isArray(value) ? (value as Values) : {}}
          disabled={disabled}
          org={org}
          onChange={(next) => onChange(Object.keys(next).length > 0 ? next : undefined)}
        />
      </fieldset>
    );
  } else if (propriete.type === "object") {
    control = <Correspondance id={id} value={value} disabled={disabled} aide={aide} onChange={onChange} />;
  } else if (propriete.format === "multiline") {
    control = (
      <Textarea
        id={id}
        className="w-auto font-mono text-xs"
        rows={6}
        value={typeof value === "string" ? value : ""}
        required={requis}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      />
    );
  } else if (propriete.type === "array" && propriete.items?.enum) {
    control = <ChoiceList id={id} choices={propriete.items.enum} value={value} disabled={disabled} onChange={onChange} />;
  } else if (propriete.type === "array") {
    control = <ListField id={id} items={propriete.items} value={value} disabled={disabled} aide={aide} onChange={onChange} />;
  } else {
    const isNumber = propriete.type === "integer" || propriete.type === "number";
    control = (
      <Input
        id={id}
        type={inputType(propriete, isNumber)}
        value={value === undefined || value === null ? "" : String(value)}
        required={requis}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") return onChange(undefined);
          if (propriete.type === "integer") return onChange(Number.parseInt(raw, 10));
          if (propriete.type === "number") return onChange(Number(raw));
          return onChange(raw);
        }}
      />
    );
  }
  return (
    <div className={name === "data_handling" && connectorType ? "space-y-1 md:col-span-2" : "space-y-1"}>
      {label}
      {control}
      {indication}
    </div>
  );
}

function inputType(propriete: JsonSchema, isNumber: boolean): string {
  if (isNumber) return "number";
  const byFormat: Record<string, string> = { date: "date", password: "password", uri: "url", email: "email" };
  return byFormat[propriete.format ?? ""] ?? "text";
}

/** One item of a list as the schema types it (`integer` and `number` lists hold numbers, not strings). */
export function listItem(raw: string, items: JsonSchema | undefined): unknown {
  if (items?.type === "integer") return Number.parseInt(raw, 10);
  if (items?.type === "number") return Number(raw);
  return raw;
}

/**
 * A list, comma separated. The typed text stays as typed (a trailing comma is kept: the next item comes
 * after it), and it follows the value when that changes from elsewhere.
 */
function ListField({
  id,
  items,
  value,
  disabled,
  aide,
  onChange,
}: {
  id: string;
  items?: JsonSchema;
  value: unknown;
  disabled?: boolean;
  aide?: string;
  onChange: (value: unknown) => void;
}) {
  const written = Array.isArray(value) ? value.join(", ") : "";
  const [text, setText] = useState(written);
  const [seen, setSeen] = useState(written);
  if (written !== seen) {
    setSeen(written);
    const typed = text
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .join(", ");
    if (typed !== written) setText(written);
  }
  return (
    <Input
      id={id}
      type={items?.format === "uri" ? "url" : "text"}
      value={text}
      placeholder="Comma separated"
      disabled={disabled}
      aria-describedby={aide}
      onChange={(event) => {
        setText(event.target.value);
        const list = event.target.value
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean)
          .map((part) => listItem(part, items))
          .filter((item) => typeof item !== "number" || !Number.isNaN(item));
        onChange(list.length > 0 ? list : undefined);
      }}
    />
  );
}

/** A list whose items are a closed set (`items.enum`): one checkbox per choice. */
function ChoiceList({
  id,
  choices,
  value,
  disabled,
  onChange,
}: {
  id: string;
  choices: unknown[];
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
}) {
  const chosen = Array.isArray(value) ? value : [];
  return (
    <div id={id} role="group" className="flex flex-wrap gap-3">
      {choices.map((choice) => (
        <label key={String(choice)} className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={chosen.includes(choice)}
            disabled={disabled}
            onChange={(event) => {
              const next = event.target.checked
                ? choices.filter((c) => c === choice || chosen.includes(c))
                : chosen.filter((c) => c !== choice);
              onChange(next.length > 0 ? next : undefined);
            }}
          />
          {String(choice)}
        </label>
      ))}
    </div>
  );
}

/** `key = value`, one per line: what is read, and what is typed. */
export function parseMapping(text: string): { values: Record<string, string>; unreadable: number[] } {
  const parsed: Record<string, string> = {};
  const unreadable: number[] = [];
  text.split("\n").forEach((line, index) => {
    if (!line.trim()) return;
    const egal = line.indexOf("=");
    const key = egal < 0 ? "" : line.slice(0, egal).trim();
    if (!key) {
      unreadable.push(index + 1);
      return;
    }
    parsed[key] = line.slice(egal + 1).trim();
  });
  return { values: parsed, unreadable };
}

function writeMapping(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([cle, contenu]) => `${cle} = ${String(contenu)}`)
    .join("\n");
}

/**
 * A mapping, one line per pair. The typed text stays as it is while typing; an
 * unreadable line is reported, never erased; and it follows the value when it changes from elsewhere (the
 * form's read arrives after its first render).
 */
function Correspondance({
  id,
  value,
  disabled,
  aide,
  onChange,
}: {
  id: string;
  value: unknown;
  disabled?: boolean;
  aide?: string;
  onChange: (value: unknown) => void;
}) {
  const [text, setTexte] = useState(() => writeMapping(value));
  const [recue, setRecue] = useState(value);
  if (value !== recue) {
    // A value that came from elsewhere (the read) replaces the text; one that was just typed does not.
    setRecue(value);
    const tapee = parseMapping(text).values;
    if (JSON.stringify(tapee) !== JSON.stringify(value ?? {})) setTexte(writeMapping(value));
  }
  const { unreadable } = parseMapping(text);
  return (
    <>
      <Textarea
        id={id}
        className="w-full font-mono"
        rows={4}
        value={text}
        placeholder="key = value"
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          setTexte(event.target.value);
          const { values } = parseMapping(event.target.value);
          onChange(Object.keys(values).length > 0 ? values : undefined);
        }}
      />
      {unreadable.length > 0 && (
        <p className="text-xs text-danger" role="alert">
          Line{unreadable.length > 1 ? "s" : ""} {unreadable.join(", ")}: key = value expected
        </p>
      )}
    </>
  );
}

/** The path an asset field uploads to, `{org}` filled in; null when it cannot be (no org, or another placeholder). */
export function uploadPath(template: string | undefined, org: string | undefined): string | null {
  if (!template) return null;
  if (template.includes("{org}")) {
    if (!org) return null;
    template = template.replaceAll("{org}", encodeURIComponent(org));
  }
  return /[{}]/.test(template) ? null : template;
}

/** Why a file cannot be offered, from the schema's limits, or null. A courtesy: the upload route is the judge. */
export function fileRefusal(file: { type: string; size: number }, propriete: JsonSchema): string | null {
  const accepte = propriete["x-accept"];
  if (accepte && accepte.length > 0 && !accepte.includes(file.type)) {
    return `This file type is not accepted (${accepte.join(", ")})`;
  }
  const max = propriete["x-max-bytes"];
  if (max !== undefined && file.size > max) {
    return `This file is larger than ${max >= 1024 ? `${Math.round(max / 1024)} KiB` : `${max} bytes`}`;
  }
  return null;
}

/**
 * A file the person uploads (`format: "asset"`, S42-13): the current one as a thumbnail, a file
 * input, and "Remove". The file goes to the field's `x-upload` as the raw body; the `ref` the
 * answer carries becomes the field's value, and the form's own Save writes it. Removing writes
 * `null`: an explicit "no file", not a field left out.
 */
function AssetField({
  id,
  propriete,
  value,
  disabled,
  describedBy,
  org,
  onChange,
}: {
  id: string;
  propriete: JsonSchema;
  value: unknown;
  disabled?: boolean;
  describedBy?: string;
  org?: string;
  onChange: (value: unknown) => void;
}) {
  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const path = uploadPath(propriete["x-upload"], org);
  const actuel = typeof value === "string" && value !== "" ? value : null;
  // Only a same-origin served file is drawn; any other reference is shown as text, never loaded.
  const image = actuel ? safeAsset(actuel) : undefined;

  async function choose(file: File | undefined) {
    setErreur(null);
    if (!file || !path) return;
    const refusal = fileRefusal(file, propriete);
    if (refusal) return setErreur(refusal);
    setEnvoi(true);
    try {
      const { ref } = await api.sectionUpload(path, file);
      onChange(ref);
    } catch (cause) {
      setErreur(cause instanceof Error ? cause.message : "upload refused");
    } finally {
      setEnvoi(false);
    }
  }

  return (
    <div className="space-y-2" data-testid={`asset-${id}`}>
      {actuel &&
        (image ? (
          // A served file: `next/image` would add an optimiser in front of a same-origin asset.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={image} alt="Current file" className="h-10 w-auto max-w-40 rounded-control border border-line bg-surface-muted p-1" />
        ) : (
          <p className="break-all font-mono text-xs text-ink-muted">{actuel}</p>
        ))}
      <div className="flex flex-wrap items-center gap-2">
        <input
          id={id}
          type="file"
          accept={propriete["x-accept"]?.join(",")}
          disabled={disabled || envoi || !path}
          aria-describedby={describedBy}
          className="text-xs"
          // The same file chosen twice must fire again.
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            void choose(file);
          }}
        />
        {actuel && (
          <Button size="sm" tone="default" type="button" disabled={disabled || envoi} onClick={() => onChange(null)}>
            Remove
          </Button>
        )}
        {envoi && <span className="text-xs text-ink-muted">Uploading…</span>}
      </div>
      {erreur && (
        <p className="text-xs text-danger" role="alert">
          {erreur}
        </p>
      )}
    </div>
  );
}
