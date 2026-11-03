// @vitest-environment node
/**
 * A colour class that names no theme colour does nothing: Tailwind generates no rule for it, and the
 * border falls back to the text colour, the background to nothing. `border-l-waiting-solid` was one
 * (S24-04) — the solid tones are spelt `waiting`, `failed`… —: a wait's border was grey, and so was a
 * refusal's. The appearance tests checked the class's NAME, not that it exists. This one reads the
 * theme's colours (`--color-*` in `globals.css`) and refuses, in the console's code, any colour class
 * of one of their families that names none of them. Tailwind's colours (`red-500`…), sizes and widths
 * are none of its business.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const ROOT = join(WEB, "src");
// The console's colours are declared in its own stylesheet only (ADR 0048).
const SHEETS = [join(ROOT, "app/globals.css")];

function files(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

function themeColours(): Set<string> {
  const names = new Set<string>();
  for (const sheet of SHEETS) {
    for (const m of readFileSync(sheet, "utf8").matchAll(/--color-([a-z0-9-]+)\s*:/g)) names.add(m[1]!);
  }
  return names;
}

// `border-[xytrbl]` BEFORE `border`: otherwise `border-l-waiting-solid` would read as `border` +
// `l-waiting-solid`, of an unknown family `l`, and would pass.
const PROPERTIES = "bg|text|border-[xytrbl]|border|divide|outline|ring|fill|stroke|decoration|accent|caret|placeholder|from|via|to";
const CLASS = new RegExp(`(?<![\\w-])(?:${PROPERTIES})-([a-z][a-z0-9-]*)(?:/\\d+)?(?![\\w-])`, "g");

/** The colour classes of a source that aim at a theme family without naming one of its colours. */
export function unknownColours(source: string, names: Set<string>): string[] {
  const families = new Set([...names].map((name) => name.split("-")[0]!));
  return [...source.matchAll(CLASS)]
    .filter((m) => families.has(m[1]!.split("-")[0]!) && !names.has(m[1]!))
    .map((m) => m[0]);
}

describe("the console's colour classes", () => {
  it("the detector sees a colour unknown to a theme family, and nothing else", () => {
    const names = new Set(["waiting", "waiting-soft", "ink", "ink-muted", "line"]);
    const source =
      'className="border-l-waiting-solid hover:text-ink-muted bg-waiting-soft/50 text-xs border-l-2 bg-red-500 ' +
      'border-b-line/50 text-ink-mute"';
    expect(unknownColours(source, names)).toEqual(["border-l-waiting-solid", "text-ink-mute"]);
  });

  it("every colour class of the console names a theme colour", () => {
    const names = themeColours();
    expect(names.has("waiting")).toBe(true);
    const faults = files(ROOT).flatMap((path) =>
      unknownColours(readFileSync(path, "utf8"), names).map((cls) => `${relative(ROOT, path)}: ${cls}`),
    );
    expect(faults).toEqual([]);
  });
});
