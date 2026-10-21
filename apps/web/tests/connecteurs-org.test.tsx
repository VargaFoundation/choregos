import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { lignesDeLivraison, rangerLesConnecteurs } from "@/components/connecteurs-org";
import { ApiError } from "@/lib/api";
import { libelleDeSorte } from "@/lib/sortes-de-connecteurs";
import type { ConnectorDto, ConnectorType, OrgConnector } from "@/lib/types";

const { params } = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => params }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { annuaire, parc, fournisseur, outil, type } = vi.hoisted(() => ({
  annuaire: { name: "entra-acme", kind: "identity", type: "entra", operations: [] } as unknown as OrgConnector,
  parc: { name: "parc", kind: "mdm", type: "demo", operations: [] } as unknown as OrgConnector,
  fournisseur: { name: "supplier-agent", kind: "mcp", type: "mcp", operations: [] } as unknown as OrgConnector,
  outil: (kind: string, type: string, status = "ok") => ({ kind, type, status, config: {} }) as ConnectorDto,
  type: (kind: string, nom: string) =>
    ({ kind, type: nom, display: nom, config_schema: {}, secret_fields: [] }) as unknown as ConnectorType,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: {
      ...reel.api,
      orgConnectors: vi.fn().mockResolvedValue([annuaire, fournisseur, parc]),
      projects: vi.fn().mockResolvedValue({
        items: [
          { slug: "billing-api", name: "Billing API" },
          { slug: "rh", name: "HR" },
        ],
        meta: { has_more: false },
      }),
      connectors: vi.fn(async (slug: string) => {
        if (slug === "rh") throw new reel.ApiError(403, { title: "Forbidden" });
        return [outil("cd", "argocd", "error"), outil("tracker", "jira"), outil("notify", "slack")];
      }),
      connectorTypes: vi.fn().mockResolvedValue([type("tracker", "jira"), type("identity", "entra"), type("mcp", "mcp"), type("documents", "sharepoint")]),
    },
  };
});

describe("les connecteurs, rangés par ce qu'ils sont (S21-04)", () => {
  it("un serveur MCP n'est pas un système métier, même quand il sert l'agent d'un fournisseur", () => {
    expect(rangerLesConnecteurs([annuaire, fournisseur, parc])).toEqual({ metier: [annuaire, parc], mcp: [fournisseur] });
  });

  it("une sorte se lit en clair ; celle d'un greffon garde son nom", () => {
    expect(libelleDeSorte("identity")).toBe("directory (identity)");
    expect(libelleDeSorte("mcp")).toBe("MCP server");
    expect(libelleDeSorte("scm")).toBe("source code");
    expect(libelleDeSorte("payroll")).toBe("payroll");
  });

  it("les outils de livraison se lisent par projet, dans l'ordre des exigences, sans ce qui n'en est pas", () => {
    const [ligne] = lignesDeLivraison([{ slug: "billing-api", name: "Billing API" }], [
      { data: [outil("cd", "argocd", "error"), outil("tracker", "jira"), outil("notify", "slack")] },
    ]);
    expect(ligne!.outils).toEqual([
      { kind: "tracker", type: "jira", status: "ok" },
      { kind: "cd", type: "argocd", status: "error" },
    ]);
  });

  it("un projet illisible ne masque pas les autres", () => {
    const lignes = lignesDeLivraison(
      [
        { slug: "a", name: "A" },
        { slug: "b", name: "B" },
      ],
      [{ error: new ApiError(403, {}) }, { data: [outil("scm", "github")] }],
    );
    expect(lignes.map((l) => l.refus)).toEqual(["No access", null]);
    expect(lignes[1]!.outils).toHaveLength(1);
  });

  it("chaque section dit ce qu'elle contient, et déclarer ne propose pas les outils de livraison d'un projet", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const metier = screen.getByTestId("section-metier");
    const mcp = screen.getByTestId("section-mcp");
    expect(within(metier).getByRole("heading", { name: /^Business systems \(API\) \(\d+\)$/ })).toBeInTheDocument();
    expect(await within(metier).findByText(/entra-acme/)).toBeInTheDocument();
    expect(within(metier).getByText(/parc/)).toBeInTheDocument();
    expect(within(metier).queryByText(/supplier-agent/)).toBeNull();
    expect(await within(mcp).findByText(/supplier-agent/)).toBeInTheDocument();
    expect(mcp).toHaveTextContent("A supplier's agent served over MCP is declared here");
    // Les outils de livraison, en lecture, et le projet refusé ne cache pas l'autre.
    expect(await screen.findByTestId("livraison-billing-api")).toHaveTextContent("Work tracker: jira");
    expect(await screen.findByText("No access")).toBeInTheDocument();
    // Le choix du type : des familles, jamais un tracker (il se règle dans le projet).
    await screen.findAllByRole("option", { name: /entra/ });
    const groupes = [...document.querySelectorAll("optgroup")].map((g) => g.getAttribute("label"));
    expect(groupes).toEqual(["Business systems (API)", "MCP servers"]);
    expect(screen.queryByRole("option", { name: /jira/ })).toBeNull();
  });

  it("?type=documents/sharepoint preselects the type to declare (the knowledge form links here)", async () => {
    params.set("type", "documents/sharepoint");
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <OrgConnectorsPage />
        </QueryClientProvider>,
      );
      await screen.findAllByRole("option", { name: /sharepoint/ });
      const select = screen.getByDisplayValue(/sharepoint/) as HTMLSelectElement;
      expect(select.value).toBe("documents/sharepoint");
    } finally {
      params.delete("type");
    }
  });

  it("names the page Installed connectors and points to the catalogues instead of a Catalogue line (S42-06)", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Installed connectors" })).toBeInTheDocument();
    expect(screen.queryByText(/Catalogue:/)).toBeNull();
    const callout = screen.getByTestId("add-from-catalogues");
    expect(callout).toHaveTextContent("Add from the catalogues");
    expect(within(callout).getByRole("link", { name: "Browse MCP servers" })).toHaveAttribute("href", "/catalogue/mcp-servers");
    expect(within(callout).getByRole("link", { name: "Browse connector types" })).toHaveAttribute("href", "/catalogue/connectors");
    expect(screen.getByText(/Connectors this organisation has installed/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Project delivery tools — configured in each project" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Declare a connector" })).toBeInTheDocument();
  });
});
