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

function fichiers(dossier: string): string[] {
  return readdirSync(dossier).flatMap((nom) => {
    const chemin = join(dossier, nom);
    if (statSync(chemin).isDirectory()) return fichiers(chemin);
    return /\.(tsx?|css|mjs)$/.test(nom) ? [chemin] : [];
  });
}

describe("the console without the foundation's design system", () => {
  it("package.json does not depend on it", () => {
    const paquet = JSON.parse(readFileSync(join(WEB, "package.json"), "utf8")) as Record<string, Record<string, string>>;
    const dependances = { ...paquet.dependencies, ...paquet.devDependencies };
    expect(Object.keys(dependances).filter((nom) => nom.startsWith("@varga/"))).toEqual([]);
  });

  it("no source, stylesheet or config names it", () => {
    const sources = [...fichiers(join(WEB, "src")), join(WEB, "next.config.mjs")];
    const fautes = sources.filter((chemin) => /@varga\/|node_modules\/@varga/.test(readFileSync(chemin, "utf8")));
    expect(fautes.map((chemin) => relative(WEB, chemin))).toEqual([]);
  });

  it.each(["src/app/page.tsx", "src/mocks/data.ts", "tests/exemple.test.tsx"])(
    "eslint refuses an import from it in %s",
    async (fichier) => {
      const eslint = new ESLint({ cwd: WEB });
      const [resultat] = await eslint.lintText('import { Card } from "@varga/design-system";\nexport const C = Card;\n', {
        filePath: join(WEB, fichier),
      });
      const regles = resultat!.messages.map((m) => m.ruleId);
      expect(regles).toContain("no-restricted-imports");
    },
    30_000,
  );
});
