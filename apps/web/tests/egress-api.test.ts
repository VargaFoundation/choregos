import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";

/** The adds that came with the catalogues and remote agents carry `allow_egress` only once asked (S39-22). */
function fetched() {
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
  return () => spy.mock.calls.map(([url, init]) => ({ url: String(url), body: init?.body }));
}

afterEach(() => vi.restoreAllMocks());

describe("allow_egress on the adds", () => {
  it("is absent until the modal was answered", async () => {
    const calls = fetched();
    await api.installSkillCatalogueEntry("acme", "runbook");
    await api.discoverRemoteAgent("acme", "https://a.test/card");
    await api.createAgent("acme", { slug: "x", display_name: "X" } as never);
    await api.publishAgentVersion("acme", "x", {} as never);
    const seen = calls();
    expect(seen.map((c) => c.url.includes("allow_egress"))).toEqual([false, false, false, false]);
    expect(seen[0]!.body).toBeUndefined();
  });

  it("is sent as a body for a skill install and as a query for the agents", async () => {
    const calls = fetched();
    await api.installSkillCatalogueEntry("acme", "runbook", true);
    await api.discoverRemoteAgent("acme", "https://a.test/card", true);
    await api.createAgent("acme", { slug: "x", display_name: "X" } as never, true);
    await api.publishAgentVersion("acme", "x", {} as never, true);
    const [skill, discover, create, publish] = calls();
    expect(JSON.parse(String(skill!.body))).toEqual({ allow_egress: true });
    expect([discover, create, publish].every((c) => c!.url.endsWith("?allow_egress=true"))).toBe(true);
  });
});
