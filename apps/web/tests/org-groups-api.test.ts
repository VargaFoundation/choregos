// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

/** The console's client of the organisation's groups (ADR 0160): the eight operations of the contract, each at its path. */
function answering(status = 200, body: unknown = {}) {
  const fetched = vi.fn(async (_url: string, _init?: RequestInit) => new Response(status === 204 ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetched);
  return fetched;
}

afterEach(() => vi.unstubAllGlobals());

describe("the client of the groups' routes", () => {
  it("calls each of the eight operations at its path, with its verb and its body", async () => {
    const fetched = answering(200, { items: [] });
    const { api } = await import("@/lib/api");
    const base = "/api/v1/orgs/acme/groups";
    const one = `${base}/release-captains`;
    const calls: [() => Promise<unknown>, string, string, unknown?][] = [
      [() => api.orgGroups("acme"), "GET", base],
      [() => api.createOrgGroup("acme", { slug: "release-captains", name: "Captains" }), "POST", base, { slug: "release-captains", name: "Captains" }],
      [() => api.orgGroup("acme", "release-captains"), "GET", one],
      [() => api.updateOrgGroup("acme", "release-captains", { description: null }), "PATCH", one, { description: null }],
      [() => api.deleteOrgGroup("acme", "release-captains"), "DELETE", one],
      [() => api.orgGroupMembers("acme", "release-captains"), "GET", `${one}/members`],
      [() => api.addOrgGroupMember("acme", "release-captains", { email: "cap@acme.dev" }), "POST", `${one}/members`, { email: "cap@acme.dev" }],
      [() => api.removeOrgGroupMember("acme", "release-captains", "u-1"), "DELETE", `${one}/members/u-1`],
    ];
    for (const [call, method, path, body] of calls) {
      fetched.mockClear();
      await call();
      const [url, init] = fetched.mock.calls[0]!;
      expect([init?.method ?? "GET", url]).toEqual([method, path]);
      expect(init?.body === undefined ? undefined : JSON.parse(String(init.body))).toEqual(body);
    }
  });
});
