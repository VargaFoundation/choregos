// SPDX-License-Identifier: Apache-2.0
"use client";

import { useId, useState } from "react";
import { GitHubRepoField } from "@/components/github-repo-field";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { safeAsset } from "@/lib/served-theme";

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
  /** `format: "asset"` (S42-13): where the chosen file is POSTed, what it may be, and how large. */
  "x-upload"?: string;
  "x-accept"?: string[];
  "x-max-bytes"?: number;
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
  org,
}: {
  schema: JsonSchema;
  value: Valeurs;
  onChange: (value: Valeurs) => void;
  disabled?: boolean;
  /** Fills `{org}` in an asset field's `x-upload`; without it such a field cannot upload. */
  org?: string;
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
          org={org}
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
  org,
  onChange,
}: {
  id: string;
  nom: string;
  propriete: JsonSchema;
  requis: boolean;
  valeur: unknown;
  disabled?: boolean;
  org?: string;
  onChange: (valeur: unknown) => void;
}) {
  const libelle = propriete.title ?? nom.replaceAll("_", " ");
  const aide = propriete.description ? `${id}-aide` : undefined;
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
    controle = (
      <GitHubRepoField
        id={id}
        value={typeof valeur === "string" ? valeur : ""}
        disabled={disabled}
        describedBy={aide}
        onChange={onChange}
      />
    );
  } else if (propriete.format === "asset") {
    controle = (
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
  } else if (propriete.type === "object") {
    controle = <Correspondance id={id} valeur={valeur} disabled={disabled} aide={aide} onChange={onChange} />;
  } else if (propriete.format === "multiline") {
    controle = (
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
    controle = (
      <Input
        id={id}
        value={Array.isArray(valeur) ? valeur.join(", ") : ""}
        placeholder="Comma separated"
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
      <Input
        id={id}
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
          const { valeurs } = lireUneCorrespondance(event.target.value);
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
export function cheminDeTeleversement(modele: string | undefined, org: string | undefined): string | null {
  if (!modele) return null;
  if (modele.includes("{org}")) {
    if (!org) return null;
    modele = modele.replaceAll("{org}", encodeURIComponent(org));
  }
  return /[{}]/.test(modele) ? null : modele;
}

/** Why a file cannot be offered, from the schema's limits, or null. A courtesy: the upload route is the judge. */
export function refusDuFichier(fichier: { type: string; size: number }, propriete: JsonSchema): string | null {
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
  const chemin = cheminDeTeleversement(propriete["x-upload"], org);
  const actuel = typeof valeur === "string" && valeur !== "" ? valeur : null;
  // Only a same-origin served file is drawn; any other reference is shown as text, never loaded.
  const image = actuel ? safeAsset(actuel) : undefined;

  async function choisir(fichier: File | undefined) {
    setErreur(null);
    if (!fichier || !chemin) return;
    const refus = refusDuFichier(fichier, propriete);
    if (refus) return setErreur(refus);
    setEnvoi(true);
    try {
      const { ref } = await api.sectionUpload(chemin, fichier);
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
          disabled={disabled || envoi || !chemin}
          aria-describedby={describedBy}
          className="text-xs"
          // The same file chosen twice must fire again.
          onChange={(event) => {
            const fichier = event.target.files?.[0];
            event.target.value = "";
            void choisir(fichier);
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
