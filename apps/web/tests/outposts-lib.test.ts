// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { canManageOutposts } from "@/lib/memberships";
import {
  CALL_STATES,
  callSubject,
  controlPlaneLine,
  deploymentRefusal,
  doorKeysLine,
  enrolCommand,
  INSTANCE_LABEL,
  outpostConfigProblems,
  policyLines,
  presence,
  reachability,
  refusalOf,
  retryIn,
  routeRefusal,
  stateLabel,
  timeoutBounds,
} from "@/lib/outposts";
import type { Membership } from "@/lib/types";

const KEYS = ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw", "kPu0v5q0mTqH1cQ7m9cN2rY8wXhJ4bS6dF3gA1zL0eE"];

describe("what an outpost's operator pastes (ADR 0148 §13)", () => {
  it("the policy file's lines: the door's origin, then the keys to pin, the one that signs first", () => {
    expect(policyLines("https://outposts.example.com", KEYS)).toBe(
      `control_plane: https://outposts.example.com\ndoor_keys: ["${KEYS[0]}", "${KEYS[1]}"]`,
    );
    expect(doorKeysLine(KEYS.slice(0, 1))).toBe(`door_keys: ["${KEYS[0]}"]`);
  });

  it("a deployment that declares no origin gets a comment, never a value that would be pasted", () => {
    for (const none of [null, undefined, ""]) {
      const line = controlPlaneLine(none);
      expect(line.startsWith("# control_plane:")).toBe(true);
      expect(line).toContain("declares no origin");
    }
    expect(policyLines(null, KEYS).split("\n")).toHaveLength(2);
  });

  it("the enrolment command is the daemon's, with the token", () => {
    expect(enrolCommand("choe_abc")).toBe("choregos-outpost enrol --token choe_abc");
  });
});

describe("where an outpost stands, in words", () => {
  afterEach(() => vi.useRealTimers());

  it("online, last seen, or never seen", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-12T10:00:00Z"));
    expect(presence({ online: true, last_seen_at: "2026-11-12T09:59:50Z" })).toEqual({ online: true, label: "Online" });
    expect(presence({ online: false, last_seen_at: "2026-11-12T07:00:00Z" })).toEqual({ online: false, label: "Last seen 3 hours ago" });
    expect(presence({ online: false, last_seen_at: null })).toEqual({ online: false, label: "Never seen" });
    expect(presence({ online: false })).toEqual({ online: false, label: "Never seen" });
  });

  it("a state reads in words, and an approved instance is called approved", () => {
    expect(CALL_STATES.map(stateLabel)).toEqual(["Pending", "Claimed", "Answered", "Refused", "Expired", "Timed out", "In doubt"]);
    expect(INSTANCE_LABEL).toEqual({ pending: "Pending", active: "Approved", revoked: "Revoked" });
    expect(callSubject({ kind: "call_tool", tool: "search_issues" })).toBe("search_issues");
    expect(callSubject({ kind: "list_tools", tool: null })).toBe("Tool listing");
  });
});

describe("who manages outposts (`outpost:manage`)", () => {
  it("the organisation's administrator, in that organisation, and nobody else", () => {
    const of = (role: Membership["role"], org = "acme", project_slug: string | null = null) => [{ org, role, project_slug }];
    expect(canManageOutposts(of("org_admin"), "acme")).toBe(true);
    expect(canManageOutposts(of("org_admin", "globex"), "acme")).toBe(false);
    expect(canManageOutposts(of("org_admin", "acme", "billing-api"), "acme")).toBe(false);
    for (const role of ["project_owner", "release_captain", "developer", "viewer"] as const) {
      expect(canManageOutposts(of(role), "acme")).toBe(false);
    }
    expect(canManageOutposts([], "acme")).toBe(false);
  });
});

describe("the API's refusal, read as it is", () => {
  it("keeps the code, the API's own sentence and the connectors it names", () => {
    const refused = refusalOf(
      new ApiError(409, { detail: "`factory-floor` still has connectors that route to it", code: "outpost_in_use", connectors: ["tracker"] }),
    );
    expect(refused).toEqual({ status: 409, code: "outpost_in_use", detail: "`factory-floor` still has connectors that route to it", connectors: ["tracker"] });
    expect(deploymentRefusal(refused)).toBeNull();
    expect(refusalOf(new ApiError(403, { detail: "the outposts of acme are managed by its administrator" }))).toMatchObject({ status: 403, code: null, connectors: [] });
    expect(refusalOf(new Error("network down")).detail).toBe("network down");
    expect(refusalOf("?").detail).toBe("refused");
  });

  it("says which of the two refusals of the deployment it is", () => {
    const off = refusalOf(new ApiError(409, { detail: "outposts are switched off on this deployment", code: "outposts_disabled" }));
    const noKey = refusalOf(new ApiError(409, { detail: "the outposts' door has no signing key on this deployment", code: "door_key_missing" }));
    expect(deploymentRefusal(off)).toBe("Outposts are switched off on this deployment");
    expect(deploymentRefusal(noKey)).toBe("The outposts' door has no signing key on this deployment");
    expect(deploymentRefusal(off)).not.toBe(deploymentRefusal(noKey));
  });
});

