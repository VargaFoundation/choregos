// @vitest-environment node
/**
 * The API validates a served theme against the console's contrast budget on top of the console's
 * own colours (`theme_defaults.json`, S39-03). If someone edits `theme.css` and forgets
 * `pnpm theme:defaults`, the API would judge a theme against colours the console no longer has.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { themeDefaults } from "./theme-tokens";

const COMMITTED = resolve(import.meta.dirname, "../../api/src/choregos_api/theme_defaults.json");

it("theme_defaults.json is what theme.css resolves to (run `pnpm theme:defaults`)", () => {
  const committed = JSON.parse(readFileSync(COMMITTED, "utf-8"));
  expect(committed).toEqual(themeDefaults());
});

it("every themable role is a colour of both modes, 52 of them", () => {
  const { roles, dark, light } = themeDefaults();
  expect(roles).toHaveLength(52);
  for (const role of roles) {
    expect(dark[role], role).toMatch(/^#[0-9a-f]{6}$/);
    expect(light[role], role).toMatch(/^#[0-9a-f]{6}$/);
  }
});
