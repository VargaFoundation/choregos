import { describe, expect, it } from "vitest";
import { canManageMembers } from "@/lib/memberships";
import { initiatorInWords } from "@/lib/who-writes";

describe("who may give an application its account (member:manage, S62-11)", () => {
  it("an organisation administrator, a project owner of the organisation or of that project", () => {
    expect(canManageMembers([{ org: "acme", role: "org_admin" }], "acme", "billing-api")).toBe(true);
    expect(canManageMembers([{ org: "acme", role: "project_owner" }], "acme", "billing-api")).toBe(true);
    expect(canManageMembers([{ org: "acme", role: "project_owner", project_slug: "billing-api" }], "acme", "billing-api")).toBe(true);
  });

  it("not a developer, not a project owner elsewhere, not another organisation's administrator", () => {
    expect(canManageMembers([{ org: "acme", role: "developer" }], "acme", "billing-api")).toBe(false);
    expect(canManageMembers([{ org: "acme", role: "project_owner", project_slug: "checkout" }], "acme", "billing-api")).toBe(false);
    expect(canManageMembers([{ org: "other", role: "org_admin" }], "acme", "billing-api")).toBe(false);
  });
});

describe("a work item an application started says so (ADR 0131)", () => {
  it("names the application, not its reserved address", () => {
    expect(initiatorInWords("machine:partner-app.p1@machines.choregos.invalid")).toBe("the application partner-app");
  });
});
