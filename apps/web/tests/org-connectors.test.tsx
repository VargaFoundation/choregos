import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { deliveryRows, groupConnectors } from "@/components/org-connectors";
import { ApiError } from "@/lib/api";
import { kindLabel } from "@/lib/connector-kinds";
import type { ConnectorDto, ConnectorType, OrgConnector } from "@/lib/types";

const { params } = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => params }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

const { annuaire, parc, fournisseur, outil, type } = vi.hoisted(() => ({
  annuaire: { name: "entra-acme", kind: "identity", type: "entra", operations: [] } as unknown as OrgConnector,
  parc: { name: "parc", kind: "mdm", type: "demo", operations: [] } as unknown as OrgConnector,
  fournisseur: { name: "supplier-agent", kind: "mcp", type: "mcp", operations: [] } as unknown as OrgConnector,
  outil: (kind: string, type: string, status = "ok") => ({ kind, type, status, config: {} }) as ConnectorDto,
  type: (kind: string, name: string) =>
    ({ kind, type: name, display: name, config_schema: {}, secret_fields: [] }) as unknown as ConnectorType,
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

describe("the connectors, sorted by what they are (S21-04)", () => {
  it("an MCP server is not a business system, even when it serves a supplier's agent", () => {
    expect(groupConnectors([annuaire, fournisseur, parc])).toEqual({ metier: [annuaire, parc], mcp: [fournisseur] });
  });

  it("a kind is read in plain words; an extension's keeps its name", () => {
    expect(kindLabel("identity")).toBe("directory (identity)");
    expect(kindLabel("mcp")).toBe("MCP server");
    expect(kindLabel("scm")).toBe("source code");
    expect(kindLabel("payroll")).toBe("payroll");
  });

  it("the delivery tools are read per project, in the order of the requirements, without what is not one", () => {
    const [row] = deliveryRows([{ slug: "billing-api", name: "Billing API" }], [
      { data: [outil("cd", "argocd", "error"), outil("tracker", "jira"), outil("notify", "slack")] },
    ]);
    expect(row!.tools).toEqual([
      { kind: "tracker", type: "jira", status: "ok" },
      { kind: "cd", type: "argocd", status: "error" },
    ]);
  });

  it("an unreadable project does not mask the others", () => {
    const rows = deliveryRows(
      [
        { slug: "a", name: "A" },
        { slug: "b", name: "B" },
      ],
      [{ error: new ApiError(403, {}) }, { data: [outil("scm", "github")] }],
    );
    expect(rows.map((l) => l.refusal)).toEqual(["No access", null]);
    expect(rows[1]!.tools).toHaveLength(1);
  });

  it("each section says what it contains, and declaring does not offer a project's delivery tools", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgConnectorsPage />
      </QueryClientProvider>,
    );
    const business = screen.getByTestId("business-section");
    const mcp = screen.getByTestId("section-mcp");
    expect(within(business).getByRole("heading", { name: /^Business systems \(API\) \(\d+\)$/ })).toBeInTheDocument();
    expect(await within(business).findByText(/entra-acme/)).toBeInTheDocument();
    expect(within(business).getByText(/parc/)).toBeInTheDocument();
    expect(within(business).queryByText(/supplier-agent/)).toBeNull();
    expect(await within(mcp).findByText(/supplier-agent/)).toBeInTheDocument();
    expect(mcp).toHaveTextContent("A supplier's agent served over MCP is declared here");
    // The delivery tools, read-only, and the refused project does not hide the other.
    expect(await screen.findByTestId("delivery-billing-api")).toHaveTextContent("Work tracker: jira");
    expect(await screen.findByText("No access")).toBeInTheDocument();
    // The choice of type: families, never a tracker (it is set in the project).
    await screen.findAllByRole("option", { name: /entra/ });
    const groups = [...document.querySelectorAll("optgroup")].map((g) => g.getAttribute("label"));
    expect(groups).toEqual(["Business systems (API)", "MCP servers"]);
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
    expect(callout).toHaveTextContent("Add from the library");
    expect(within(callout).getByRole("link", { name: "Browse MCP servers" })).toHaveAttribute("href", "/library/mcp-servers");
    expect(within(callout).getByRole("link", { name: "Browse connector types" })).toHaveAttribute("href", "/library/connectors");
    expect(screen.getByText(/Connectors this organisation has installed/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Project delivery tools — configured in each project" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Declare a connector" })).toBeInTheDocument();
  });

  it("a source's link marks a business system's card too (?connector=), not only an MCP server's", async () => {
    window.history.pushState({}, "", "/admin/connectors?connector=entra-acme");
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <OrgConnectorsPage />
        </QueryClientProvider>,
      );
      const card = await screen.findByTestId("connector-entra-acme");
      expect(card).toHaveAttribute("aria-current", "true");
      expect(card).toHaveAttribute("id", "connector-card-entra-acme");
      expect(screen.getByTestId("connector-parc")).not.toHaveAttribute("aria-current");
    } finally {
      window.history.pushState({}, "", "/admin/connectors");
    }
  });

  it("says the name's rule and keeps Declare off for a name the API refuses, not a bare 422", async () => {
    params.set("type", "documents/sharepoint");
    try {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <OrgConnectorsPage />
        </QueryClientProvider>,
      );
      await screen.findAllByRole("option", { name: /sharepoint/ });
      const name = screen.getByPlaceholderText("entra-acme");
      const declare = screen.getByRole("button", { name: "Declare" });
      fireEvent.change(name, { target: { value: "Globex Confluence" } });
      expect(name).toHaveAttribute("aria-invalid", "true");
      expect(name).toHaveAccessibleDescription(/Lowercase letters, digits and dashes/);
      expect(declare).toBeDisabled();
      fireEvent.change(name, { target: { value: "globex-confluence" } });
      expect(name).toHaveAttribute("aria-invalid", "false");
      expect(declare).toBeEnabled();
    } finally {
      params.delete("type");
    }
  });
});
