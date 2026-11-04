// SPDX-License-Identifier: Apache-2.0
/**
 * The connector kinds, said in plain words. The review of 07/10 read "identity", "mdm" and "mcp"
 * side by side without knowing what tells them apart: a label says what the system IS.
 */
const LIBELLES: Record<string, string> = {
  identity: "directory (identity)",
  mdm: "device fleet (MDM)",
  shipping: "carrier (shipping)",
  access_control: "physical access (badges)",
  marketdata: "market data (prices)",
  web_search: "web search",
  mcp: "MCP server",
  tracker: "work tracker",
  scm: "source code",
  ci: "CI pipelines",
  cd: "deployment (CD)",
  runtime: "agent runtime",
  gateway: "model gateway",
  memory: "memory",
  notify: "notifications",
};

/** The title of a connector card (S40-03b): what the system is for, as a person would name it. */
const TITRES: Record<string, string> = {
  tracker: "Work tracker",
  scm: "Source code",
  ci: "CI pipelines",
  cd: "Deployment (CD)",
  runtime: "Agent runtime",
  gateway: "Model gateway",
  memory: "Memory",
  notify: "Notifications",
};

export function kindTitle(kind: string): string {
  const title = TITRES[kind];
  if (title) return title;
  const label = LIBELLES[kind] ?? kind;
  return label.charAt(0).toUpperCase() + label.slice(1);
}
export const titreDeSorte = kindTitle; // lang: fr-ok alias:internal

/** What a kind designates; a kind that an extension brings keeps its own name. */
export function kindLabel(kind: string): string {
  return LIBELLES[kind] ?? kind;
}
export const libelleDeSorte = kindLabel; // lang: fr-ok alias:internal

/**
 * The delivery tools of a PROJECT (ADR 0034): they are set in each project's settings, never at
 * organisation level. The order is that of the requirements (`choregos_core.dsl.exigences`).
 */
export const PROJECT_KINDS = ["tracker", "scm", "ci", "cd", "runtime", "gateway", "memory"] as const;
export const SORTES_DU_PROJET = PROJECT_KINDS; // lang: fr-ok alias:internal

export function isProjectKind(kind: string): boolean {
  return (PROJECT_KINDS as readonly string[]).includes(kind);
}
export const estUneSorteDuProjet = isProjectKind; // lang: fr-ok alias:internal

/**
 * A kind an ORGANISATION may declare (S44-04): every kind but a project's delivery tools, except the
 * model gateway — an organisation that brings its own LLM gateway declares it once, and its model
 * catalogue and its runs use it (ADR 0060). The `direct` gateway measures no cost: never offered here.
 */
export function isOrgDeclarable(kind: string, type?: string): boolean {
  if (kind === "gateway") return type !== "direct";
  return !isProjectKind(kind);
}
