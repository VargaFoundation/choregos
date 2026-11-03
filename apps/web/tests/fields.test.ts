// @vitest-environment node
/**
 * No raw input field outside `components/ui` (S24-04, ADR 0043).
 *
 * An `<input>`, a `<select>` or a `<textarea>` written in a page carried its own styling:
 * rounded here, square there, 28 or 36 px high, an ink focus, or nothing at all (the browser's
 * rendering). 78 fields, as many variants. They go through `Input`, `Select` and `Textarea` of
 * `components/ui`, which carry the console's `CHAMP`. The controls that are not input fields stay
 * raw: checkbox, radio button, file, slider.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const ROOT = join(import.meta.dirname, "../src");
const ALLOWED = new Set(["checkbox", "radio", "file", "range", "hidden", "color"]);
const TAGS = new Set(["input", "select", "textarea"]);

function files(folder: string): string[] {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (statSync(path).isDirectory()) return files(path);
    return path.endsWith(".tsx") ? [path] : [];
  });
}

/** The raw input fields of a TSX source: `file:line <tag>`. */
export function rawFields(name: string, text: string): string[] {
  const source = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tag = node.tagName.getText(source);
      if (TAGS.has(tag)) {
        const attribute = node.attributes.properties.find(
          (a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText(source) === "type",
        );
        const type = attribute?.initializer && ts.isStringLiteral(attribute.initializer) ? attribute.initializer.text : undefined;
        if (!(tag === "input" && type && ALLOWED.has(type))) {
          const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
          found.push(`${name}:${line + 1} <${tag}${type ? ` type="${type}"` : ""}>`);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe("the console's input fields", () => {
  it("all go through Input, Select or Textarea of components/ui", () => {
    const raw = files(ROOT)
      .filter((path) => !path.endsWith(join("components", "ui.tsx")))
      .flatMap((path) => rawFields(relative(ROOT, path), readFileSync(path, "utf8")));
    expect(raw).toEqual([]);
  });

  it("the guard sees a raw field, and lets a checkbox through (it does not pass vacuously)", () => {
    const source = `export const A = () => (<form>
      <input value="x" className="rounded border" />
      <select><option>a</option></select>
      <textarea />
      <input type={secret ? "password" : "text"} />
      <input type="checkbox" />
      <input type="file" />
    </form>);`;
    expect(rawFields("a.tsx", source)).toEqual([
      "a.tsx:2 <input>",
      "a.tsx:3 <select>",
      "a.tsx:4 <textarea>",
      "a.tsx:5 <input>",
    ]);
  });
});

describe("the console's legends", () => {
  it("go through LEGENDE: no CSS capitals written by hand (`uppercase tracking-wide`)", () => {
    const written = files(ROOT)
      .filter((path) => !path.endsWith(join("components", "ui.tsx")))
      .flatMap((path) =>
        readFileSync(path, "utf8")
          .split("\n")
          .flatMap((line, i) => (/\buppercase\b[^"]*\btracking-wide\b|\btracking-wide\b[^"]*\buppercase\b/.test(line) ? [`${relative(ROOT, path)}:${i + 1}`] : [])),
      );
    expect(written).toEqual([]);
  });
});
