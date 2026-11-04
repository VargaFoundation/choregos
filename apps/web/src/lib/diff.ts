// SPDX-License-Identifier: Apache-2.0
/**
 * The diff of two texts, line by line: enough to review two versions of a workflow.
 *
 * A longest common subsequence, by dynamic programming: a workflow has a few hundred lines, the
 * table fits in memory, and no dependency enters the bundle.
 */
export type DiffLine = { kind: "same" | "add" | "del"; text: string; before?: number; after?: number };

export function diffLines(avant: string, after: string): DiffLine[] {
  const a = avant.split("\n");
  const b = after.split("\n");
  const largeur = b.length + 1;
  // table[i * largeur + j]: the longest common subsequence of a[i:] and b[j:]
  const table = new Int32Array((a.length + 1) * largeur);
  const lcs = (i: number, j: number) => table[i * largeur + j] ?? 0;
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * largeur + j] = a[i] === b[j] ? lcs(i + 1, j + 1) + 1 : Math.max(lcs(i + 1, j), lcs(i, j + 1));
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ kind: "same", text: a[i] ?? "", before: i + 1, after: j + 1 });
      i += 1;
      j += 1;
    } else if (lcs(i + 1, j) >= lcs(i, j + 1)) {
      lines.push({ kind: "del", text: a[i] ?? "", before: i + 1 });
      i += 1;
    } else {
      lines.push({ kind: "add", text: b[j] ?? "", after: j + 1 });
      j += 1;
    }
  }
  for (; i < a.length; i += 1) lines.push({ kind: "del", text: a[i] ?? "", before: i + 1 });
  for (; j < b.length; j += 1) lines.push({ kind: "add", text: b[j] ?? "", after: j + 1 });
  return lines;
}

/** Only the lines that change, with `contexte` unchanged lines around them, like `diff -u`. */
export function hunks(lines: DiffLine[], context = 2): DiffLine[][] {
  const garder = lines.map((line, index) =>
    lines.slice(Math.max(0, index - context), index + context + 1).some((voisine) => voisine.kind !== "same"),
  );
  const morceaux: DiffLine[][] = [];
  let courant: DiffLine[] = [];
  lines.forEach((line, index) => {
    if (garder[index]) {
      courant.push(line);
    } else if (courant.length > 0) {
      morceaux.push(courant);
      courant = [];
    }
  });
  if (courant.length > 0) morceaux.push(courant);
  return morceaux;
}
