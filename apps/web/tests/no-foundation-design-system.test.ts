// @vitest-environment node
/**
 * The console owns its primitives (ADR 0048): the Varga Foundation design system is not a dependency
 * of `apps/web` — not a package, not a stylesheet, not a Tailwind `@source` — and eslint refuses an
 * import from it, in the code as in the fixtures and the tests.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css|mjs)$/.test(name) ? [path] : [];
  });
}

describe("the console without the foundation's design system", () => {
  it("package.json does not depend on it", () => {
    const pkg = JSON.parse(readFileSync(join(WEB, "package.json"), "utf8")) as Record<string, Record<string, string>>;
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    expect(Object.keys(deps).filter((name) => name.startsWith("@varga/"))).toEqual([]);
  });

  it("no source, stylesheet or config names it", () => {
    const files = [...sourceFiles(join(WEB, "src")), join(WEB, "next.config.mjs")];
    const offenders = files.filter((path) => /@varga\/|node_modules\/@varga/.test(readFileSync(path, "utf8")));
    expect(offenders.map((path) => relative(WEB, path))).toEqual([]);
  });

  it.each(["src/app/page.tsx", "src/mocks/data.ts", "tests/exemple.test.tsx"])(
    "eslint refuses an import from it in %s",
    async (file) => {
      const eslint = new ESLint({ cwd: WEB });
      const [result] = await eslint.lintText('import { Card } from "@varga/design-system";\nexport const C = Card;\n', {
        filePath: join(WEB, file),
      });
      const rules = result!.messages.map((m) => m.ruleId);
      expect(rules).toContain("no-restricted-imports");
    },
    30_000,
  );
});
