// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { mockApi } from "@/mocks/data";
import { MOCK_DOOR_KEYS, MOCK_DOOR_ORIGIN } from "@/mocks/outposts";
import type {
  AgentAccess,
  ConnectorDiscovery,
  ConnectorOperation,
  ConnectorType,
  OrgConnector,
  Outpost,
  OutpostCallPage,
  OutpostEnrolmentCreated,
  OutpostInstance,
  OutpostInstanceList,
  OutpostList,
} from "@/lib/types";

/**
 * The demo's outposts answer as the management API does: the console runs on them in development
 * and in the end-to-end journeys, so a rule the API has and the demo lacks is a screen nobody sees
 * refuse. Each refusal carries the API's code.
 */
const BASE = "/orgs/acme/outposts";
const post = <T>(path: string, body?: unknown) => mockApi<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const refusal = async (call: Promise<unknown>) => {
  const cause = await call.then(
    () => null,
    (error: unknown) => error,
  );
  expect(cause).toBeInstanceOf(ApiError);
  return cause as ApiError;
};

describe("the demo's outposts", () => {
  it("lists an outpost in service with an instance that waits, and a paused one", async () => {
    const { items } = await mockApi<OutpostList>(BASE);
    const factory = items.find((o) => o.slug === "factory-floor")!;
    const cold = items.find((o) => o.slug === "cold-store")!;
    expect([factory.status, factory.online, factory.instances]).toEqual(["active", true, { pending: 1, active: 1, revoked: 1 }]);
    expect([cold.status, cold.online]).toEqual(["paused", false]);
    expect(cold.last_seen_at).not.toBeNull();
    expect(factory.door_keys).toEqual(MOCK_DOOR_KEYS);
    for (const key of MOCK_DOOR_KEYS) expect(key).toMatch(/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/);
    const { items: instances } = await mockApi<OutpostInstanceList>(`${BASE}/factory-floor/instances`);
    expect(instances.map((i) => i.status)).toEqual(["pending", "active", "revoked"]);
    for (const each of instances) expect(each.fingerprint).toMatch(/^SHA256:[A-Za-z0-9_-]{43}$/);
  });

  it("shows calls as digests, with the states and refusal codes the page words", async () => {
    const all = await mockApi<OutpostCallPage>(`${BASE}/factory-floor/calls?limit=50`);
    expect(new Set(all.items.map((c) => c.state))).toEqual(new Set(["claimed", "answered", "refused", "in_doubt", "expired"]));
    expect(all.items.filter((c) => c.refusal).map((c) => c.refusal)).toEqual(["write_denied", "tool_not_allowed"]);
    expect(Object.keys(all.items[0]!)).not.toContain("arguments");
    const refused = await mockApi<OutpostCallPage>(`${BASE}/factory-floor/calls?state=refused&limit=1`);
    expect([refused.items.length, refused.meta.has_more]).toEqual([1, true]);
    const next = await mockApi<OutpostCallPage>(`${BASE}/factory-floor/calls?state=refused&limit=1&cursor=${refused.meta.next_cursor}`);
    expect([next.items.length, next.meta.has_more, next.items[0]!.id !== refused.items[0]!.id]).toEqual([1, false, true]);
  });

  it("follows an enrolment to its end: a token, an instance born pending, an approval against its fingerprint", async () => {
    const created = await post<Outpost>(BASE, { slug: "loading-dock", display_name: "Loading dock" });
    expect([created.status, created.instances]).toEqual(["active", { pending: 0, active: 0, revoked: 0 }]);
    expect((await refusal(post(BASE, { slug: "loading-dock" }))).problem.code).toBe("outpost_exists");
    expect((await refusal(post(BASE, { slug: "Loading Dock" }))).status).toBe(422);

    const issued = await post<OutpostEnrolmentCreated>(`${BASE}/loading-dock/enrolments`, {});
    expect(issued.token.startsWith("choe_")).toBe(true);
    expect([issued.control_plane, issued.door_keys]).toEqual([MOCK_DOOR_ORIGIN, MOCK_DOOR_KEYS]);
    expect(new Date(issued.expires_at).getTime() - Date.now()).toBeLessThanOrEqual(15 * 60_000);
    const [pending] = (await mockApi<OutpostInstanceList>(`${BASE}/loading-dock/instances`)).items;
    expect(pending!.status).toBe("pending");
    expect((await mockApi<OutpostInstanceList>(`${BASE}/loading-dock/instances`)).items).toHaveLength(1);

    const approve = `${BASE}/loading-dock/instances/${pending!.id}/approve`;
    const other = `SHA256:${"x".repeat(43)}`;
    expect((await refusal(post(approve, { fingerprint: other }))).problem.code).toBe("fingerprint_mismatch");
    expect((await refusal(post(approve, { fingerprint: "nope" }))).status).toBe(422);
    const approved = await post<OutpostInstance>(approve, { fingerprint: pending!.fingerprint });
    expect([approved.status, approved.approved_by, approved.online]).toEqual(["active", "augustin@acme.dev", true]);
    expect((await refusal(post(approve, { fingerprint: pending!.fingerprint }))).problem.code).toBe("instance_not_pending");
    expect((await mockApi<Outpost>(`${BASE}/loading-dock`)).online).toBe(true);
  });

  it("pauses, resumes, revokes for good, and deletes only what is revoked", async () => {
    await post<Outpost>(BASE, { slug: "annex" });
    await post(`${BASE}/annex/enrolments`, {});
    const [enrolled] = (await mockApi<OutpostInstanceList>(`${BASE}/annex/instances`)).items;
    expect((await post<Outpost>(`${BASE}/annex/pause`)).status).toBe("paused");
    expect((await post<Outpost>(`${BASE}/annex/resume`)).status).toBe("active");
    expect((await refusal(mockApi(`${BASE}/annex`, { method: "DELETE" }))).problem.code).toBe("outpost_not_revoked");

    const one = await post<OutpostInstance>(`${BASE}/annex/instances/${enrolled!.id}/revoke`);
    expect(one.status).toBe("revoked");
    expect((await mockApi<Outpost>(`${BASE}/annex`)).epoch).toBe(1);
    await post(`${BASE}/annex/instances/${enrolled!.id}/revoke`);
    expect((await mockApi<Outpost>(`${BASE}/annex`)).epoch).toBe(1);

    const revoked = await post<Outpost>(`${BASE}/annex/revoke`);
    expect([revoked.status, revoked.epoch]).toEqual(["revoked", 2]);
    for (const gesture of ["pause", "resume", "enrolments"]) {
      expect((await refusal(post(`${BASE}/annex/${gesture}`, {}))).problem.code).toBe("outpost_revoked");
    }
    await expect(mockApi(`${BASE}/annex`, { method: "DELETE" })).resolves.toBeUndefined();
    expect((await refusal(mockApi(`${BASE}/annex`))).status).toBe(404);
    expect((await refusal(mockApi(`${BASE}/annex/instances`))).status).toBe(404);
    expect((await refusal(post(`${BASE}/factory-floor/instances/nobody/revoke`))).status).toBe(404);
  });
});

