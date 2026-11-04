// @vitest-environment node
/**
 * Where the console's colours come from (ADR 0043): each one is a step of the Radix Colors scales
 * (MIT) — `--slate-3` is exactly step 3 of `slate` (light) or of `slateDark` (dark) — or
 * `#fff` / `#000`. No other value gets in: an eyeballed tint cannot slip in, and the look can
 * be rebuilt from a public source, borrowing nothing from anywhere else.
 *
 * And every token the console reads (`var(--choregos-…)`, in its code and its stylesheets) is declared
 * by its theme: the console owns its tokens (ADR 0048), no other stylesheet supplies one, and a role
 * read but never declared would silently fall back to nothing.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import * as radix from "@radix-ui/colors";
import { describe, expect, it } from "vitest";
import { declarationsOf, THEME_SHEET } from "./tokens";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

const SCALES = radix as unknown as Record<string, Record<string, string>>;
const ALLOWED = new Set(["#fff", "#000"]);
const BLOCKS = [
  { selector: ":root", suffix: "Dark" },
  { selector: ':root[data-theme="light"]', suffix: "" },
] as const;

describe("the provenance of the console's colours", () => {
  it.each(BLOCKS)("each named step of $selector is the Radix step of the same name", ({ selector, suffix }) => {
    const steps = [...declarationsOf(THEME_SHEET, selector)].filter(([name]) => /^--[a-z]+-\d+$/.test(name));
    expect(steps.length, "no Radix step declared").toBeGreaterThan(10);
    for (const [name, value] of steps) {
      const [, hue, rank] = /^--([a-z]+)-(\d+)$/.exec(name)!;
      const scale = SCALES[`${hue}${suffix}`];
      expect(scale, `unknown Radix scale: ${hue}${suffix}`).toBeDefined();
      expect(value.toLowerCase(), `${name} in ${selector}`).toBe(scale![`${hue}${rank}`]);
    }
  });

  it.each(BLOCKS)("no colour literal other than #fff and #000 in the roles of $selector", ({ selector }) => {
    for (const [name, value] of declarationsOf(THEME_SHEET, selector)) {
      if (/^--[a-z]+-\d+$/.test(name)) continue;
      for (const literal of value.match(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)|oklch\([^)]*\)/g) ?? []) {
        expect(ALLOWED.has(literal.toLowerCase()), `${name}: ${literal}`).toBe(true);
      }
    }
  });

  it("every token the console reads is declared by its theme", () => {
    const declared = new Set(declarationsOf(THEME_SHEET, ":root").keys());
    // A card or the sidebar redefines the surface for what it holds: still a declared token.
    const read = new Set<string>();
    for (const path of sourceFiles(join(import.meta.dirname, "../src"))) {
      for (const m of readFileSync(path, "utf8").matchAll(/var\((--choregos-[a-z0-9-]+)/g)) read.add(m[1]!);
    }
    expect(read.size, "no token read: the test checks nothing").toBeGreaterThan(20);
    expect([...read].filter((name) => !declared.has(name)).sort()).toEqual([]);
  });

  it("no stylesheet of the console declares a colour role outside its theme", () => {
    const elsewhere = sourceFiles(join(import.meta.dirname, "../src"))
      .filter((path) => path.endsWith(".css") && path !== THEME_SHEET)
      .flatMap((path) => [...readFileSync(path, "utf8").matchAll(/^\s*(--(?:choregos|varga)-[a-z0-9-]+)\s*:/gm)].map((m) => m[1]!));
    expect(elsewhere).toEqual([]);
  });
});
