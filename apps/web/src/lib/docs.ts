// SPDX-License-Identifier: Apache-2.0
/**
 * The public user documentation (S62-11), as data: the pages it has, and which one each screen of the
 * console points to.
 *
 * The base URL is the instance's (`docs_url` in `GET /edition`, from `CHOREGOS_DOCS_URL`), so an
 * air-gapped install points the links at its own copy. The slugs are fixed by the S62 shared contract:
 * a link names one of `DOC_SLUGS`, never a page the site may not have, and a test holds every entry of
 * `DOCS_BY_ROUTE` to that list.
 */

/** The public site, when the instance has not said otherwise yet. */
export const DEFAULT_DOCS_URL = "https://docs.varga.foundation/choregos";

/** The pages of the documentation (S62 shared contract). `""` is its home. Keep this list the only one. */
export const DOC_SLUGS = [
  "",
  "concepts",
  "get-started",
  "use/projects-and-members",
  "use/workflows",
  "use/work-items",
  "use/decisions",
  "use/knowledge",
  "use/connectors",
  "use/budgets-and-policies",
  "build/skills",
  "build/agents",
  "build/plugins",
  "build/tools",
  "integrate/mcp",
  "integrate/a2a",
  "integrate/triggers",
  "integrate/rest",
  "cli/install",
  "cli/reference",
  "host",
  "reference",
] as const;

export type DocSlug = (typeof DOC_SLUGS)[number];

/**
 * Which page of the documentation a screen points to: a route pattern and its slug. `*` stands for one
 * segment (a project's slug); a pattern is a prefix of the path, unless it ends with `$`. The most
 * specific pattern wins (the one with the most segments), so `/p/*\/integrations/a2a` beats
 * `/p/*\/integrations`.
 */
export const DOCS_BY_ROUTE: ReadonlyArray<readonly [string, DocSlug]> = [
  ["/$", "use/projects-and-members"],
  ["/projects/new", "use/projects-and-members"],
  ["/p/*$", "use/projects-and-members"],
  ["/p/*/members", "use/projects-and-members"],
  ["/p/*/settings", "use/projects-and-members"],
  ["/p/*/settings/people", "use/projects-and-members"],
  ["/p/*/settings/security", "use/budgets-and-policies"],
  ["/p/*/settings/agents", "build/agents"],
  ["/p/*/settings/connections", "use/connectors"],
  ["/p/*/workflow", "use/workflows"],
  ["/p/*/workflows", "use/workflows"],
  ["/p/*/board", "use/work-items"],
  ["/p/*/items", "use/work-items"],
  ["/p/*/inbox", "use/decisions"],
  ["/inbox", "use/decisions"],
  ["/p/*/knowledge", "use/knowledge"],
  ["/knowledge", "use/knowledge"],
  ["/library/connectors", "use/connectors"],
  ["/admin/connectors", "use/connectors"],
  ["/p/*/agents", "build/agents"],
  ["/agents", "build/agents"],
  ["/skills", "build/skills"],
  ["/library/plugins", "build/plugins"],
  ["/library/mcp-servers", "build/tools"],
  ["/integrations", "integrate/mcp"],
  ["/integrations/a2a", "integrate/a2a"],
  ["/integrations/cli", "cli/install"],
  ["/integrations/rest", "integrate/rest"],
  ["/p/*/integrations", "integrate/mcp"],
  ["/p/*/integrations/a2a", "integrate/a2a"],
  ["/p/*/integrations/cli", "cli/install"],
  ["/p/*/integrations/rest", "integrate/rest"],
  ["/costs", "use/budgets-and-policies"],
  ["/admin", "host"],
];

/** A base URL the console may put in an `href`: an `http(s)` address, else the public site. */
export function docsBase(value: string | null | undefined): string {
  const trimmed = (value ?? "").trim().replace(/\/+$/, "");
  return /^https?:\/\/[^\s"'<>]+$/.test(trimmed) ? trimmed : DEFAULT_DOCS_URL;
}

/** `https://docs…/choregos/integrate/a2a`; the home is `https://docs…/choregos/`. */
export function docsUrl(base: string | null | undefined, slug: DocSlug): string {
  return `${docsBase(base)}/${slug}`;
}

function matches(pattern: string, segments: string[]): boolean {
  const exact = pattern.endsWith("$");
  const wanted = pattern.replace(/\$$/, "").split("/").filter(Boolean);
  if (exact ? segments.length !== wanted.length : segments.length < wanted.length) return false;
  return wanted.every((part, index) => part === "*" || part === segments[index]);
}

/** The page a screen points to, or `null` when none fits it. */
export function docsSlugFor(pathname: string | null | undefined): DocSlug | null {
  if (!pathname) return null;
  const segments = pathname.split(/[?#]/)[0]!.split("/").filter(Boolean);
  let best: { slug: DocSlug; weight: number } | null = null;
  for (const [pattern, slug] of DOCS_BY_ROUTE) {
    if (!matches(pattern, segments)) continue;
    const weight = pattern.replace(/\$$/, "").split("/").filter(Boolean).length;
    if (!best || weight > best.weight) best = { slug, weight };
  }
  return best?.slug ?? null;
}
