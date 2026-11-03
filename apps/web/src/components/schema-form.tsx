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
 * of multi-line texts (`format: "multiline"`: a PEM certificate) and of mappings
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
export function missingFields(schema: JsonSchema, valeurs: Values): string[] {
  return (schema.required ?? []).filter((nom) => {
    const value = valeurs[nom];
    if (value === undefined || value === null || value === "") return true;
    if (Array.isArray(value)) return value.length === 0;
    return typeof value === "object" && Object.keys(value).length === 0;
  });
}

/** The schema's default values, to seed an empty form. */
export function defaultValues(schema: JsonSchema): Values {
  const values: Values = {};
  for (const [nom, propriete] of Object.entries(schema.properties ?? {})) {
    if (propriete.default !== undefined) values[nom] = propriete.default;
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
      {proprietes.map(([nom, propriete]) => (
        <Field
          key={nom}
          id={`${base}-${nom}`}
          nom={nom}
          propriete={propriete}
          requis={requis.has(nom)}
          valeur={value[nom]}
          disabled={disabled}
          org={org}
          connectorType={connectorType}
          onChange={(valeur) => {
            const suivantes = { ...value };
            if (valeur === undefined) delete suivantes[nom];
            else suivantes[nom] = valeur;
            onChange(suivantes);
          }}
        />
      ))}
    </div>
  );
}

function Field({
  id,
  nom,
  propriete,
  requis,
  valeur,
  disabled,
  org,
  connectorType,
  onChange,
}: {
  id: string;
  nom: string;
  propriete: JsonSchema;
  requis: boolean;
  valeur: unknown;
  disabled?: boolean;
  org?: string;
  connectorType?: string;
  onChange: (valeur: unknown) => void;
}) {
  const libelle = propriete.title ?? nom.replaceAll("_", " ");
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
          checked={Boolean(valeur)}
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
        value={valeur === undefined ? "" : String(valeur)}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      >
        <option value="">—</option>
        {propriete.enum.map((choix) => (
          <option key={String(choix)} value={String(choix)}>
            {String(choix)}
          </option>
        ))}
      </Select>
    );
  } else if (propriete.format === "github-repo") {
    // A repository picked from what the Choregos GitHub App sees, never an installation id (ADR 0073).
    control = (
      <GitHubRepoField
        id={id}
        value={typeof valeur === "string" ? valeur : ""}
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
        valeur={valeur}
        disabled={disabled}
        describedBy={aide}
        org={org}
        onChange={onChange}
      />
    );
  } else if (nom === "data_handling" && connectorType && propriete.properties?.terms_ref) {
    control = <DataHandlingField id={id} type={connectorType} value={valeur} disabled={disabled} onChange={onChange} />;
  } else if (propriete.type === "object") {
    control = <Correspondance id={id} valeur={valeur} disabled={disabled} aide={aide} onChange={onChange} />;
  } else if (propriete.format === "multiline") {
    control = (
      <Textarea
        id={id}
        className="w-auto font-mono text-xs"
        rows={6}
        value={typeof valeur === "string" ? valeur : ""}
        required={requis}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      />
    );
  } else if (propriete.type === "array") {
    control = (
      <Input
        id={id}
        value={Array.isArray(valeur) ? valeur.join(", ") : ""}
        placeholder="Comma separated"
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          const list = event.target.value
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean);
          onChange(list.length > 0 ? list : undefined);
        }}
      />
    );
  } else {
    const isNumber = propriete.type === "integer" || propriete.type === "number";
    control = (
      <Input
        id={id}
        type={isNumber ? "number" : propriete.format === "date" ? "date" : propriete.format === "password" ? "password" : "text"}
        value={valeur === undefined || valeur === null ? "" : String(valeur)}
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
    <div className={nom === "data_handling" && connectorType ? "space-y-1 md:col-span-2" : "space-y-1"}>
      {label}
      {control}
      {indication}
    </div>
  );
}

