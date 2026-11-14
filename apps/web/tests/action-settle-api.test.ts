// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The console's client of `settleAction` (S65-26): its path, its verb and its body; and a settlement in a session that
 * is too old goes back through the sign-in and comes back to the action's page, as a decision does.
 */
function answering(status = 200, body: unknown = {}) {
  const fetched = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetched);
  return fetched;
}

afterEach(() => vi.unstubAllGlobals());

describe("the client of a settlement", () => {
  it("posts the outcome, the note and the result to the action's settlement", async () => {
    const fetched = answering(200, { id: "a1", status: "approved" });
    const { api } = await import("@/lib/api");
    await api.settleAction("acme:billing-api", "a1", { outcome: "made", note: "found", result: { key: "OPS-8" } });
    const [url, init] = fetched.mock.calls[0]!;
    expect([init?.method, url]).toEqual(["POST", "/api/v1/projects/acme:billing-api/actions/a1/settlement"]);
    expect(JSON.parse(String(init?.body))).toEqual({ outcome: "made", note: "found", result: { key: "OPS-8" } });
  });

  it("a session that is too old goes back through the sign-in, and comes back to the action's page", async () => {
    const assign = vi.fn();
    const page = "/p/billing-api/actions/a1";
    vi.stubGlobal("window", { location: { pathname: page, search: "", origin: "http://c", assign } });
    answering(401, {
      title: "Authentication too old",
      detail: "this decision needs an authentication less than 10 min old; yours is 12 min old",
      errors: [{ error: "step_up_required", reauth: "GET /api/v1/auth/login?reauth=1" }],
    });
    const { api, ApiError } = await import("@/lib/api");
    const refused = await api
      .settleAction("acme:billing-api", "a1", { outcome: "not_made", note: "nothing there" })
      .catch((cause: unknown) => cause);
    expect(refused).toBeInstanceOf(ApiError);
    expect(assign).toHaveBeenCalledWith(`http://c/api/v1/auth/login?redirect_to=${encodeURIComponent(page)}&reauth=1`);
  });
});
