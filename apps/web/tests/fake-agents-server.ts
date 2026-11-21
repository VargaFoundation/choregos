// SPDX-License-Identifier: Apache-2.0
import type { Agent } from "@/lib/types";
import type { AgentsParams } from "@/lib/api";

/**
 * What `GET /orgs/{org}/agents` does with a registry (ADR 0206): the filters in the query, a page of `limit`
 * rows by name, an opaque cursor. A test hands it the agents and plugs it into `api.agentsPage`.
 */
export function fakeAgentsPage(registry: Agent[], pending: string[] = []) {
  return async (_org: string, params: AgentsParams = {}) => {
    const needle = (params.q ?? "").toLowerCase();
    const kept = registry
      .filter(
        (a) =>
          (!params.scope || (a.scope ?? "org") === params.scope) &&
          (params.retired !== false || a.lifecycle !== "retired") &&
          (!params.awaiting_approval || pending.includes(a.slug)) &&
          (params.tag ?? []).every((t) => (a.tags ?? []).includes(t)) &&
          (!needle || a.slug.includes(needle) || a.display_name.toLowerCase().includes(needle)),
      )
      .sort((x, y) => x.slug.localeCompare(y.slug));
    const start = params.cursor ? Number(params.cursor) : 0;
    const limit = params.limit ?? 50;
    const more = start + limit < kept.length;
    return { items: kept.slice(start, start + limit), meta: { has_more: more, next_cursor: more ? String(start + limit) : null } };
  };
}
