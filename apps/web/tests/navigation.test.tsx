import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { entreeActive, NAV, TopNav } from "@/app/top-nav";

const chemin = { courant: "/" };
vi.mock("next/navigation", () => ({ usePathname: () => chemin.courant }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: null, orgs: [], org: "varga", choisirOrg: vi.fn(), deconnecter: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, api: { ...reel.api, edition: vi.fn().mockResolvedValue({ edition: "community" }) } };
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
    for (const pathname of ["/", "/agents", "/skills", "/approvals", "/integrations", "/admin/connectors", "/p/x"]) {
      expect(NAV.filter((entree) => entreeActive(entree.href, pathname))).toHaveLength(1);
    }
  });
});
