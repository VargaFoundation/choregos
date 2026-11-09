// SPDX-License-Identifier: Apache-2.0
/** A workbook as tables of text. Values only: a formula is shown by its last computed result. */
import { attribute, child, children, part, relationIdOf, relationshipsOf } from "./xml";
import type { Zip } from "./zip";

export const MAX_ROWS = 500;
export const MAX_COLUMNS = 60;
const MAX_SHEETS = 50;

export type Sheet = { name: string; rows: string[][]; truncated: boolean };
export type Workbook = { kind: "xlsx"; sheets: Sheet[]; urls: string[] };

function columnOf(reference: string | null): number {
  const letters = /^[A-Z]+/.exec(reference ?? "")?.[0] ?? "";
  return [...letters].reduce((total, letter) => total * 26 + letter.charCodeAt(0) - 64, 0) - 1;
}

function textOf(node: Element | null): string {
  if (!node) return "";
  return Array.from(node.getElementsByTagName("*"))
    .filter((element) => element.localName === "t")
    .map((element) => element.textContent ?? "")
    .join("");
}

function valueOf(cell: Element, shared: string[]): string {
  const type = attribute(cell, "t");
  const raw = child(cell, "v")?.textContent ?? "";
  if (type === "s") return shared[Number(raw)] ?? "";
  if (type === "inlineStr") return textOf(child(cell, "is"));
  if (type === "b") return raw === "1" ? "TRUE" : "FALSE";
  return raw;
}

export async function readWorkbook(zip: Zip): Promise<Workbook> {
  const path = "xl/workbook.xml";
  const [workbook, relations, strings] = await Promise.all([
    part(zip, path),
    relationshipsOf(zip, path),
    part(zip, "xl/sharedStrings.xml"),
  ]);
  const shared = children(strings, "si").map((item) => textOf(item));
  const sheets: Sheet[] = [];
  for (const sheet of children(child(workbook, "sheets"), "sheet").slice(0, MAX_SHEETS)) {
    const target = relations.get(relationIdOf(sheet));
    const root = target && !target.external ? await part(zip, target.target) : null;
    const allRows = children(child(root, "sheetData"), "row");
    let truncated = allRows.length > MAX_ROWS;
    const rows = allRows.slice(0, MAX_ROWS).map((row) => {
      const cells: string[] = [];
      for (const cell of children(row, "c")) {
        const column = columnOf(attribute(cell, "r"));
        if (column >= MAX_COLUMNS) {
          truncated = true;
          continue;
        }
        const at = column >= 0 ? column : cells.length;
        while (cells.length < at) cells.push("");
        cells[at] = valueOf(cell, shared);
      }
      return cells;
    });
    sheets.push({ name: attribute(sheet, "name") ?? `Sheet ${sheets.length + 1}`, rows, truncated });
  }
  return { kind: "xlsx", sheets, urls: [] };
}