describe("the demo's connectors at an outpost (ADR 0149)", () => {
  const declare = (name: string, config: Record<string, unknown>) =>
    post<OrgConnector>("/orgs/acme/connectors", { name, kind: "mcp", type: "mcp-outpost", config });

  it("offers the type with no secret field, and an outpost's connectors are those that route to it", async () => {
    const types = await mockApi<ConnectorType[]>("/connectors/types");
    const type = types.find((t) => t.type === "mcp-outpost")!;
    expect(type.secret_fields).toEqual([]);
    expect(Object.keys((type.config_schema as { properties: object }).properties)).toEqual(["outpost", "endpoint", "timeout_s"]);
    const { items } = await mockApi<OutpostList>(BASE);
    expect(items.find((o) => o.slug === "factory-floor")!.connectors).toEqual(["tracker-onsite"]);
    const branch = items.find((o) => o.slug === "branch-office")!;
    expect([branch.status, branch.online, branch.endpoints, branch.connectors]).toEqual(["active", false, [], ["erp-branch"]]);
  });

  it("refuses a declaration as the API does: an unknown outpost on its field, a paused one with its code", async () => {
    const unknown = await refusal(declare("nowhere-tools", { outpost: "nowhere", endpoint: "tracker" }));
    expect([unknown.status, unknown.problem.errors]).toEqual([422, [{ loc: ["body", "config", "outpost"], msg: expect.stringContaining("nowhere") }]]);
    expect((await refusal(declare("addressed", { outpost: "factory-floor", endpoint: "https://tracker.example.com" }))).status).toBe(422);
    expect((await refusal(declare("with-url", { outpost: "factory-floor", endpoint: "tracker", url: "https://x.example.com" }))).status).toBe(422);
    const paused = await refusal(declare("cold-tools", { outpost: "cold-store", endpoint: "sensors" }));
    expect([paused.status, paused.problem.code, paused.problem.outpost, paused.problem.endpoint]).toEqual([409, "outpost_paused", "cold-store", "sensors"]);
    const made = await declare("warehouse-onsite", { outpost: "factory-floor", endpoint: "warehouse" });
    expect(made.type).toBe("mcp-outpost");
    expect((await mockApi<Outpost>(`${BASE}/factory-floor`)).connectors).toContain("warehouse-onsite");
  });

  it("discovers through an outpost whose instance serves the alias, and says when none does", async () => {
    const diff = await post<ConnectorDiscovery>("/orgs/acme/connectors/warehouse-onsite/discover");
    expect(diff.added).toEqual(["stock_level", "reserve_stock"]);
    const offline = await refusal(post("/orgs/acme/connectors/erp-branch/discover"));
    expect([offline.status, offline.problem.code, offline.problem.outpost, offline.problem.endpoint]).toEqual([409, "outpost_offline", "branch-office", "erp"]);
  });

  it("says on an agent's access where a tool lives: one outpost online, one offline", async () => {
    const access = await mockApi<AgentAccess>("/orgs/acme/agents/onboarding-coordinator/access?project=billing-api");
    const where = Object.fromEntries(access.tools.filter((t) => t.outpost).map((t) => [t.name, t.outpost]));
    expect(where["tracker-onsite__search_issues"]).toEqual({ name: "factory-floor", endpoint: "tracker", status: "active", online: true });
    expect(where["erp-branch__stock_level"]).toEqual({ name: "branch-office", endpoint: "erp", status: "active", online: false });
    expect(where["tracker-onsite__create_issue"], "a forbidden operation is not reached").toBeUndefined();
    expect(access.tools.find((t) => t.name === "track_order")).not.toHaveProperty("outpost");
  });

  it("judges the two statements of a write's rule as the API does (S65-17c, ADR 0149 §6)", async () => {
    const rule = (operation: string, body: unknown) =>
      mockApi<ConnectorOperation>(`/orgs/acme/connectors/warehouse-onsite/operations/${operation}`, { method: "PATCH", body: JSON.stringify(body) });
    const neither = await refusal(rule("reserve_stock", { policy: "approval" }));
    expect([neither.status, neither.problem.errors?.map((e) => (e as { loc: string[] }).loc.at(-1))]).toEqual([422, ["platform_arguments", "in_doubt_accepted"]]);
    const keyless = await refusal(rule("reserve_stock", { policy: "approval", platform_arguments: ["action_key"] }));
    expect([keyless.status, keyless.problem.errors]).toEqual([422, [{ loc: ["body", "platform_arguments"], msg: expect.stringContaining("cannot hand it one") }]]);
    const read = await refusal(rule("stock_level", { in_doubt_accepted: true }));
    expect([read.status, read.problem.errors]).toEqual([422, [{ loc: ["body", "in_doubt_accepted"], msg: expect.stringContaining("nothing to accept") }]]);
    const opened = await rule("reserve_stock", { policy: "approval", in_doubt_accepted: true });
    expect([opened.policy, opened.in_doubt_accepted]).toEqual(["approval", true]);
    const withdrawn = await refusal(rule("reserve_stock", { in_doubt_accepted: null }));
    expect(withdrawn.status, "withdrawing the last statement of an open write").toBe(422);
    const closed = await rule("reserve_stock", { policy: "forbidden" });
    expect((await rule("reserve_stock", { in_doubt_accepted: null })).in_doubt_accepted).toBe(false);
    expect(closed.policy).toBe("forbidden");
  });
});
