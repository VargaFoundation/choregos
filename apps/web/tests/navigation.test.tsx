import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { isActiveEntry, GROUPS, NAV, projectGroups } from "@/components/shell/navigation";
import { ThemeProvider } from "@/lib/theme";

const route = { current: "/" };
vi.mock("next/navigation", () => ({ usePathname: () => route.current }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "acme", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      edition: vi.fn().mockResolvedValue({ edition: "community" }),
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "billing-api" }], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({
        items: [{ id: "w2", pending_request: { id: "hr", kind: "approval", payload: {}, requested_at: "2026-10-08T10:00:00Z" } }],
        meta: { has_more: false },
      }),
      orgActions: vi.fn().mockResolvedValue([{ id: "a1" }, { id: "a2" }]),
    },
  };
});

function mount(pathname: string) {
  route.current = pathname;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider>
        <TopNav />
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return screen.getByRole("navigation", { name: "Main navigation" });
}

describe("la navigation (S21-03, S24-03)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("each entry is placed in a group, once; skills have their own", () => {
    expect(NAV.map((entree) => entree.label)).toContain("Skills");
    const rangees = GROUPS.flatMap((groupe) => groupe.entrees);
    expect([...rangees].sort()).toEqual(NAV.map((entree) => entree.href).sort());
  });

  it("Integrations is not in the Catalogue group and is named like the project's entry (S40-08)", () => {
    const catalogue = GROUPS.find((groupe) => groupe.titre === "Catalogue")!;
    expect(catalogue.entrees).not.toContain("/integrations");
    const organisation = GROUPS.find((groupe) => groupe.titre === "Organisation")!;
    expect(organisation.entrees[0]).toBe("/integrations");
    const project = projectGroups("x")
      .flatMap((groupe) => groupe.entries)
      .find((entree) => entree.href.endsWith("/integrations"))!;
    expect(NAV.find((entree) => entree.href === "/integrations")!.label).toBe(project.label);
  });

  it("projects stay active inside a project, and a single entry is active at a time", () => {
    expect(isActiveEntry("/", "/p/billing-api/board")).toBe(true);
    for (const pathname of ["/", "/agents", "/skills", "/inbox", "/integrations", "/admin/connectors", "/p/x"]) {
      expect(NAV.filter((entree) => isActiveEntry(entree.href, pathname))).toHaveLength(1);
    }
  });

  it("below 1280 px, folds behind \"menu\": the panel opens, closes on Escape and gives back the focus (S23-01)", () => {
    const navigation = mount("/");
    const menu = screen.getByRole("button", { name: /^Menu/ });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();

    fireEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const panel = navigation.querySelector<HTMLElement>("#menu-principal");
    expect(panel).not.toBeNull();
    // The six entries are there, in their groups, and the current page's one is marked.
    expect(panel?.querySelectorAll("a")).toHaveLength(NAV.length + 1); // + « sign in »
    expect(panel?.querySelector('a[aria-current="page"]')?.textContent).toBe("Projects");
    expect(within(panel!).getByRole("list", { name: "Catalogue" })).toBeInTheDocument();
    // The theme choice is there too: below 1280 px, the header no longer shows it elsewhere.
    expect(within(panel!).getByRole("group", { name: "Theme" })).toBeInTheDocument();

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();
    expect(menu).toHaveFocus();
  });

  it("the menu button counts what waits for a person: one ticket and two actions (S23-02)", async () => {
    mount("/");
    expect(await within(screen.getByRole("button", { name: /^Menu/ })).findByText(", 3 waiting")).toBeInTheDocument();
  });

  it("the header's two theme choices are two distinct groups", () => {
    mount("/");
    fireEvent.click(screen.getByRole("button", { name: /^Menu/ }));
    // The header (from 1280 px) and the panel: the same name would make only one group.
    const groups = screen.getAllByRole("group", { name: "Theme" });
    expect(groups).toHaveLength(2);
    const names = groups.map((groupe) => within(groupe).getAllByRole("radio")[0]!.getAttribute("name"));
    expect(new Set(names).size).toBe(2);
    fireEvent.click(within(groups[1]!).getByRole("radio", { name: "Light" }));
    for (const group of groups) expect(within(group).getByRole("radio", { name: "Light" })).toBeChecked();
  });
});
