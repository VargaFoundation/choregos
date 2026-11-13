// SPDX-License-Identifier: Apache-2.0
/**
 * Watchers, stars, forks and the "agents you watch" items, played in memory for the demo (ADR 0152).
 * The counters start from fixed seeds so a screenshot is stable; a gesture changes them for the page's life.
 */
import type { Agent, AgentForkEntry, AgentNotification, AgentSocial } from "@/lib/types";

type Mine = { watching: AgentSocial["watching"]; starred: boolean };
const SEED: Record<string, { watchers: number; stars: number }> = {
  developer: { watchers: 7, stars: 12 },
  architect: { watchers: 3, stars: 5 },
  "developer-billing-api": { watchers: 1, stars: 0 },
};
const mine = new Map<string, Mine>();
const nowIso = () => new Date().toISOString();
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const notifications: AgentNotification[] = [
  { id: "n1", kind: "production_moved", agent: "developer", actor: "admin@acme.dev", version: 2, previous_version: 1, detail: "promote", created_at: ago(40), read_at: null },
  { id: "n2", kind: "forked", agent: "developer", fork: "developer-billing-api", actor: "lea@acme.dev", version: 1, created_at: ago(300), read_at: null },
  { id: "n3", kind: "lifecycle_changed", agent: "architect", detail: "deprecated", created_at: ago(2000), read_at: ago(1000) },
];

function stateOf(slug: string): Mine {
  let state = mine.get(slug);
  if (!state) mine.set(slug, (state = { watching: null, starred: false }));
  return state;
}

function forksOf(slug: string, agents: Agent[]): Agent[] {
  return agents.filter((a) => a.forked_from?.slug === slug);
}

export function socialOf(agent: Agent, agents: Agent[] = []): AgentSocial {
  const seed = SEED[agent.slug] ?? { watchers: 0, stars: 0 };
  const state = stateOf(agent.slug);
  return {
    // The seeds are other people's; the caller's own gesture adds one.
    watchers: seed.watchers + (state.watching != null ? 1 : 0),
    stars: seed.stars + (state.starred ? 1 : 0),
    forks: forksOf(agent.slug, agents).length,
    watching: state.watching,
    starred_by_me: state.starred,
  };
}

export function agentSocialApi(method: string, route: string, body: unknown, agents: Agent[], email: string): unknown {
  const slugged = /^\/orgs\/[^/]+\/agents\/([^/]+)\/(forks|watch|star)$/.exec(route);
  if (slugged) {
    const agent = agents.find((a) => a.slug === slugged[1]);
    if (!agent) return undefined;
    const state = stateOf(agent.slug);
    if (slugged[2] === "forks" && method === "GET") {
      const items: AgentForkEntry[] = forksOf(agent.slug, agents).map((fork) => ({
        slug: fork.slug,
        display_name: fork.display_name,
        scope: fork.scope ?? "org",
        project: fork.project ?? null,
        owner: fork.owner ?? null,
        created_at: fork.created_at ?? ago(3000),
        forked_from_version: fork.forked_from?.version ?? null,
        latest_version: fork.latest_version,
        diverged: fork.latest_version > 1,
        lifecycle: "active",
      }));
      return { items };
    }
    if (slugged[2] === "watch" && method === "PUT") state.watching = (body as { level?: AgentSocial["watching"] } | null)?.level ?? "all";
    else if (slugged[2] === "watch" && method === "DELETE") state.watching = null;
    else if (slugged[2] === "star" && method === "PUT") state.starred = true;
    else if (slugged[2] === "star" && method === "DELETE") state.starred = false;
    else return undefined;
    return socialOf(agent, agents);
  }
  if (method === "GET" && /^\/orgs\/[^/]+\/agent-notifications$/.test(route)) {
    return { items: structuredClone(notifications), unread: notifications.filter((n) => n.read_at == null).length };
  }
  if (method === "POST" && /^\/orgs\/[^/]+\/agent-notifications\/read$/.test(route)) {
    let updated = 0;
    for (const n of notifications) {
      if (n.read_at != null) continue;
      n.read_at = nowIso();
      updated++;
    }
    return { updated };
  }
  const one = /^\/orgs\/[^/]+\/agent-notifications\/([^/]+)\/read$/.exec(route);
  if (method === "POST" && one) {
    const found = notifications.find((n) => n.id === one[1] && n.read_at == null);
    if (found) found.read_at = nowIso();
    return { updated: found ? 1 : 0 };
  }
  void email;
  return undefined;
}
