// SPDX-License-Identifier: Apache-2.0
/**
 * A CSV file as rows (RFC 4180: quoted fields, doubled quotes, newlines in quotes). Parsed in the
 * browser into plain strings and drawn as text; a formula in a cell stays a piece of text.
 */

export type ParsedCsv = { header: string[]; rows: string[][]; truncated: boolean; total: number };

function delimiterOf(line: string): string {
  const candidates = [",", ";", "\t"];
  const counts = candidates.map((c) => line.split(c).length);
  return candidates[counts.indexOf(Math.max(...counts))] ?? ",";
}

function parseRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i]!;
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (c === '"') {
        quoted = false;
      } else {
        field += c;
      }
    } else if (c === '"') {
      quoted = true;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export function parseCsv(text: string, maxRows: number): ParsedCsv {
  const source = text.replace(/^﻿/, "");
  const firstLine = source.split(/\r?\n/, 1)[0] ?? "";
  const all = parseRows(source, delimiterOf(firstLine)).filter((row) => row.some((cell) => cell !== ""));
  const [header = [], ...body] = all;
  return { header, rows: body.slice(0, maxRows), truncated: body.length > maxRows, total: body.length };
}
