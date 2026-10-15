/**
 * Writes `apps/api/src/choregos_api/theme_defaults.json`: the console's colour roles, resolved to
 * `#rrggbb`, for each mode, from `src/styles/theme.css` (S39-03, ADR 0064).
 *
 * The API validates a served theme against the console's contrast budget on the EFFECTIVE tokens
 * (these defaults, overridden by the theme), so the defaults must be the console's own — never a
 * second copy typed by hand. `tests/theme-defaults-drift.test.ts` fails when `theme.css` changes
 * without this script being run again: `pnpm theme:defaults`.
 */
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { themeDefaults } from "../tests/theme-tokens.ts";

const DESTINATION = resolve(import.meta.dirname, "../../api/src/choregos_api/theme_defaults.json");

writeFileSync(DESTINATION, `${JSON.stringify(themeDefaults(), null, 2)}\n`);
console.log(`wrote ${DESTINATION}`);
