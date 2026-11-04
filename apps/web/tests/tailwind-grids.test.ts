// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

// Tailwind 4 copies the arbitrary value as it is: `grid-cols-[2fr,1fr]` becomes
// `grid-template-columns: 2fr,1fr`, which the browser rejects: the grid falls back to a single
// column and the side panel goes UNDER the editor (seen on dev, 2026-10-07). Tracks are separated
// by `_`; a comma is only allowed inside a function (`minmax(0,1fr)`, `repeat(...)`).

const ROOT = join(import.meta.dirname, "../src");
const GRID = /(?:[\w-]+:)*grid-(?:cols|rows)-\[([^\]]+)\]|\[grid-template-(?:columns|rows):([^\]]+)\]/g;

function files(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(tsx?|css)$/.test(name) ? [path] : [];
  });
}

/** What is left of a value once the parenthesised groups are removed, nested ones included. */
function outsideFunctions(value: string): string {
  let rest = value;
  for (let before = ""; before !== rest; ) {
    before = rest;
    rest = rest.replace(/\([^()]*\)/g, "");
  }
  return rest;
}

function commaGrids(source: string): string[] {
  return [...source.matchAll(GRID)]
    .filter((m) => outsideFunctions(m[1] ?? m[2] ?? "").includes(","))
    .map((m) => m[0]);
}

describe("Tailwind 4's arbitrary grids", () => {
  it("the detector recognises a comma between tracks, not inside a function", () => {
    expect(commaGrids('className="lg:grid-cols-[2fr,1fr]"')).toEqual(["lg:grid-cols-[2fr,1fr]"]);
    expect(commaGrids('className="[grid-template-columns:1fr,2fr]"')).toHaveLength(1);
    expect(commaGrids('className="lg:grid-cols-[2fr_1fr] md:grid-cols-[minmax(0,1fr)_17rem]"')).toEqual([]);
    expect(commaGrids('className="grid-cols-[repeat(auto-fill,minmax(12rem,1fr))]"')).toEqual([]);
  });

  it("no grid separates its tracks by a comma: Tailwind 4 makes it an invalid rule", () => {
    const faults = files(ROOT).flatMap((path) =>
      commaGrids(readFileSync(path, "utf8")).map((cls) => `${relative(ROOT, path)}: ${cls}`),
    );
    expect(faults).toEqual([]);
  });
});
