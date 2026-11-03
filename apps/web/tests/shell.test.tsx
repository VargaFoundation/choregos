import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { Sidebar } from "@/components/shell/sidebar";
import { CLE_DE_LA_BARRE } from "@/lib/preferences";
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
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "billing-api" }], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({
        items: [{ id: "w2", pending_request: { id: "hr", kind: "approval", payload: {}, requested_at: "2026-10-08T10:00:00Z" } }],
        meta: { has_more: false },
      }),
      orgActions: vi.fn().mockResolvedValue([{ id: "a1" }, { id: "a2" }]),
      edition: vi.fn().mockResolvedValue({ edition: "community" }),
    },
  };
});

function mount(pathname: string) {
  route.current = pathname;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Sidebar />
    </QueryClientProvider>,
  );
  return screen.getByRole("complementary", { name: "Sidebar" });
}

describe("the sidebar (S24-03, ADR 0043)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("arranges the navigation in three named groups", () => {
    const barre = mount("/");
    const navigation = within(barre).getByRole("navigation", { name: "Main navigation" });
    const names = (liste: string) =>
      within(within(navigation).getByRole("list", { name: liste }))
        .getAllByRole("link")
        .map((lien) => lien.textContent);
    expect(names("Work")).toEqual(["Projects", "Inbox"]);
    expect(names("Catalogue")).toEqual(["Agents", "Skills", "Knowledge", "Memories", "MCP servers", "Connector types", "Plugins"]);
    expect(names("Organisation")).toEqual(["Integrations", "Spend", "Admin"]);
  });

  it.each([
    ["/p/billing-api/board", "Board"],
    ["/p/billing-api/items/w1", "Board"],
    ["/p/billing-api/workflows/default-simple/map", "Workflows"],
    ["/p/billing-api", "Overview"],
    ["/skills/onboarding-procedure", "Skills"],
    ["/integrations/cursor", "Integrations"],
    ["/admin/connectors", "Installed connectors"],
    ["/admin", "Overview"],
  ])("on %s, a single entry is active: %s", (pathname, expected) => {
    const barre = mount(pathname);
    const actives = within(barre).getAllByRole("link").filter((lien) => lien.getAttribute("aria-current") === "page");
    expect(actives.map((lien) => lien.textContent)).toEqual([expected]);
  });

  it("the inbox counts what waits for a person: one ticket and two actions (S23-02)", async () => {
    const barre = mount("/");
    const inbox = within(barre).getByRole("link", { name: /^Inbox/ });
    expect(inbox).toHaveAttribute("href", "/inbox");
    expect(await within(inbox).findByText(", 3 waiting")).toBeInTheDocument();
  });

  it("folds into a rail: the choice is kept, set on <html>, and each icon has its tooltip", () => {
    const barre = mount("/");
    const skills = within(barre).getByRole("link", { name: "Skills" });
    expect(skills).not.toHaveAttribute("title");

    fireEvent.click(within(barre).getByRole("button", { name: /Collapse sidebar/ }));
    expect(window.localStorage.getItem(CLE_DE_LA_BARRE)).toBe("rail");
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    expect(skills).toHaveAttribute("title", "Skills");

    fireEvent.click(within(barre).getByRole("button", { name: /Expand sidebar/ }));
    expect(window.localStorage.getItem(CLE_DE_LA_BARRE)).toBe("full");
    expect(document.documentElement.dataset.sidebar).toBeUndefined();
    expect(skills).not.toHaveAttribute("title");
  });

  it("About comes before the collapse button and stays a named button once folded (S42-12)", () => {
    const barre = mount("/");
    const [premier, second] = within(barre).getAllByRole("button");
    expect(premier).toHaveAccessibleName("About");
    expect(second).toHaveAccessibleName(/Collapse sidebar/);
    fireEvent.click(within(barre).getByRole("button", { name: /Collapse sidebar/ }));
    expect(within(barre).getByRole("button", { name: "About" })).toHaveAttribute("title", "About");
  });

  it("a fold made in another tab applies here too", () => {
    const barre = mount("/");
    act(() => {
      window.localStorage.setItem(CLE_DE_LA_BARRE, "rail");
      window.dispatchEvent(new StorageEvent("storage", { key: CLE_DE_LA_BARRE }));
    });
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    expect(within(barre).getByRole("link", { name: "Skills" })).toHaveAttribute("title", "Skills");
  });

  it("a refused storage does not prevent folding the bar", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const barre = mount("/");
    fireEvent.click(within(barre).getByRole("button", { name: /Collapse sidebar/ }));
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    vi.restoreAllMocks();
  });

  it("the bar and the menu panel, open together: no duplicate identifier", () => {
    // Below 1280 px, the bar is only hidden: its captions stay in the page when the menu
    // opens, and each `aria-labelledby` must point to its own.
    route.current = "/";
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ThemeProvider>
          <Sidebar />
          <TopNav />
        </ThemeProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Menu/ }));
    const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
    expect(ids.filter((id, rang) => ids.indexOf(id) !== rang)).toEqual([]);
    expect(screen.getAllByRole("list", { name: "Work" })).toHaveLength(2);
  });
});
