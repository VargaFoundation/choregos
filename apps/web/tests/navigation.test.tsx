import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopNav } from "@/app/top-nav";
import { entreeActive, GROUPES, NAV } from "@/components/coquille/navigation";
import { ThemeProvider } from "@/lib/theme";

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
      <ThemeProvider>
        <TopNav />
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return screen.getByRole("navigation", { name: "main navigation" });
}

describe("la navigation (S21-03, S24-03)", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("chaque entrée est rangée dans un groupe, une seule fois ; les skills ont la leur", () => {
    expect(NAV.map((entree) => entree.label)).toContain("skills");
    const rangees = GROUPES.flatMap((groupe) => groupe.entrees);
    expect([...rangees].sort()).toEqual(NAV.map((entree) => entree.href).sort());
  });

  it("les projets restent actifs dans un projet, et une seule entrée l'est à la fois", () => {
    expect(entreeActive("/", "/p/billing-api/board")).toBe(true);
    for (const pathname of ["/", "/agents", "/skills", "/inbox", "/integrations", "/admin/connectors", "/p/x"]) {
      expect(NAV.filter((entree) => entreeActive(entree.href, pathname))).toHaveLength(1);
    }
  });

  it("sous 1280 px, se replie derrière « menu » : le panneau s'ouvre, se ferme sur Échap et rend le focus (S23-01)", () => {
    const navigation = rendre("/p/billing-api");
    const menu = screen.getByRole("button", { name: /^menu/ });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();

    fireEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    const panneau = navigation.querySelector<HTMLElement>("#menu-principal");
    expect(panneau).not.toBeNull();
    // Les six entrées y sont, dans leurs groupes, et celle de la page courante y est marquée.
    expect(panneau?.querySelectorAll("a")).toHaveLength(NAV.length + 1); // + « sign in »
    expect(panneau?.querySelector('a[aria-current="page"]')?.textContent).toBe("projects");
    expect(within(panneau!).getByRole("list", { name: "catalogue" })).toBeInTheDocument();
    // Le choix du thème y est aussi : sous 1280 px, l'en-tête ne le montre plus ailleurs.
    expect(within(panneau!).getByRole("group", { name: "theme" })).toBeInTheDocument();

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(navigation.querySelector("#menu-principal")).toBeNull();
    expect(menu).toHaveFocus();
  });

  it("le bouton du menu compte ce qui attend une personne : un ticket et deux actions (S23-02)", async () => {
    rendre("/");
    expect(await within(screen.getByRole("button", { name: /^menu/ })).findByText(", 3 waiting")).toBeInTheDocument();
  });

  it("les deux choix du thème de l'en-tête sont deux groupes distincts", () => {
    rendre("/");
    fireEvent.click(screen.getByRole("button", { name: /^menu/ }));
    // L'en-tête (à partir de 1280 px) et le panneau : un même nom n'en ferait qu'un groupe.
    const groupes = screen.getAllByRole("group", { name: "theme" });
    expect(groupes).toHaveLength(2);
    const noms = groupes.map((groupe) => within(groupe).getAllByRole("radio")[0]!.getAttribute("name"));
    expect(new Set(noms).size).toBe(2);
    fireEvent.click(within(groupes[1]!).getByRole("radio", { name: "light" }));
    for (const groupe of groupes) expect(within(groupe).getByRole("radio", { name: "light" })).toBeChecked();
  });
});
