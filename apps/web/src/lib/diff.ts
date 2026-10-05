// SPDX-License-Identifier: Apache-2.0
/**
 * Le diff de deux textes, ligne à ligne — de quoi relire deux versions d'un workflow.
 *
 * Une plus longue sous-suite commune, en programmation dynamique : un workflow compte quelques
 * centaines de lignes, la table tient en mémoire, et aucune dépendance n'entre dans le bundle.
 */
export type LigneDeDiff = { kind: "same" | "add" | "del"; text: string; before?: number; after?: number };

export function diffLines(avant: string, apres: string): LigneDeDiff[] {
  const a = avant.split("\n");
  const b = apres.split("\n");
  const largeur = b.length + 1;
  // table[i * largeur + j] : la plus longue sous-suite commune de a[i:] et b[j:]
  const table = new Int32Array((a.length + 1) * largeur);
  const lcs = (i: number, j: number) => table[i * largeur + j] ?? 0;
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i * largeur + j] = a[i] === b[j] ? lcs(i + 1, j + 1) + 1 : Math.max(lcs(i + 1, j), lcs(i, j + 1));
    }
  }
  const lignes: LigneDeDiff[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lignes.push({ kind: "same", text: a[i] ?? "", before: i + 1, after: j + 1 });
      i += 1;
      j += 1;
    } else if (lcs(i + 1, j) >= lcs(i, j + 1)) {
      lignes.push({ kind: "del", text: a[i] ?? "", before: i + 1 });
      i += 1;
    } else {
      lignes.push({ kind: "add", text: b[j] ?? "", after: j + 1 });
      j += 1;
    }
  }
  for (; i < a.length; i += 1) lignes.push({ kind: "del", text: a[i] ?? "", before: i + 1 });
  for (; j < b.length; j += 1) lignes.push({ kind: "add", text: b[j] ?? "", after: j + 1 });
  return lignes;
}

/** Les seules lignes qui changent, avec `contexte` lignes inchangées autour — comme `diff -u`. */
export function hunks(lignes: LigneDeDiff[], contexte = 2): LigneDeDiff[][] {
  const garder = lignes.map((ligne, index) =>
    lignes.slice(Math.max(0, index - contexte), index + contexte + 1).some((voisine) => voisine.kind !== "same"),
  );
  const morceaux: LigneDeDiff[][] = [];
  let courant: LigneDeDiff[] = [];
  lignes.forEach((ligne, index) => {
    if (garder[index]) {
      courant.push(ligne);
    } else if (courant.length > 0) {
      morceaux.push(courant);
      courant = [];
    }
  });
  if (courant.length > 0) morceaux.push(courant);
  return morceaux;
}
