import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { entreeActive, NAV, TopNav } from "@/app/top-nav";

const chemin = { courant: "/" };
vi.mock("next/navigation", () => ({ usePathname: () => chemin.courant }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "varga", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
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

function rendre(pathname: string) {
  chemin.courant = pathname;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TopNav />
    </QueryClientProvider>,
  );
  return screen.getByRole("navigation", { name: "main navigation" });
}

describe("la navigation d'en-tête (S21-03)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("les skills ont leur propre entrée, active sur /skills et non sur /agents", () => {
    expect(NAV.map((entree) => entree.label)).toContain("skills");
    rendre("/skills/onboarding-procedure");
    expect(screen.getByRole("link", { name: "skills" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "agents" })).not.toHaveAttribute("aria-current");
  });

  it("AI clients mène à /integrations", () => {
    rendre("/integrations/cursor");
    const lien = screen.getByRole("link", { name: "AI clients" });
    expect(lien).toHaveAttribute("href", "/integrations");
    expect(lien).toHaveAttribute("aria-current", "page");
  });

  it("les projets restent actifs dans un projet, et une seule entrée l'est à la fois", () => {
    expect(entreeActive("/", "/p/billing-api/board")).toBe(true);
    for (const pathname of ["/", "/agents", "/skills", "/inbox", "/integrations", "/admin/connectors", "/p/x"]) {
      expect(NAV.filter((entree) => entreeActive(entree.href, pathname))).toHaveLength(1);
    }
  });

  it("se replie derrière « menu » : le panneau s'ouvre, se ferme sur Échap et rend le focus (S23-01)", () => {
    const navigation = rendre("/p/billing-api");
    const menu = screen.getByRole("button", { name: /^menu/ });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();

    fireEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const panneau = navigation.querySelector("#menu-principal");
    expect(panneau).not.toBeNull();
    // Les six entrées y sont, et celle de la page courante y est marquée.
    expect(panneau?.querySelectorAll("a")).toHaveLength(NAV.length + 1); // + « sign in »
    expect(panneau?.querySelector('a[aria-current="page"]')?.textContent).toBe("projects");

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();
    expect(menu).toHaveFocus();
  });

  it("l'inbox compte ce qui attend une personne : un ticket et deux actions (S23-02)", async () => {
    rendre("/");
    const inbox = screen.getByRole("link", { name: /^inbox/ });
    expect(inbox).toHaveAttribute("href", "/inbox");
    expect(await within(inbox).findByText(", 3 waiting")).toBeInTheDocument();
    // Replié, le bouton du menu le dit aussi.
    expect(await within(screen.getByRole("button", { name: /^menu/ })).findByText(", 3 waiting")).toBeInTheDocument();
  });
});
