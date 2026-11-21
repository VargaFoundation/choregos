// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { describe, expect, it } from "vitest";
import { mockApi } from "@/mocks/data";

type Page = { items: Record<string, unknown>[]; meta: { has_more: boolean; next_cursor: string | null } };
const get = (path: string) => mockApi<Page>(path);

/** Every row of a mock list, page after page by `limit=1`: each is reached exactly once. */
async function walk(path: string, key: string): Promise<unknown[]> {
  const seen: unknown[] = [];
  let cursor: string | null = null;
  for (let step = 0; step < 100; step++) {
    const page: Page = await get(`${path}${path.includes("?") ? "&" : "?"}limit=1${cursor ? `&cursor=${cursor}` : ""}`);
    expect(page.items.length).toBeLessThanOrEqual(1);
    seen.push(...page.items.map((row) => row[key]));
    if (!page.meta.has_more) return seen;
    cursor = page.meta.next_cursor;
  }
  throw new Error("the list never ended");
}

/**
 * The demo answers the five lists that are not SQL tables as the API does (ADR 0206): `{items, meta}`, a
 * page at a time, filtered on `q` and on the route's own parameters before the cut.
 */
describe("the demo pages the lists built in code (ADR 0206)", () => {
  it.each([
    ["/projects/f4f/triggers", "name"],
    ["/orgs/acme/workflow-catalogue", "name"],
    ["/orgs/acme/agent-catalogue", "slug"],
    ["/platform/models", "model_name"],
    ["/orgs/acme/models/usable", "model_name"],
  ])("%s: every row once across pages, as many as in one go", async (path, key) => {
    const whole = (await get(`${path}?limit=200`)).items.map((row) => row[key]);
    expect(whole.length).toBeGreaterThan(0);
    expect(await walk(path, key)).toEqual(whole);
  });

  it("filters before it cuts: q, kind and needs_workspace", async () => {
    const all = (await get("/orgs/acme/agent-catalogue?limit=200")).items;
    const internal = (await get("/orgs/acme/agent-catalogue?kind=internal&limit=200")).items;
    expect(internal.length).toBeGreaterThan(0);
    expect(internal.length).toBeLessThan(all.length);
    expect(internal.every((e) => e.kind === "internal")).toBe(true);

    const first = String(all[0]!.slug);
    const found = (await get(`/orgs/acme/agent-catalogue?q=${first.toUpperCase()}&limit=200`)).items;
    expect(found.map((e) => e.slug)).toContain(first);
    expect((await get("/orgs/acme/agent-catalogue?q=no-such-agent-anywhere")).items).toEqual([]);

    const without = (await get("/orgs/acme/workflow-catalogue?needs_workspace=false&limit=200")).items;
    const withRepo = (await get("/orgs/acme/workflow-catalogue?needs_workspace=true&limit=200")).items;
    const every = (await get("/orgs/acme/workflow-catalogue?limit=200")).items;
    expect(without.length + withRepo.length).toBe(every.length);
    expect(without.length).toBeGreaterThan(0);
    expect(withRepo.length).toBeGreaterThan(0);
  });
});
