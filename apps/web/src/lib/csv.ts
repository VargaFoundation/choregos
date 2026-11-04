// SPDX-License-Identifier: Apache-2.0
/** Rows as CSV (RFC 4180): a field that carries a comma, a quote or a line break is wrapped in
 * quotes, and its quotes doubled. A cell that would start with `=`, `+`, `-` or `@` is prefixed
 * with an apostrophe: a spreadsheet would run it as a formula. */
export function versCsv(colonnes: string[], rows: Array<Array<unknown>>): string {
  const cellule = (value: unknown) => {
    let text = value === undefined || value === null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [colonnes, ...rows].map((row) => row.map(cellule).join(",")).join("\r\n") + "\r\n";
}