/** `key = value`, one per line: what is read, and what is typed. */
export function parseMapping(texte: string): { valeurs: Record<string, string>; illisibles: number[] } {
  const parsed: Record<string, string> = {};
  const illisibles: number[] = [];
  texte.split("\n").forEach((ligne, index) => {
    if (!ligne.trim()) return;
    const egal = ligne.indexOf("=");
    const key = egal < 0 ? "" : ligne.slice(0, egal).trim();
    if (!key) {
      illisibles.push(index + 1);
      return;
    }
    parsed[key] = ligne.slice(egal + 1).trim();
  });
  return { valeurs: parsed, illisibles };
}

function writeMapping(valeur: unknown): string {
  if (!valeur || typeof valeur !== "object" || Array.isArray(valeur)) return "";
  return Object.entries(valeur as Record<string, unknown>)
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
  valeur,
  disabled,
  aide,
  onChange,
}: {
  id: string;
  valeur: unknown;
  disabled?: boolean;
  aide?: string;
  onChange: (valeur: unknown) => void;
}) {
  const [texte, setTexte] = useState(() => writeMapping(valeur));
  const [recue, setRecue] = useState(valeur);
  if (valeur !== recue) {
    // A value that came from elsewhere (the read) replaces the text; one that was just typed does not.
    setRecue(valeur);
    const tapee = parseMapping(texte).valeurs;
    if (JSON.stringify(tapee) !== JSON.stringify(valeur ?? {})) setTexte(writeMapping(valeur));
  }
  const { illisibles } = parseMapping(texte);
  return (
    <>
      <Textarea
        id={id}
        className="w-full font-mono"
        rows={4}
        value={texte}
        placeholder="key = value"
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          setTexte(event.target.value);
          const { valeurs } = parseMapping(event.target.value);
          onChange(Object.keys(valeurs).length > 0 ? valeurs : undefined);
        }}
      />
      {illisibles.length > 0 && (
        <p className="text-xs text-danger" role="alert">
          Line{illisibles.length > 1 ? "s" : ""} {illisibles.join(", ")}: key = value expected
        </p>
      )}
    </>
  );
}

/** The path an asset field uploads to, `{org}` filled in; null when it cannot be (no org, or another placeholder). */
export function uploadPath(modele: string | undefined, org: string | undefined): string | null {
  if (!modele) return null;
  if (modele.includes("{org}")) {
    if (!org) return null;
    modele = modele.replaceAll("{org}", encodeURIComponent(org));
  }
  return /[{}]/.test(modele) ? null : modele;
}

/** Why a file cannot be offered, from the schema's limits, or null. A courtesy: the upload route is the judge. */
export function fileRefusal(fichier: { type: string; size: number }, propriete: JsonSchema): string | null {
  const accepte = propriete["x-accept"];
  if (accepte && accepte.length > 0 && !accepte.includes(fichier.type)) {
    return `This file type is not accepted (${accepte.join(", ")})`;
  }
  const max = propriete["x-max-bytes"];
  if (max !== undefined && fichier.size > max) {
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
  valeur,
  disabled,
  describedBy,
  org,
  onChange,
}: {
  id: string;
  propriete: JsonSchema;
  valeur: unknown;
  disabled?: boolean;
  describedBy?: string;
  org?: string;
  onChange: (valeur: unknown) => void;
}) {
  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const path = uploadPath(propriete["x-upload"], org);
  const actuel = typeof valeur === "string" && valeur !== "" ? valeur : null;
  // Only a same-origin served file is drawn; any other reference is shown as text, never loaded.
  const image = actuel ? safeAsset(actuel) : undefined;

  async function choose(fichier: File | undefined) {
    setErreur(null);
    if (!fichier || !path) return;
    const refusal = fileRefusal(fichier, propriete);
    if (refusal) return setErreur(refusal);
    setEnvoi(true);
    try {
      const { ref } = await api.sectionUpload(path, fichier);
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
