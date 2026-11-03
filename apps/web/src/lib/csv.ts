// SPDX-License-Identifier: Apache-2.0
/** Rows as CSV (RFC 4180): a field that carries a comma, a quote or a line break is wrapped in
 * quotes, and its quotes doubled. A cell that would start with `=`, `+`, `-` or `@` is prefixed
 * with an apostrophe: a spreadsheet would run it as a formula. */
export function versCsv(colonnes: string[], lignes: Array<Array<unknown>>): string {
  const cellule = (valeur: unknown) => {
    let text = valeur === undefined || valeur === null ? "" : typeof valeur === "object" ? JSON.stringify(valeur) : String(valeur);
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [colonnes, ...lignes].map((ligne) => ligne.map(cellule).join(",")).join("\r\n") + "\r\n";
}
