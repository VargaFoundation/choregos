// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockPage } from "@/mocks/paging";

/**
 * The workflow lists are paged by the server (ADR 0206): the client reads the whole set by following
 * `next_cursor` where a screen needs it all, and the demo mocks page and sort like the server.
 */
function pages(...bodies: unknown[]) {
  const fetched = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(bodies.shift()), { status: 200 }));
  vi.stubGlobal("fetch", fetched);
  return fetched;
}

const meta = (next: string | null) => ({ has_more: next !== null, next_cursor: next });

afterEach(() => vi.unstubAllGlobals());

describe("the client of the paged workflow lists", () => {
  it("follows next_cursor until the last page, asking 200 rows each time", async () => {
    const fetched = pages(
      { items: [{ name: "a" }], meta: meta("c1") },
      { items: [{ name: "b" }], meta: meta("c2") },
      { items: [{ name: "c" }], meta: meta(null) },
    );
    const { api } = await import("@/lib/api");
    const all = await api.workflows("acme:billing");
    expect(all.map((w) => w.name)).toEqual(["a", "b", "c"]);
    const urls = fetched.mock.calls.map((c) => c[0]);
    expect(urls[0]).toMatch(/\/projects\/[^/]+\/workflows\?limit=200$/);
    expect(urls[1]).toMatch(/workflows\?cursor=c1&limit=200$/);
    expect(urls[2]).toMatch(/workflows\?cursor=c2&limit=200$/);
  });

  it("pages the versions, the implicit agents and the workflow agents the same way", async () => {
    const { api } = await import("@/lib/api");
    const fetched = pages({ items: [{ version: 2 }], meta: meta("v") }, { items: [{ version: 1 }], meta: meta(null) });
    expect((await api.workflowVersions("acme:billing", "default")).map((v) => v.version)).toEqual([2, 1]);
    expect(fetched.mock.calls[1]![0]).toMatch(/workflows\/default\/versions\?cursor=v&limit=200$/);
    const implicit = pages({ items: [{ actor: "dev" }], meta: meta(null) });
    expect(await api.implicitAgents("acme:billing")).toHaveLength(1);
    expect(implicit.mock.calls[0]![0]).toMatch(/implicit-agents\?limit=200$/);
    const actors = pages({ items: [{ actor: "dev" }], meta: meta(null) });
    expect(await api.workflowAgents("acme:billing")).toHaveLength(1);
    expect(actors.mock.calls[0]![0]).toMatch(/workflow-agents\?limit=200$/);
  });

  it("asks one page of the machine accounts and the pins, with the cursor and the limit it is given", async () => {
    const { api } = await import("@/lib/api");
    const fetched = pages({ items: [], meta: meta(null) }, { items: [], meta: meta(null) });
    await api.machineAccounts("acme:billing", { cursor: "x", limit: 50 });
    await api.projectAgents("acme:billing", { limit: 10 });
    expect(fetched.mock.calls[0]![0]).toMatch(/machine-accounts\?cursor=x&limit=50$/);
    expect(fetched.mock.calls[1]![0]).toMatch(/\/agents\?limit=10$/);
  });
});

describe("the demo mock pages like the server", () => {
  const rows = Array.from({ length: 5 }, (_, i) => ({ name: `w${i}`, tie: 1 }));

  it("reaches every row exactly once, in key order, whatever the page size", () => {
    const seen: string[] = [];
    let path = "/x?limit=2";
    for (let guard = 0; guard < 10; guard++) {
      const page = mockPage([...rows].reverse(), path, (r) => r.name);
      seen.push(...page.items.map((r) => r.name));
      if (!page.meta.has_more) break;
      path = `/x?limit=2&cursor=${page.meta.next_cursor}`;
    }
    expect(seen).toEqual(["w0", "w1", "w2", "w3", "w4"]);
  });

  it("keeps rows that share a key, each once, and can read newest first", () => {
    const tied = [{ id: "a" }, { id: "b" }, { id: "c" }];
    const first = mockPage(tied, "/x?limit=2", () => 7);
    const second = mockPage(tied, `/x?limit=2&cursor=${first.meta.next_cursor}`, () => 7);
    expect([...first.items, ...second.items].map((r) => r.id)).toEqual(["a", "b", "c"]);
    const versions = mockPage([{ v: 1 }, { v: 3 }, { v: 2 }], "/x?limit=2", (r) => r.v, { descending: true });
    expect(versions.items.map((r) => r.v)).toEqual([3, 2]);
    expect(versions.meta.has_more).toBe(true);
  });
});
