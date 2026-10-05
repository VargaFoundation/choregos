// SPDX-License-Identifier: Apache-2.0
/**
 * Le YAML d'un gabarit, renommé : `metadata.name` est le nom du workflow dans le projet, et l'API
 * refuse un YAML dont le nom n'est pas celui de la route (ADR 0031). Le reste du texte ne bouge pas.
 */
export function renommer(yaml: string, nom: string): string {
  return yaml.replace(/(metadata:\s*\{?\s*name:\s*)[^,\s}]+/, `$1${nom}`);
}