describe("a connector at an outpost (ADR 0149)", () => {
  it("reads a refusal of its route: the outpost, the alias, the outpost's own code, when to ask again", () => {
    const refused = refusalOf(
      new ApiError(409, { detail: "the outpost `factory-floor` refused the call", code: "outpost_refused", outpost: "factory-floor", endpoint: "tracker", refusal: "write_denied" }),
    );
    expect(refused).toMatchObject({ code: "outpost_refused", outpost: "factory-floor", endpoint: "tracker", refusal: "write_denied" });
    expect(routeRefusal(refused)).toBe("The outpost refused the call");
    const busy = refusalOf(new ApiError(429, { detail: "too many calls", retry_after: 45 }));
    expect([busy.retryAfter, routeRefusal(busy)]).toEqual([45, "Too many calls to the outpost"]);
    expect(refusalOf(new ApiError(409, { detail: "x", retry_after: 45 })).retryAfter).toBeUndefined();
    for (const [code, title] of [
      ["outpost_paused", "The outpost is handed nothing"],
      ["outpost_offline", "The outpost is offline"],
      ["outpost_timeout", "The outpost did not answer in time"],
    ] as const) {
      expect(routeRefusal(refusalOf(new ApiError(409, { detail: "x", code })))).toBe(title);
    }
    // The refusals of the outposts' own routes are not the route's: shown as the API wrote them.
    expect(routeRefusal(refusalOf(new ApiError(409, { detail: "x", code: "outpost_in_use" })))).toBeNull();
    expect(routeRefusal(refusalOf(new Error("down")))).toBeNull();
  });

  it("reads the fields a 422 names in the configuration, and only those", () => {
    const named = refusalOf(
      new ApiError(422, {
        detail: "not an outpost",
        errors: [{ loc: ["body", "config", "outpost"], msg: "not an outpost of acme" }, { loc: ["body", "name"], msg: "taken" }, { loc: ["body", "config", "endpoint"] }, "?"],
      }),
    );
    expect(named.fields).toEqual({ outpost: "not an outpost of acme", endpoint: "refused" });
    expect(refusalOf(new ApiError(422, { detail: "no field" })).fields).toBeUndefined();
    expect(refusalOf(new ApiError(409, { detail: "x", errors: [{ loc: ["body", "config", "outpost"], msg: "m" }] })).fields).toBeUndefined();
  });

  it("says when to try again", () => {
    expect(retryIn(1)).toBe("Try again in 1 second.");
    expect(retryIn(30)).toBe("Try again in 30 seconds.");
    expect(retryIn(60)).toBe("Try again in 1 minute.");
    expect(retryIn(90)).toBe("Try again in 2 minutes.");
  });

  it("checks an alias and a timeout before the API does, by the type's own bounds", () => {
    const schema = { properties: { timeout_s: { type: "number", minimum: 1, maximum: 120, default: 30 } } };
    expect(timeoutBounds(schema)).toEqual({ min: 1, max: 120, fallback: 30 });
    expect(timeoutBounds(undefined)).toEqual({ min: undefined, max: undefined, fallback: undefined });
    expect(outpostConfigProblems({ outpost: "factory-floor", endpoint: "tracker", timeout_s: 30 }, schema)).toEqual({});
    expect(Object.keys(outpostConfigProblems({ endpoint: "https://tracker.example.com/mcp" }, schema))).toEqual(["endpoint"]);
    expect(Object.keys(outpostConfigProblems({ endpoint: "Tracker" }, schema))).toEqual(["endpoint"]);
    expect(outpostConfigProblems({ endpoint: "" }, schema)).toEqual({});
    expect(outpostConfigProblems({ timeout_s: 0 }, schema).timeout_s).toBe("A number of seconds between 1 and 120.");
    expect(Object.keys(outpostConfigProblems({ timeout_s: Number.NaN }, schema))).toEqual(["timeout_s"]);
    expect(outpostConfigProblems({ timeout_s: 500 }, {}).timeout_s).toBeUndefined();
    expect(outpostConfigProblems({ timeout_s: "soon" }, {}).timeout_s).toBe("A number of seconds.");
  });

  it("says whether a tool at an outpost answers now, and why not when the outpost is handed nothing", () => {
    expect(reachability({ name: "factory-floor", endpoint: "tracker", status: "active", online: true })).toEqual([{ label: "Online", tone: "ok" }]);
    expect(reachability({ name: "lab", endpoint: "forge", status: "active", online: false })).toEqual([{ label: "Offline", tone: "warn" }]);
    expect(reachability({ name: "lab", endpoint: "forge", status: "revoked", online: false }).map((s) => s.label)).toEqual(["Offline", "Revoked"]);
    expect(reachability({ name: null, endpoint: "forge", status: null, online: false }).map((s) => s.label)).toEqual(["Offline"]);
  });
});
