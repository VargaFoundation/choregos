// @vitest-environment node
/**
 * The organisation's catalogues are libraries (S55-15), as the project's workflow library is since
 * S55-04: "catalogue" read as French in the URL. MCP servers, connector types, plugins and the skills
 * one live under `library`; an old link, a bookmark or a document that still says `/catalogue/…` lands
 * on the same page with its sub-path and its query, for good (a permanent redirect).
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../next.config.mjs";
import { GROUPS, NAV } from "@/components/shell/navigation";
import { RESERVED_WORKFLOW_NAMES } from "@/lib/workflow-name";

const SRC = join(import.meta.dirname, "../src");
const APP = join(SRC, "app");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("the organisation's library routes", () => {
  it.each(["library/mcp-servers", "library/connectors", "library/plugins", "skills/library"])("%s is a page", (page) => {
    expect(existsSync(join(APP, page, "page.tsx"))).toBe(true);
  });

  it("leaves no page under a catalogue segment", () => {
    const left = files(APP)
      .map((path) => relative(APP, path))
      .filter((path) => path.split("/").includes("catalogue"));
    expect(left).toEqual([]);
  });

  it("redirects every old path for good, its sub-path kept (the query passes through)", async () => {
    const redirects = await config.redirects!();
    expect(redirects).toEqual(
      expect.arrayContaining([
        { source: "/catalogue/:path*", destination: "/library/:path*", permanent: true },
        { source: "/skills/catalogue/:path*", destination: "/skills/library/:path*", permanent: true },
        { source: "/admin/connectors/catalogue", destination: "/library/mcp-servers", permanent: true },
      ]),
    );
  });

  it("links nowhere through an old path: the sidebar, the pages and the components", () => {
    for (const href of GROUPS.flatMap((group) => group.entrees)) {
      expect(NAV.some((entry) => entry.href === href)).toBe(true);
      const page = href === "/" ? "page.tsx" : join(href.slice(1), "page.tsx");
      expect(existsSync(join(APP, page)), href).toBe(true);
    }
    const old = /["'`](\/catalogue\/|\/skills\/catalogue\b|\/admin\/connectors\/catalogue\b|\/p\/[^"'`]*\/workflows\/catalogue\b)/;
    const offenders = files(SRC).filter((path) => old.test(readFileSync(path, "utf8")));
    expect(offenders.map((path) => relative(SRC, path))).toEqual([]);
  });
});

describe("the names a workflow may not take (S55-15)", () => {
  it("reserves every console page and every redirect under a project's workflows", async () => {
    const workflows = join(APP, "p/[slug]/workflows");
    const pages = readdirSync(workflows).filter((name) => !name.startsWith("[") && statSync(join(workflows, name)).isDirectory());
    const redirected = (await config.redirects!())
      .map((r) => /^\/p\/:slug\/workflows\/([a-z-]+)$/.exec(r.source)?.[1])
      .filter((name): name is string => !!name);
    expect(pages.length).toBeGreaterThan(0);
    expect([...RESERVED_WORKFLOW_NAMES].sort()).toEqual([...new Set([...pages, ...redirected])].sort());
  });

  it("is the list the API refuses", () => {
    const api = readFileSync(join(import.meta.dirname, "../../api/src/choregos_api/services/definitions.py"), "utf8");
    const declared = /RESERVED_WORKFLOW_NAMES = frozenset\(\{([^}]*)\}\)/.exec(api)?.[1];
    expect(declared?.match(/"[a-z-]+"/g)?.map((n) => n.slice(1, -1)).sort()).toEqual([...RESERVED_WORKFLOW_NAMES].sort());
  });
});
