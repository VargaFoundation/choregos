// SPDX-License-Identifier: Apache-2.0
/**
 * Les champs d'une demande, lus dans `metadata.inputs` de son workflow — un JSON Schema (ADR 0031,
 * S16-04). Le formulaire « new request » s'en déduit ; l'API valide ce qu'il envoie, et reste juge.
 */
export type Champ = {
  name: string;
  label: string;
  hint?: string;
  required: boolean;
  control: "text" | "date" | "number" | "integer" | "boolean" | "choice" | "list";
  choices?: string[];
};

type Propriete = {
  type?: string;
  format?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  items?: { type?: string };
};

export function champsDepuisSchema(schema: Record<string, unknown> | undefined): Champ[] {
  const proprietes = (schema?.properties ?? {}) as Record<string, Propriete>;
  const requis = new Set(Array.isArray(schema?.required) ? (schema.required as string[]) : []);
  return Object.entries(proprietes).map(([name, propriete]) => {
    const control: Champ["control"] = propriete.enum
      ? "choice"
      : propriete.type === "boolean"
        ? "boolean"
        : propriete.type === "integer"
          ? "integer"
          : propriete.type === "number"
            ? "number"
            : propriete.type === "array"
              ? "list"
              : propriete.format === "date"
                ? "date"
                : "text";
    return {
      name,
      label: propriete.title ?? name.replaceAll("_", " "),
      hint: propriete.description,
      required: requis.has(name),
      control,
      choices: propriete.enum?.map(String),
    };
  });
}

/** Ce qu'on a saisi, typé comme le schéma l'attend ; un champ vide n'est pas envoyé. */
export function valeursPourLApi(champs: Champ[], saisies: Record<string, string | boolean>): Record<string, unknown> {
  const valeurs: Record<string, unknown> = {};
  for (const champ of champs) {
    const saisie = saisies[champ.name];
    if (saisie === undefined || saisie === "") continue;
    if (champ.control === "boolean") valeurs[champ.name] = Boolean(saisie);
    else if (champ.control === "integer") valeurs[champ.name] = Number.parseInt(String(saisie), 10);
    else if (champ.control === "number") valeurs[champ.name] = Number(saisie);
    else if (champ.control === "list")
      valeurs[champ.name] = String(saisie)
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    else valeurs[champ.name] = String(saisie);
  }
  return valeurs;
}
