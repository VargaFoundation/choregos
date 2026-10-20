import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import procedures from "@/mocks/procedures.json";

// The console speaks to the people who use the platform: an ADR number or a story id is the
// developers' bookkeeping, not something a project owner can act on (S41-22: the project's
// network section read "under the organisation's ceiling (ADR 0070)"). Comments keep them; every
// string and JSX text the console can render may not carry them.
const SRC = resolve(import.meta.dirname, "../src");
const INTERNAL = /\bADR[ -]?\d{3,4}\b|\bS\d{2}-\d{2}[a-z]?\b/;

function* sources(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* sources(path);
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) yield path;
  }
}

/** Each piece of text a file can put on screen: string literals, template parts and JSX text. Comments are not nodes. */
function renderedTexts(file: string, text: string): string[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteralLike(node) || ts.isJsxText(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      found.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe("the console shows no ADR number nor story id", () => {
  it("the scan reads strings and JSX text, never comments", () => {
    const texts = renderedTexts(
      "x.tsx",
      `// ADR 0070\n/** S41-22 */\nconst a = "under the ceiling (ADR 0070)";\nconst b = <p lead={\`s \${a} S39-22\`}>Hosts {/* ADR 0044 */} ADR-0007</p>;`,
    );
    const flagged = texts.filter((t) => INTERNAL.test(t));
    expect(flagged).toEqual(["under the ceiling (ADR 0070)", " S39-22", " ADR-0007"]);
  });

  it("in any source the console renders", () => {
    const faults: string[] = [];
    for (const file of sources(SRC)) {
      for (const text of renderedTexts(file, readFileSync(file, "utf-8"))) {
        if (INTERNAL.test(text)) faults.push(`${relative(SRC, file)}: ${text.trim().slice(0, 100)}`);
      }
    }
    expect(faults).toEqual([]);
  });

  it("in the setup procedures the console opens", () => {
    const faults = JSON.stringify(procedures).match(new RegExp(INTERNAL.source, "g")) ?? [];
    expect(faults).toEqual([]);
  });
});
