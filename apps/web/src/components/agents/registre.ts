// SPDX-License-Identifier: Apache-2.0
import type { AgentCredential, AgentResolution, AgentSpec, ApiToken, ImplicitAgent } from "@/lib/types";

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
  if (spec.runtime?.kind === "code") morceaux.push(`code · ${spec.runtime.image}`);
  else if (spec.backend) morceaux.push(spec.backend);
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

/** Who owns an agent (ADR 0061). */
export type AgentScope = "platform" | "org" | "project" | "personal";
/** The scopes a person can create or fork into: a common agent only comes from the catalogue. */
export type MakeScope = Exclude<AgentScope, "platform">;

export const SCOPES: AgentScope[] = ["platform", "org", "project", "personal"];

/** The word the console uses for a scope: "Common" is the platform's catalogue agents. */
export function scopeLabel(scope: AgentScope | undefined, project?: string | null): string {
  switch (scope) {
    case "platform":
      return "Common";
    case "project":
      return project ? `Project · ${project}` : "Project";
    case "personal":
      return "Personal";
    default:
      // An API that predates the scopes sends none: every agent was an organisation's.
      return "Organisation";
  }
}

/** One sentence on what a scope means, for the filters and the dialogs. */
export function scopeHint(scope: AgentScope): string {
  switch (scope) {
    case "platform":
      return "Common agents come from the platform's catalogue; only an organisation admin changes them.";
    case "org":
      return "Shared by every project; an organisation admin changes it.";
    case "project":
      return "Belongs to one project and runs only there; its owner changes it.";
    default:
      return "Yours alone: it never runs in a project's workflow. Promote or fork it to reach one.";
  }
}

/**
 * The slug a fork proposes: `<source>-<project>` into a project, `<source>-<user>` into a personal
 * scope, `<source>-copy` into the organisation. 2 to 63 characters, starting with a letter.
 */
export function forkSlug(source: string, scope: MakeScope, project: string | null | undefined, email: string | null | undefined): string {
  const suffix =
    scope === "project" ? (project ?? "") : scope === "personal" ? (email ?? "").split("@")[0]! : "copy";
  const clean = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9-]+/g, "-")
      .replace(/^-+|-+$/g, "");
  const tail = clean(suffix) || "copy";
  return `${clean(source).slice(0, 63 - tail.length - 1).replace(/-+$/, "")}-${tail}`;
}

/** What a person may create, from their memberships; the API stays the judge (ADR 0061). */
export type Creatable = { org: boolean; personal: boolean; projects: string[] };

export function creatableScopes(memberships: Array<{ org: string; role: string; project_slug?: string | null }>, org: string): Creatable {
  const mine = memberships.filter((m) => m.org === org);
  const admin = mine.some((m) => m.role === "org_admin" && !m.project_slug);
  const personal = admin || mine.some((m) => ["developer", "release_captain", "project_owner", "org_admin"].includes(m.role));
  const projects = mine.filter((m) => m.role === "project_owner" && m.project_slug).map((m) => m.project_slug!);
  return { org: admin, personal, projects: [...new Set(projects)] };
}

/** The scopes, among the creatable ones, in the order the form offers them. */
export function makeScopes(c: Creatable, allProjects: string[]): MakeScope[] {
  const out: MakeScope[] = [];
  if (c.org) out.push("org");
  if ((c.org ? allProjects : c.projects).length > 0) out.push("project");
  if (c.personal) out.push("personal");
  return out;
}

/**
 * Whether the page must hold an edit back: the server says `can.publish` is false. An API that sends
 * no `can` has no governance: nothing is held back.
 */
export function readOnly(agent: { can?: { publish?: boolean } }): boolean {
  return agent.can?.publish === false;
}

/**
 * Why a run of the project would load this revision, in a word (S40-02, ADR 0071): the label that follows
 * `v3 ·` on a process step. An answer from before channels carries no `source`: it says what it always said.
 */
export function sourceLabel(r: Pick<AgentResolution, "source" | "pinned" | "channel">): string {
  switch (r.source) {
    case "stable":
      return "production";
    case "latest":
      return "latest";
    case "project_channel":
      return "testing";
    case "pin_channel":
      return r.channel === "candidate" ? "pinned to testing" : "pinned to production";
    case "canary":
      return "testing, canary";
    case "pin":
      return "pinned";
    case "explicit":
      return "explicit";
    default:
      return r.pinned ? "pinned" : "latest";
  }
}

/** "10 % of work items run v5": what the project's answer cannot apply (it has no work item) but the page can say. */
export function canarySentence(r: Pick<AgentResolution, "candidate_version" | "candidate_percent" | "source">): string | null {
  if (r.candidate_version == null || !r.candidate_percent) return null;
  if (r.source === "explicit" || r.source === "pin" || r.source === "pin_channel") return null;
  return `${r.candidate_percent} % of work items run v${r.candidate_version}`;
}
