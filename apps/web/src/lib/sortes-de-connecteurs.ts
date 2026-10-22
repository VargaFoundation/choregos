// SPDX-License-Identifier: Apache-2.0
/**
 * Les sortes de connecteurs, dites en clair. La revue du 07/10 lisait « identity », « mdm »,
 * « mcp » côte à côte sans savoir ce qui les distingue : un libellé dit ce que le système EST.
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

export function titreDeSorte(kind: string): string {
  const titre = TITRES[kind];
  if (titre) return titre;
  const libelle = LIBELLES[kind] ?? kind;
  return libelle.charAt(0).toUpperCase() + libelle.slice(1);
}

/** Ce qu'une sorte désigne ; une sorte qu'un greffon apporte garde son propre nom. */
export function libelleDeSorte(kind: string): string {
  return LIBELLES[kind] ?? kind;
}

/**
 * Les outils de livraison d'un PROJET (ADR 0034) : ils se règlent dans les réglages de chaque projet,
 * jamais à l'échelle de l'organisation. L'ordre est celui des exigences (`choregos_core.dsl.exigences`).
 */
export const SORTES_DU_PROJET = ["tracker", "scm", "ci", "cd", "runtime", "gateway", "memory"] as const;

export function estUneSorteDuProjet(kind: string): boolean {
  return (SORTES_DU_PROJET as readonly string[]).includes(kind);
}
