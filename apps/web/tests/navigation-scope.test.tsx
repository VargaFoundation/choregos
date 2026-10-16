import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { projectGroups, projectTrail, scopeOf } from "@/components/coquille/navigation";

/**
 * The sidebar switches scope (S30-03, plan item 11): the organisation's pages at its level, a
 * project's sections inside it (with a project switcher), the administration's inside it. The
 * project's and the administration's tab rows are gone.
 */
const path = { current: "/" };
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "acme", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({
        items: [
          { slug: "billing-api", name: "Billing API" },
          { slug: "joiners", name: "Joiners and leavers" },
        ],
        meta: { has_more: false },
      }),
      project: vi.fn().mockResolvedValue({ slug: "billing-api", name: "Billing API" }),
      adminSections: vi.fn().mockResolvedValue([{ id: "scim", title: "SCIM" }]),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
    },
  };
});

function sidebar(pathname: string) {
  path.current = pathname;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BarreLaterale />
    </QueryClientProvider>,
  );
  return screen.getByRole("complementary", { name: "Sidebar" });
}

const labels = (list: HTMLElement) => within(list).getAllByRole("link").map((a) => a.textContent);

describe("the sidebar switches scope", () => {
  it("knows where it stands", () => {
    expect(scopeOf("/")).toEqual({ kind: "org" });
    expect(scopeOf("/agents/x")).toEqual({ kind: "org" });
    expect(scopeOf("/p/billing-api/board")).toEqual({ kind: "project", slug: "billing-api" });
    expect(scopeOf("/admin/members")).toEqual({ kind: "admin" });
  });

  it("inside a project, lists its sections in three groups, and no organisation entry", async () => {
    const bar = sidebar("/p/billing-api/board");
    expect(labels(within(bar).getByRole("list", { name: "Work" }))).toEqual(["Overview", "Board", "Inbox", "Trains", "Findings"]);
    expect(labels(within(bar).getByRole("list", { name: "Define" }))).toEqual(["Workflows", "Agents", "Knowledge", "Actions", "Memory"]);
    expect(labels(within(bar).getByRole("list", { name: "Configure" }))).toEqual(["Integrations", "Members", "Settings"]);
    expect(within(bar).queryByRole("link", { name: "Skills" })).toBeNull();
    expect(within(bar).getByRole("link", { name: "Board" })).toHaveAttribute("aria-current", "page");
    expect(within(bar).getByRole("link", { name: "Workflows" })).toHaveAttribute("href", "/p/billing-api/workflows");
    expect(within(bar).getByRole("link", { name: "Knowledge" })).toHaveAttribute("href", "/p/billing-api/knowledge");
    // The way back to the organisation, and a switcher listing the organisation's projects.
    expect(within(bar).getByRole("link", { name: /All of acme/ })).toHaveAttribute("href", "/");
    const switcher = within(bar).getByTestId("project-switcher");
    expect(await within(switcher).findByRole("link", { name: "Joiners and leavers" })).toHaveAttribute("href", "/p/joiners");
    expect(within(switcher).getByRole("link", { name: "Billing API" })).toHaveAttribute("aria-current", "true");
  });

  it("inside a project, Inbox is the project's inbox, not the organisation's (S39-04)", () => {
    const bar = sidebar("/p/billing-api/board");
    expect(within(bar).getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/p/billing-api/inbox");
    expect(within(bar).getByRole("link", { name: "Inbox" })).not.toHaveAttribute("aria-current");
  });

  it("on the project's inbox, that entry is the current page and the only one", () => {
    const bar = sidebar("/p/billing-api/inbox");
    expect(within(bar).getByRole("link", { name: "Inbox" })).toHaveAttribute("aria-current", "page");
    expect(bar.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(projectTrail("billing-api", "/p/billing-api/inbox")).toEqual([{ label: "Inbox" }]);
  });

  it("a project's base, opened inside the project, keeps the Knowledge entry active (S39-14)", () => {
    const bar = sidebar("/p/billing-api/knowledge/billing-runbooks");
    expect(within(bar).getByRole("link", { name: "Knowledge" })).toHaveAttribute("aria-current", "page");
    expect(within(bar).getByRole("link", { name: "Agents" })).not.toHaveAttribute("aria-current");
  });

  it("at the organisation's level, lists the organisation's pages", () => {
    const bar = sidebar("/skills");
    expect(labels(within(bar).getByRole("list", { name: "Catalogue" }))).toEqual(["Agents", "Skills", "Knowledge", "MCP servers", "AI clients"]);
    expect(within(bar).queryByTestId("project-switcher")).toBeNull();
  });

  it("inside the administration, lists its sections, a plugin's included", async () => {
    const bar = sidebar("/admin/members");
    const list = within(bar).getByRole("list", { name: "Administration" });
    expect(await within(list).findByRole("link", { name: "SCIM" })).toHaveAttribute("href", "/admin/x/scim");
    expect(within(list).getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
  });

  it("the trains section can be left out (S30-07 decides when)", () => {
    const work = projectGroups("billing-api", { trains: false })[0]!.entries.map((e) => e.label);
    expect(work).not.toContain("Trains");
    expect(projectGroups("billing-api")[0]!.entries.map((e) => e.label)).toContain("Trains");
  });

  it("the trail of a project page: its section, then what is below it", () => {
    expect(projectTrail("billing-api", "/p/billing-api")).toEqual([]);
    expect(projectTrail("billing-api", "/p/billing-api/board")).toEqual([{ label: "Board" }]);
    const deeper = projectTrail("billing-api", "/p/billing-api/workflows/default-simple/map")!;
    expect(deeper[0]).toEqual({ href: "/p/billing-api/workflows", label: "Workflows" });
    expect(deeper).toHaveLength(2);
    // A ticket and a run write their own trail.
    expect(projectTrail("billing-api", "/p/billing-api/items/w1")).toBeNull();
  });
});
