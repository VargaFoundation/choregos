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
  policyLines,
  presence,
  refusalOf,
  stateLabel,
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
