// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId, useState } from "react";

/**
 * Un formulaire tiré d'un JSON Schema — celui d'une section d'administration déclarée par un
 * greffon (ADR 0032), demain celui d'un connecteur (ADR 0034). Fait ici plutôt qu'avec `@rjsf` :
 * le budget du bundle, et les seuls cas qui servent — un objet de scalaires, de choix, de listes,
 * de textes sur plusieurs lignes (`format: "multiline"` : un certificat PEM) et de correspondances
 * (`type: "object"` de chaînes : un groupe de l'IdP → un groupe de rôle), une par ligne.
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
  additionalProperties?: JsonSchema | boolean;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
};

type Valeurs = Record<string, unknown>;

/** Les champs obligatoires laissés vides : de quoi désactiver « enregistrer » avant l'API. */
export function champsManquants(schema: JsonSchema, valeurs: Valeurs): string[] {
  return (schema.required ?? []).filter((nom) => {
    const valeur = valeurs[nom];
    if (valeur === undefined || valeur === null || valeur === "") return true;
    if (Array.isArray(valeur)) return valeur.length === 0;
    return typeof valeur === "object" && Object.keys(valeur).length === 0;
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
  } else if (propriete.type === "object") {
    controle = <Correspondance id={id} valeur={valeur} disabled={disabled} aide={aide} onChange={onChange} />;
  } else if (propriete.format === "multiline") {
    controle = (
      <textarea
        id={id}
        className={`${classes} font-mono text-xs`}
        rows={6}
        value={typeof valeur === "string" ? valeur : ""}
        required={requis}
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      />
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

/** `clé = valeur`, une par ligne : ce qu'on lit, et ce qu'on tape. */
export function lireUneCorrespondance(texte: string): { valeurs: Record<string, string>; illisibles: number[] } {
  const valeurs: Record<string, string> = {};
  const illisibles: number[] = [];
  texte.split("\n").forEach((ligne, index) => {
    if (!ligne.trim()) return;
    const egal = ligne.indexOf("=");
    const cle = egal < 0 ? "" : ligne.slice(0, egal).trim();
    if (!cle) {
      illisibles.push(index + 1);
      return;
    }
    valeurs[cle] = ligne.slice(egal + 1).trim();
  });
  return { valeurs, illisibles };
}

function ecrireUneCorrespondance(valeur: unknown): string {
  if (!valeur || typeof valeur !== "object" || Array.isArray(valeur)) return "";
  return Object.entries(valeur as Record<string, unknown>)
    .map(([cle, contenu]) => `${cle} = ${String(contenu)}`)
    .join("\n");
}

/**
 * Une correspondance, une ligne par paire. Le texte tapé reste tel quel pendant la saisie — une
 * ligne illisible est dite, jamais effacée — et suit la valeur quand elle change d'ailleurs (la
 * lecture du formulaire arrive après son premier rendu).
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
  const [texte, setTexte] = useState(() => ecrireUneCorrespondance(valeur));
  const [recue, setRecue] = useState(valeur);
  if (valeur !== recue) {
    // Une valeur venue d'ailleurs (la lecture) remplace le texte ; celle qu'on vient de taper, non.
    setRecue(valeur);
    const tapee = lireUneCorrespondance(texte).valeurs;
    if (JSON.stringify(tapee) !== JSON.stringify(valeur ?? {})) setTexte(ecrireUneCorrespondance(valeur));
  }
  const { illisibles } = lireUneCorrespondance(texte);
  return (
    <>
      <textarea
        id={id}
        className="w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs"
        rows={4}
        value={texte}
        placeholder="key = value"
        disabled={disabled}
        aria-describedby={aide}
        onChange={(event) => {
          setTexte(event.target.value);
          const { valeurs } = lireUneCorrespondance(event.target.value);
          onChange(Object.keys(valeurs).length > 0 ? valeurs : undefined);
        }}
      />
      {illisibles.length > 0 && (
        <p className="text-xs text-danger" role="alert">
          line{illisibles.length > 1 ? "s" : ""} {illisibles.join(", ")}: key = value expected
        </p>
      )}
    </>
  );
}
