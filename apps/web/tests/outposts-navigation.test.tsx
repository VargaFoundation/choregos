// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AdminLayout from "@/app/admin/layout";
import { adminGroups, adminTrail, sectionOf } from "@/components/shell/navigation";
import { Sidebar } from "@/components/shell/sidebar";

/**
 * Outposts in the administration's navigation: the entry is listed for whoever holds
 * `outpost:manage`, the organisation's administrator, and stays the current one on an outpost's page.
 */
const path = { current: "/admin" };
const session = vi.hoisted(() => ({ memberships: [] as { org: string; role: string; project_slug: string | null }[] }));
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: { memberships: session.memberships }, orgs: [], org: "acme", selectOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      adminSections: vi.fn().mockResolvedValue([]),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
    },
  };
});

const ADMIN = [{ org: "acme", role: "org_admin", project_slug: null }];

function within_(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

function administration(pathname: string, memberships: typeof session.memberships) {
  path.current = pathname;
  session.memberships = memberships;
  within_(<Sidebar />);
  return within(within(screen.getByRole("complementary", { name: "Sidebar" })).getByRole("list", { name: "Administration" }));
}

describe("Outposts in the administration", () => {
  it("is listed for the organisation's administrator, after Network", () => {
    const list = administration("/admin", ADMIN);
    const labels = list.getAllByRole("link").map((link) => link.textContent);
    expect(labels.slice(labels.indexOf("Network"), labels.indexOf("Network") + 2)).toEqual(["Network", "Outposts"]);
    expect(list.getByRole("link", { name: "Outposts" })).toHaveAttribute("href", "/admin/outposts");
  });

  it.each([
    ["an owner of the organisation's projects", [{ org: "acme", role: "project_owner", project_slug: null }]],
    ["a developer", [{ org: "acme", role: "developer", project_slug: null }]],
    ["the administrator of another organisation", [{ org: "globex", role: "org_admin", project_slug: null }]],
    ["nobody yet", []],
  ])("is not listed for %s", (_who, memberships) => {
    const list = administration("/admin", memberships);
    expect(list.queryByRole("link", { name: "Outposts" })).toBeNull();
    expect(list.getByRole("link", { name: "Network" })).toBeInTheDocument();
  });

  it("stays the current entry on one outpost's page, and is the only one", () => {
    const list = administration("/admin/outposts/factory-floor", ADMIN);
    expect(list.getByRole("link", { name: "Outposts" })).toHaveAttribute("aria-current", "page");
    expect(list.getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page")).toHaveLength(1);
  });

  it("the trail of a page below a section names the section, then the page as it is written", () => {
    const groups = adminGroups([], { outposts: true });
    const section = sectionOf("/admin/outposts/factory-floor", groups);
    expect(section?.label).toBe("Outposts");
    const trail = adminTrail("/admin/outposts/factory-floor", section);
    expect(trail).toHaveLength(2);
    expect(trail[0]).toEqual({ href: "/admin/outposts", label: "Outposts" });
    expect(adminTrail("/admin/outposts", sectionOf("/admin/outposts", groups))).toEqual([{ label: "Outposts" }]);
    expect(adminTrail("/admin", sectionOf("/admin", groups))).toEqual([]);
    expect(adminTrail("/admin/outposts", undefined)).toEqual([]);
    // Without the right, the address belongs to no section: the layout falls back to "Administration".
    expect(sectionOf("/admin/outposts", adminGroups([]))).toBeUndefined();
  });

  it("an outpost's page is titled Outposts, with the way back to the list and the outpost's slug", () => {
    path.current = "/admin/outposts/factory-floor";
    session.memberships = ADMIN;
    within_(
      <AdminLayout>
        <p>Content</p>
      </AdminLayout>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Outposts" })).toBeInTheDocument();
    const trail = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
    expect(trail.getByRole("link", { name: "Administration" })).toHaveAttribute("href", "/admin");
    expect(trail.getByRole("link", { name: "Outposts" })).toHaveAttribute("href", "/admin/outposts");
    // The slug is data: written as it is, and it is the current page of the trail.
    expect(trail.getByText("factory-floor")).toHaveAttribute("data-verbatim");
    expect(trail.getByText("factory-floor").closest("[aria-current]")).toHaveAttribute("aria-current", "page");
  });
});
