// SPDX-License-Identifier: Apache-2.0
/** Des lignes en CSV (RFC 4180) : un champ qui porte une virgule, un guillemet ou un saut de ligne
 * est entouré de guillemets, et ses guillemets doublés. Une cellule qui commencerait par `=`, `+`,
 * `-` ou `@` est préfixée d'une apostrophe : un tableur l'exécuterait comme une formule. */
export function versCsv(colonnes: string[], lignes: Array<Array<unknown>>): string {
  const cellule = (valeur: unknown) => {
    let texte = valeur === undefined || valeur === null ? "" : typeof valeur === "object" ? JSON.stringify(valeur) : String(valeur);
    if (/^[=+\-@]/.test(texte)) texte = `'${texte}`;
    return /[",\n\r]/.test(texte) ? `"${texte.replaceAll('"', '""')}"` : texte;
  };
  return [colonnes, ...lignes].map((ligne) => ligne.map(cellule).join(",")).join("\r\n") + "\r\n";
}
