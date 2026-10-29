#!/usr/bin/env node
// Budget de bundle : ce que le navigateur télécharge ne grossit pas sans qu'on le voie.
// Lu après `next build` : la taille totale des chunks JS servis et celle du plus gros.
// Les seuils sont larges (le mesuré du 2026-09-25 : ~1,2 Mo au total) — ils attrapent
// une dépendance qu'on aurait embarquée par mégarde, pas dix kilo-octets.
// 2026-10-16 (S40 integration, batch 1): 1600 -> 1800 kB. The S39 base measured 1599 kB; S40 adds about
// 100 kB of console (vendored CC0 brand icons, memories, agent history and diff, project settings).
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const CHUNKS = join(process.cwd(), ".next", "static", "chunks");
// 2026-10-19 (merge of main 0.18.8): 1800 -> 1850 kB. Main stood at 1650 (S39-22's egress modal, S39-23's
// procedure panel); the union measures 1833 kB.
// 2026-10-20 (S41-22): 1850 -> 1875 kB. Main at ea273dd0 measures 1833 kB; the project's network form and
// its YAML splice (`@lezer/yaml`, already shipped with the policy editor, now in a shared chunk) bring 1854 kB.
// 2026-10-22 (S41-22 rebased on main cfc8a1dd): 1875 -> 1925 kB. S42-07's backends and executors panel
// takes main and S41-22 to 1879 kB; the S41 agent forms (S41-12) bring 1883.
// 2026-10-22 (S42 batch, on main after S41): 1925 -> 1950 kB. The S42 panels (About, instance identity,
// knowledge sources, rights and distillation, organisation branding) land on top of S41's agent forms.
// 2026-10-29 (S49: the workflow catalogue, the map's actors, workflow and transition-field panels): 1950 ->
// 2000 kB. Main at 615fcdfa measures 1939 kB; with S49, 1968 kB.
const TOTAL_MAX_KB = Number(process.env.BUNDLE_TOTAL_MAX_KB ?? 2000);
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
