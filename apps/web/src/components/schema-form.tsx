// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId } from "react";

/**
 * Un formulaire tiré d'un JSON Schema — celui d'une section d'administration déclarée par un
 * greffon (ADR 0032), demain celui d'un connecteur (ADR 0034). Fait ici plutôt qu'avec `@rjsf` :
 * le budget du bundle, et les seuls cas qui servent — un objet de scalaires, de choix et de listes.
 *
 * L'API reste juge : ce formulaire ne refuse que ce qui manque, il ne revalide pas le schéma.
 */
export type JsonSchema = {
  type?: string;
  title?: string;
  description?: string;
  format?: string;
  enum?: unknown[];
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
};

type Valeurs = Record<string, unknown>;

/** Les champs obligatoires laissés vides : de quoi désactiver « enregistrer » avant l'API. */
export function champsManquants(schema: JsonSchema, valeurs: Valeurs): string[] {
  return (schema.required ?? []).filter((nom) => {
    const valeur = valeurs[nom];
    return valeur === undefined || valeur === null || valeur === "" || (Array.isArray(valeur) && valeur.length === 0);
  });
}

/** Les valeurs par défaut du schéma, pour amorcer un formulaire vide. */
export function valeursParDefaut(schema: JsonSchema): Valeurs {
  const valeurs: Valeurs = {};
  for (const [nom, propriete] of Object.entries(schema.properties ?? {})) {
    if (propriete.default !== undefined) valeurs[nom] = propriete.default;
  }
  return valeurs;
}

export function SchemaForm({
  schema,
  value,
  onChange,
  disabled,
}: {
  schema: JsonSchema;
  value: Valeurs;
  onChange: (value: Valeurs) => void;
  disabled?: boolean;
}) {
  const base = useId();
  const requis = new Set(schema.required ?? []);
  const proprietes = Object.entries(schema.properties ?? {});
  if (proprietes.length === 0) return null;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {proprietes.map(([nom, propriete]) => (
        <Champ
          key={nom}
          id={`${base}-${nom}`}
          nom={nom}
          propriete={propriete}
          requis={requis.has(nom)}
          valeur={value[nom]}
          disabled={disabled}
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

function Champ({
  id,
  nom,
  propriete,
  requis,
  valeur,
  disabled,
  onChange,
}: {
  id: string;
  nom: string;
  propriete: JsonSchema;
  requis: boolean;
  valeur: unknown;
  disabled?: boolean;
  onChange: (valeur: unknown) => void;
}) {
  const libelle = propriete.title ?? nom.replaceAll("_", " ");
  const aide = propriete.description ? `${id}-aide` : undefined;
  const classes = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm";
  const etiquette = (
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
        {etiquette}
        {indication}
      </div>
    );
  }
  let controle;
  if (propriete.enum) {
    controle = (
      <select
        id={id}
        className={classes}
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
      </select>
    );
  } else if (propriete.type === "array") {
    controle = (
      <input
        id={id}
        className={classes}
        value={Array.isArray(valeur) ? valeur.join(", ") : ""}
        placeholder="comma separated"
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          const liste = event.target.value
            .split(",")
            .map((part) => part.trim())
            .filter(Boolean);
          onChange(liste.length > 0 ? liste : undefined);
        }}
      />
    );
  } else {
    const nombre = propriete.type === "integer" || propriete.type === "number";
    controle = (
      <input
        id={id}
        className={classes}
        type={nombre ? "number" : propriete.format === "date" ? "date" : propriete.format === "password" ? "password" : "text"}
        value={valeur === undefined || valeur === null ? "" : String(valeur)}
        required={requis}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          const brut = event.target.value;
          if (brut === "") return onChange(undefined);
          if (propriete.type === "integer") return onChange(Number.parseInt(brut, 10));
          if (propriete.type === "number") return onChange(Number(brut));
          return onChange(brut);
        }}
      />
    );
  }
  return (
    <div className="space-y-1">
      {etiquette}
      {controle}
      {indication}
    </div>
  );
}
