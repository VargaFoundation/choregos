#!/usr/bin/env node
// Budget de bundle : ce que le navigateur télécharge ne grossit pas sans qu'on le voie.
// Lu après `next build` : la taille totale des chunks JS servis et celle du plus gros.
// Les seuils sont larges (le mesuré du 2026-09-25 : ~1,2 Mo au total) — ils attrapent
// une dépendance qu'on aurait embarquée par mégarde, pas dix kilo-octets.
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const CHUNKS = join(process.cwd(), ".next", "static", "chunks");
const TOTAL_MAX_KB = Number(process.env.BUNDLE_TOTAL_MAX_KB ?? 1600);
const CHUNK_MAX_KB = Number(process.env.BUNDLE_CHUNK_MAX_KB ?? 600);

function* fichiers(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const chemin = join(dir, entry.name);
    if (entry.isDirectory()) yield* fichiers(chemin);
    else if (entry.name.endsWith(".js")) yield chemin;
  }
}

let total = 0;
let plusGros = { chemin: "", kb: 0 };
for (const chemin of fichiers(CHUNKS)) {
  const kb = statSync(chemin).size / 1024;
  total += kb;
  if (kb > plusGros.kb) plusGros = { chemin, kb };
}
console.log(`chunks JS : ${total.toFixed(0)} kB au total (budget ${TOTAL_MAX_KB}), le plus gros ${plusGros.kb.toFixed(0)} kB (budget ${CHUNK_MAX_KB}) — ${plusGros.chemin.replace(process.cwd() + "/", "")}`);
let ko = false;
if (total > TOTAL_MAX_KB) { console.error(`budget dépassé : ${total.toFixed(0)} kB > ${TOTAL_MAX_KB} kB`); ko = true; }
if (plusGros.kb > CHUNK_MAX_KB) { console.error(`chunk trop gros : ${plusGros.kb.toFixed(0)} kB > ${CHUNK_MAX_KB} kB`); ko = true; }
process.exit(ko ? 1 : 0);
