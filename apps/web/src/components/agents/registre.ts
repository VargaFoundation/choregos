// SPDX-License-Identifier: Apache-2.0
import type { AgentCredential, AgentSpec, ApiToken, ImplicitAgent } from "@/lib/types";

/** Les portées qui ouvrent la porte MCP (ADR 0030) : un jeton qui en porte une est un client MCP. */
const PORTEES_MCP = new Set(["mcp:read", "mcp:write"]);

export function estUnClientMcp(jeton: Pick<ApiToken, "scopes">): boolean {
  return (jeton.scopes ?? []).some((portee) => PORTEES_MCP.has(portee));
}

/**
 * Ce qu'on dit d'un client rattaché : s'il a appelé la porte, quand, et avec quoi. Rien n'est
 * inventé — un client qui n'a jamais appelé n'est pas « connecté ».
 */
export function etatDuClient(
  client: Pick<AgentCredential, "last_used_at" | "last_client" | "revoked_at">,
  maintenant: Date = new Date(),
): { connecte: boolean; recent: boolean } {
  if (client.revoked_at || !client.last_used_at) return { connecte: false, recent: false };
  const ecart = maintenant.getTime() - new Date(client.last_used_at).getTime();
  return { connecte: true, recent: ecart < 24 * 3600 * 1000 };
}

/** Une version en une ligne : modèle, backend, skills, budget du jour, outils. */
export function resumeDeLaVersion(spec: AgentSpec | undefined | null): string {
  if (!spec) return "—";
  const morceaux: string[] = [];
  if (spec.model) morceaux.push(spec.model);
  if (spec.backend) morceaux.push(spec.backend);
  const skills = spec.skills?.length ?? 0;
  if (skills) morceaux.push(`${skills} skill${skills > 1 ? "s" : ""}`);
  const outils = (spec.mcp_servers ?? []).reduce((total, serveur) => total + (serveur.tools?.length ?? 0), 0);
  if (outils) morceaux.push(`${outils} tool pattern${outils > 1 ? "s" : ""}`);
  if (spec.budget?.daily_usd != null) morceaux.push(`$${spec.budget.daily_usd}/day`);
  if (spec.budget?.monthly_usd != null) morceaux.push(`$${spec.budget.monthly_usd}/month`);
  return morceaux.length > 0 ? morceaux.join(" · ") : "defaults of the platform";
}

export type AgentImplicite = {
  workflow: string;
  acteur: string;
  role?: string;
  model?: string;
  backend?: string;
  max_turns?: number;
  max_minutes?: number;
};

/**
 * Les « agents implicites » d'un projet : les acteurs `agent` d'un workflow qui ne nomment aucun
 * agent du registre. The API computes them (S30-04, `GET /projects/{id}/implicit-agents`): the
 * console used to read every workflow of the project, one request each. Here they take the
 * console's shape.
 */
export function implicitFromApi(list: ImplicitAgent[]): AgentImplicite[] {
  return list.map((a) => ({
    workflow: a.workflow,
    acteur: a.actor,
    role: a.role ?? undefined,
    model: a.model ?? undefined,
    backend: a.backend ?? undefined,
    max_turns: a.max_turns ?? undefined,
    max_minutes: a.max_minutes ?? undefined,
  }));
}

/** Un nom d'acteur fait un slug d'agent : minuscules, chiffres et tirets. */
export function slugDeLActeur(acteur: string): string {
  const slug = acteur
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.length > 0 ? slug.slice(0, 63) : "agent";
}

/**
 * La version qu'un acteur implicite devient : ce que le workflow disait de lui, rien de plus. Ses
 * instructions restent celles du playbook de son rôle, tant qu'une version n'en écrit pas.
 */
export function versionDeLActeur(implicite: AgentImplicite): AgentSpec {
  const spec: AgentSpec = {};
  if (implicite.model) spec.model = implicite.model;
  if (implicite.backend) spec.backend = implicite.backend;
  if (implicite.max_turns != null || implicite.max_minutes != null) {
    spec.limits = { max_turns: implicite.max_turns ?? null, max_minutes: implicite.max_minutes ?? null };
  }
  return spec;
}
