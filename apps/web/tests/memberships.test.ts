import { describe, expect, it } from "vitest";
import { isMultiOrg, membershipGroups } from "@/lib/memberships";

const row = (org: string, role: "org_admin" | "project_owner" | "developer", project_slug?: string) =>
  ({ org, role, project_slug: project_slug ?? null }) as never;

describe("membershipGroups (S40-07)", () => {
  it("community: platform and project/<slug>, the organisation slug never shown", () => {
    const groups = membershipGroups(
      [row("northwind", "project_owner", "essai-it4it"), row("northwind", "org_admin"), row("northwind", "project_owner", "billing")],
      { multiOrg: false, currentOrg: "northwind" },
    );
    expect(groups).toHaveLength(1);
    expect(groups[0]!.org).toBeNull();
    expect(groups[0]!.lines).toEqual([
      { scope: "platform", role: "org_admin" },
      { scope: "project/billing", role: "project_owner" },
      { scope: "project/essai-it4it", role: "project_owner" },
    ]);
    expect(groups[0]!.lines.some((line) => line.scope.includes("northwind"))).toBe(false);
  });

  it("multi-organisation: one group per organisation, the current first, never 'platform'", () => {
    const groups = membershipGroups(
      [row("acme", "developer"), row("northwind", "org_admin"), row("northwind", "developer", "billing")],
      { multiOrg: true, currentOrg: "northwind" },
    );
    expect(groups.map((group) => group.org)).toEqual(["northwind", "acme"]);
    expect(groups[0]!.lines).toEqual([
      { scope: "organisation", role: "org_admin" },
      { scope: "project/billing", role: "contributor" },
    ]);
    expect(JSON.stringify(groups)).not.toContain("platform");
  });

  it("isMultiOrg: unknown counts as multi-organisation", () => {
    expect(isMultiOrg(undefined)).toBe(true);
    expect(isMultiOrg({ features: [] })).toBe(false);
    expect(isMultiOrg({ features: ["multi_org"] })).toBe(true);
  });

  it("project roles only: no platform line", () => {
    const groups = membershipGroups([row("northwind", "developer", "billing")], { multiOrg: false, currentOrg: "northwind" });
    expect(groups[0]!.lines.map((line) => line.scope)).toEqual(["project/billing"]);
  });
});
