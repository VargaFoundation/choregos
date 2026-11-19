// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { creatableScopes } from "@/components/agents/registry";
import { rank, roleLabel } from "@/components/members";
import {
  acceptanceRate,
  changeRequestOf,
  inChangeRequest,
  needsWorkspace,
  workspaceFixed,
  workspaceOf,
  writesWorkspace,
} from "@/lib/neutral-names";
import { neutralRole } from "@/lib/roles";

const PR = "https://github.com/northwind/billing-api/pull/7";

describe("C1's neutral names (ADR 0181), read first, the former ones until 0.30.0", () => {
  it("reads a role under either spelling", () => {
    expect(neutralRole("developer")).toBe("contributor");
    expect(neutralRole("release_captain")).toBe("approver");
    expect(neutralRole("approver")).toBe("approver");
    expect(roleLabel("release_captain")).toBe("Approver");
    expect(rank("developer")).toBe(rank("contributor"));
    expect(rank("release_captain")).toBeGreaterThan(rank("contributor"));
    expect(creatableScopes([{ org: "acme", role: "approver" }], "acme").personal).toBe(true);
    expect(creatableScopes([{ org: "acme", role: "viewer" }], "acme").personal).toBe(false);
  });

  it("reads the change request from the link, else from the former field", () => {
    expect(changeRequestOf({ links: [{ rel: "change_request", url: PR }], pr_url: null })).toBe(PR);
    expect(changeRequestOf({ links: [], pr_url: PR })).toBe(PR);
    expect(changeRequestOf({ links: [{ rel: "other", url: "x" }] })).toBeNull();
    expect(changeRequestOf(null)).toBeNull();
  });

  it("reads each renamed field, the neutral name first", () => {
    expect(workspaceOf({ workspace: "a", repository: "b" })).toBe("a");
    expect(workspaceOf({ repository: "b" })).toBe("b");
    expect(workspaceFixed({ repository_fixed: true })).toBe(true);
    expect(workspaceFixed({ workspace_fixed: false, repository_fixed: true })).toBe(false);
    expect(needsWorkspace({ needs_repository: true })).toBe(true);
    expect(needsWorkspace({ needs_workspace: false, needs_repository: true })).toBe(false);
    expect(writesWorkspace({ writes_branch: true })).toBe(true);
    expect(writesWorkspace(undefined)).toBe(false);
    expect(inChangeRequest({ in_pull_request: true })).toBe(true);
    expect(acceptanceRate({ first_pass_merge_rate: 0.5 })).toBe(0.5);
    expect(acceptanceRate({ first_pass_acceptance_rate: 0.25, first_pass_merge_rate: 0.5 })).toBe(0.25);
  });
});
