import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { BarreLaterale } from "@/components/coquille/barre-laterale";
import { CLE_DE_LA_BARRE } from "@/lib/preferences";
import { ThemeProvider } from "@/lib/theme";

const chemin = { courant: "/" };
vi.mock("next/navigation", () => ({ usePathname: () => chemin.courant }));
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

function rendre(pathname: string) {
  chemin.courant = pathname;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BarreLaterale />
    </QueryClientProvider>,
  );
  return screen.getByRole("complementary", { name: "Sidebar" });
}

describe("la barre latérale (S24-03, ADR 0043)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("range la navigation en trois groupes nommés", () => {
    const barre = rendre("/");
    const navigation = within(barre).getByRole("navigation", { name: "Main navigation" });
    const noms = (liste: string) =>
      within(within(navigation).getByRole("list", { name: liste }))
        .getAllByRole("link")
        .map((lien) => lien.textContent);
    expect(noms("Work")).toEqual(["Projects", "Inbox"]);
    expect(noms("Catalogue")).toEqual(["Agents", "Skills", "Knowledge", "Memories", "MCP servers", "Connector types"]);
    expect(noms("Organisation")).toEqual(["Integrations", "Spend", "Admin"]);
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
  ])("sur %s, une seule entrée est active : %s", (pathname, attendue) => {
    const barre = rendre(pathname);
    const actives = within(barre).getAllByRole("link").filter((lien) => lien.getAttribute("aria-current") === "page");
    expect(actives.map((lien) => lien.textContent)).toEqual([attendue]);
  });

  it("l'inbox compte ce qui attend une personne : un ticket et deux actions (S23-02)", async () => {
    const barre = rendre("/");
    const inbox = within(barre).getByRole("link", { name: /^Inbox/ });
    expect(inbox).toHaveAttribute("href", "/inbox");
    expect(await within(inbox).findByText(", 3 waiting")).toBeInTheDocument();
  });

  it("se replie en rail : le choix est gardé, posé sur <html>, et chaque icône a son infobulle", () => {
    const barre = rendre("/");
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
    const barre = rendre("/");
    const [premier, second] = within(barre).getAllByRole("button");
    expect(premier).toHaveAccessibleName("About");
    expect(second).toHaveAccessibleName(/Collapse sidebar/);
    fireEvent.click(within(barre).getByRole("button", { name: /Collapse sidebar/ }));
    expect(within(barre).getByRole("button", { name: "About" })).toHaveAttribute("title", "About");
  });

  it("un repli fait dans un autre onglet vaut ici aussi", () => {
    const barre = rendre("/");
    act(() => {
      window.localStorage.setItem(CLE_DE_LA_BARRE, "rail");
      window.dispatchEvent(new StorageEvent("storage", { key: CLE_DE_LA_BARRE }));
    });
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    expect(within(barre).getByRole("link", { name: "Skills" })).toHaveAttribute("title", "Skills");
  });

  it("un stockage refusé n'empêche pas de replier la barre", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const barre = rendre("/");
    fireEvent.click(within(barre).getByRole("button", { name: /Collapse sidebar/ }));
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    vi.restoreAllMocks();
  });

  it("la barre et le panneau du menu, ouverts ensemble : aucun identifiant en double", () => {
    // Sous 1280 px, la barre n'est que cachée : ses légendes restent dans la page quand le menu
    // s'ouvre, et chaque `aria-labelledby` doit viser la sienne.
    chemin.courant = "/";
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ThemeProvider>
          <BarreLaterale />
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
