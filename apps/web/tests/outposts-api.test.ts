// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The console's client of the outposts' management routes (ADR 0147): the twelve operations of the
 * contract, each at its path. A person approves an instance in a session that carries a recent
 * sign-in: when the API says the session is too old (401 `step_up_required`), the client goes back
 * through the sign-in and comes back to the outpost's page, the way it does for a decision on an action.
 */
const FINGERPRINT = `SHA256:${"a".repeat(43)}`;

function answering(status = 200, body: unknown = {}) {
  const fetched = vi.fn(async (_url: string, _init?: RequestInit) => new Response(status === 204 ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetched);
  return fetched;
}

afterEach(() => vi.unstubAllGlobals());

describe("the client of the outposts' routes", () => {
  it("calls each of the twelve operations at its path, with its verb and its body", async () => {
    const fetched = answering(200, { items: [], meta: { has_more: false, next_cursor: null } });
    const { api } = await import("@/lib/api");
    const base = "/api/v1/orgs/acme/outposts";
    const one = `${base}/factory-floor`;
    const instance = `${one}/instances/i-1`;
    const calls: [() => Promise<unknown>, string, string, unknown?][] = [
      [() => api.outposts("acme"), "GET", base],
      [() => api.createOutpost("acme", { slug: "factory-floor", display_name: "Factory floor" }), "POST", base, { slug: "factory-floor", display_name: "Factory floor" }],
      [() => api.outpost("acme", "factory-floor"), "GET", one],
      [() => api.pauseOutpost("acme", "factory-floor"), "POST", `${one}/pause`],
      [() => api.resumeOutpost("acme", "factory-floor"), "POST", `${one}/resume`],
      [() => api.revokeOutpost("acme", "factory-floor"), "POST", `${one}/revoke`],
      [() => api.createOutpostEnrolment("acme", "factory-floor"), "POST", `${one}/enrolments`, {}],
      [() => api.createOutpostEnrolment("acme", "factory-floor", { expires_in_minutes: 5 }), "POST", `${one}/enrolments`, { expires_in_minutes: 5 }],
      [() => api.outpostInstances("acme", "factory-floor"), "GET", `${one}/instances`],
      [() => api.approveOutpostInstance("acme", "factory-floor", "i-1", { fingerprint: FINGERPRINT }), "POST", `${instance}/approve`, { fingerprint: FINGERPRINT }],
      [() => api.revokeOutpostInstance("acme", "factory-floor", "i-1"), "POST", `${instance}/revoke`],
      [() => api.outpostCalls("acme", "factory-floor"), "GET", `${one}/calls`],
      [() => api.outpostCalls("acme", "factory-floor", { state: "refused", cursor: "c-2", limit: 50 }), "GET", `${one}/calls?state=refused&cursor=c-2&limit=50`],
    ];
    for (const [call, method, path, body] of calls) {
      fetched.mockClear();
      await call();
      const [url, init] = fetched.mock.calls[0]!;
      expect([init?.method ?? "GET", url]).toEqual([method, path]);
      expect(init?.body === undefined ? undefined : JSON.parse(String(init.body))).toEqual(body);
    }
  });

  it("a deletion answers nothing", async () => {
    const fetched = answering(204);
    const { api } = await import("@/lib/api");
    await expect(api.deleteOutpost("acme", "factory-floor")).resolves.toBeUndefined();
    expect(fetched.mock.calls[0]![0]).toBe("/api/v1/orgs/acme/outposts/factory-floor");
    expect(fetched.mock.calls[0]![1]?.method).toBe("DELETE");
  });

  it("an approval in a session that is too old goes back through the sign-in, and comes back to the outpost's page", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/admin/outposts/factory-floor", search: "", origin: "http://c", assign } });
    answering(401, {
      title: "Authentication too old",
      detail: "this decision needs an authentication less than 10 min old; yours is 12 min old",
      errors: [{ error: "step_up_required", reauth: "GET /api/v1/auth/login?reauth=1" }],
    });
    const { api, ApiError } = await import("@/lib/api");
    const refused = await api.approveOutpostInstance("acme", "factory-floor", "i-1", { fingerprint: FINGERPRINT }).catch((cause: unknown) => cause);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as Error).message).toContain("less than 10 min old");
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith("http://c/api/v1/auth/login?redirect_to=%2Fadmin%2Foutposts%2Ffactory-floor&reauth=1");
  });

  it("an approval refused for another reason goes nowhere: the page says what the API said", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/admin/outposts/factory-floor", search: "", origin: "http://c", assign } });
    answering(409, { title: "Conflict", detail: "the fingerprint sent is not the one this instance enrolled with", code: "fingerprint_mismatch" });
    const { api } = await import("@/lib/api");
    await expect(api.approveOutpostInstance("acme", "factory-floor", "i-1", { fingerprint: FINGERPRINT })).rejects.toMatchObject({
      status: 409,
      problem: { code: "fingerprint_mismatch" },
    });
    expect(assign).not.toHaveBeenCalled();
  });
});
