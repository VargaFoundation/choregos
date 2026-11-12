#!/usr/bin/env node
// Bundle budget: what the browser downloads does not grow without anyone seeing it.
// Read after `next build`: the total size of the JS chunks served and the size of the biggest one.
// The thresholds are wide (measured on 2026-09-25: ~1.2 MB in total) — they catch a dependency
// bundled by mistake, not ten kilobytes.
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
// 2026-10-28 (S47-39, S47-40): 1950 -> 1975 kB. Main at 6fd7eac measures 1939 kB; the built-in agent card,
// the map's timing, group and activity lines and the project's agents table bring 1956.
// 2026-10-29 (S49: the workflow catalogue, the map's actors, workflow and transition-field panels): 1975 ->
// 2025 kB. Main with S47-39/40 measures 1956 kB; with S49 merged on it, 1998 kB.
// 2026-10-29 (S47-17): 2025 -> 2050 kB. The cross-backend review card and the memory re-import action add
// about 26 kB (1956 -> 1982 kB on main at 87ea1831), which would leave the S49 build under 1 kB of headroom.
// 2026-11-02 (#1179): 2050 -> 2075 kB. Main sits at the 2050 kB line; the tracker card's « Filed by » row
// and the regenerated API types tip the total over it by a fraction of a kilobyte.
// 2026-11-03 (S52-06b, on S52-05 and S52-06): 2075 -> 2100 kB. The decision step on the map (S52-06) and its
// calibration card with the demo mode's calibration (S52-06b) measure 2076 kB, 1 kB over the line.
// 2026-11-03 (S52-08): 2100 -> 2150 kB. #1237 put S55-08's icon tiles back, which S52-06b had taken out to fit
// under 2100: main measures 2125 kB, and the routing card's minimum confidence adds 1 kB.
// 2026-11-05 (S57-10): 2150 -> 2175 kB. The run's tool-scripts card, the spend by door and the tool-scripts
// policy card bring main to 2155 kB (CI of #1370), 5 kB over the line.
// 2026-11-06 (C1, D2C): 2175 -> 2200 kB. S58-03's repository settings bring main to the 2175 kB line; the
// received events' and the triggers' `reopened` label tip it over by a fraction of a kilobyte.
// 2026-11-09 (S61): 2200 -> 2225 kB. The typed artifacts' viewers (a diff, a CSV table, key/value pairs, a JSON
// tree, the facts of a ticket) take the console to 2205 kB, with no new dependency.
// 2026-11-09 (S61-18): 2225 -> 2250 kB. Reading docx, pptx and xlsx in the page, by hand and loaded on demand,
// brings 29 kB where a library would bring megabytes.
// 2026-11-10 (S59-14): 2250 -> 2275 kB. S60-03's agent Access tab and the knowledge base page's status banner,
// "who uses it" and source rows take the console to 2253 kB, with no new dependency.
// 2026-11-11 (S63, assistants): 2275 -> 2350 kB. The assistants' screens (catalogue, installation, conversation
// with its stream, memory page, tasks and delegations, the embedded component and its sign-in) take the console
// to 2339 kB, with no new dependency.
// 2026-11-11 (S63 waves E and F): 2350 -> 2375 kB. The memory export and owners' memory, the action's diff, the
// richer configuration forms and the embedded token bring 2350 kB (shared JSX already folded): no headroom left.
// 2026-11-11 (S62-11): 2375 -> 2400 kB. The A2A page of Integrations (four steps, the snippet builder and its
// name list), the Help entry and the "Learn more" links add 19 kB: 2369 kB with the assistants, no new
// dependency. S62-08's CLI page, written the same day, adds 6 kB more: the two together would sit at 2375.
// 2026-11-12 (S65-17a): 2400 -> 2425 kB. Main at cb4d94d4c measures 2383 kB; the Outposts pages (the list, and
// one outpost with its instances and their approval, the enrolment, the keys to pin and the recent calls) and
// their twelve client operations bring 29 kB: 2412 kB, with no new dependency.
// 2026-11-12 (S64-06, S64-07, S64-08): 2425 -> 2450 kB. S64 adds the Activity screen, the agent's stop and the work
// item's stop reason (15 kB), the tokens-and-cost panel (5 kB) and the run's Context card (4 kB): about 24 kB on top
// of main's 2412 kB, with no new dependency.
const TOTAL_MAX_KB = Number(process.env.BUNDLE_TOTAL_MAX_KB ?? 2450);
const CHUNK_MAX_KB = Number(process.env.BUNDLE_CHUNK_MAX_KB ?? 600);

function* files(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else if (entry.name.endsWith(".js")) yield path;
  }
}

let total = 0;
let biggest = { path: "", kb: 0 };
for (const path of files(CHUNKS)) {
  const kb = statSync(path).size / 1024;
  total += kb;
  if (kb > biggest.kb) biggest = { path, kb };
}
console.log(`JS chunks: ${total.toFixed(0)} kB in total (budget ${TOTAL_MAX_KB}), the biggest ${biggest.kb.toFixed(0)} kB (budget ${CHUNK_MAX_KB}) — ${biggest.path.replace(process.cwd() + "/", "")}`);
let failed = false;
if (total > TOTAL_MAX_KB) { console.error(`budget exceeded: ${total.toFixed(0)} kB > ${TOTAL_MAX_KB} kB`); failed = true; }
if (biggest.kb > CHUNK_MAX_KB) { console.error(`chunk too big: ${biggest.kb.toFixed(0)} kB > ${CHUNK_MAX_KB} kB`); failed = true; }
process.exit(failed ? 1 : 0);
