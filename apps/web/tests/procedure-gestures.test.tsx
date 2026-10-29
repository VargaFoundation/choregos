import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgConnectorsPage from "@/app/admin/connectors/page";
import { Connecteurs } from "@/components/connecteurs";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { proceduresApi } from "@/mocks/procedures";
import type { ConnectorDto, ConnectorType, OrgConnector } from "@/lib/types";

const { params } = vi.hoisted(() => ({ params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/admin/connectors", useSearchParams: () => params }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const { api } = vi.hoisted(() => ({
  api: {
    orgConnectors: vi.fn(),
    connectorTypes: vi.fn(),
    projects: vi.fn(),
    connectors: vi.fn(),
    projectRequirements: vi.fn(),
    createKnowledgeSource: vi.fn(),
    listProcedures: vi.fn(),
    getProcedure: vi.fn(),
  },
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const type = (kind: string, name: string, display: string, secrets: string[] = []) =>
  ({ kind, type: name, display, config_schema: { type: "object", properties: { url: { type: "string" } } }, secret_fields: secrets, capabilities: [kind] }) as unknown as ConnectorType;

const wrap = (ui: React.ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  document.body.className = "";
  api.listProcedures.mockImplementation(async (query = {}) => {
    const search = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
    return proceduresApi("GET", `/procedures?${search}`);
  });
  api.getProcedure.mockImplementation(async (id: string) => proceduresApi("GET", `/procedures/${id}`));
  api.orgConnectors.mockResolvedValue([]);
  api.connectorTypes.mockResolvedValue([
    type("documents", "sharepoint", "SharePoint", ["client_secret"]),
    type("mcp", "mcp", "MCP server", ["token"]),
    type("tracker", "jira", "Jira Cloud", ["api_token"]),
  ]);
  api.projects.mockResolvedValue({ items: [], meta: { has_more: false } });
});

describe("the procedure at every add gesture (S39-23b)", () => {
  it("declaring a connector: choosing SharePoint opens its procedure at the first section", async () => {
    wrap(<OrgConnectorsPage />);
    await screen.findByRole("option", { name: /SharePoint/ });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "documents/sharepoint" } });
    const panel = await screen.findByTestId("procedure-panel");
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("Connect SharePoint and OneDrive");
    expect(within(panel).getAllByRole("heading", { level: 3 })[0]).toHaveTextContent("Before you start");
    // The form is still there beside it, not hidden behind a modal.
    expect(screen.getByRole("button", { name: "Declare" })).toBeInTheDocument();
  });

  it("the type the knowledge form links to (?type=) opens it straight away, and it can be reopened", async () => {
    params.set("type", "documents/sharepoint");
    try {
      wrap(<OrgConnectorsPage />);
      await screen.findByTestId("procedure-panel");
      fireEvent.click(screen.getByTestId("procedure-close"));
      expect(screen.queryByTestId("procedure-panel")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "How to set this up" }));
      expect(screen.getByTestId("procedure-panel")).toBeInTheDocument();
    } finally {
      params.delete("type");
    }
  });

  it("an MCP server whose address is Atlassian's gets the Rovo procedure", async () => {
    wrap(<OrgConnectorsPage />);
    await screen.findByRole("option", { name: /MCP server/ });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "mcp/mcp" } });
    const first = await screen.findByTestId("procedure-panel");
    expect(first).toHaveTextContent("Connect an MCP server");
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://mcp.atlassian.com/v2/mcp" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "How to set this up" })).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("procedure-close"));
    fireEvent.click(await screen.findByRole("button", { name: "How to set this up" }));
    await waitFor(() => expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Rovo MCP Server"));
  });

  it("a declared connector has a button, not an interruption, on its settings", async () => {
    api.orgConnectors.mockResolvedValue([
      { name: "kb", kind: "documents", type: "sharepoint", config: {}, secret_refs: {}, secrets: [], status: "ok", operations: [] } as unknown as OrgConnector,
    ]);
    wrap(<OrgConnectorsPage />);
    const panel = await screen.findByTestId("secrets-kb");
    await within(panel).findByRole("button", { name: "How to set this up" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
  });

  it("a knowledge source: choosing Confluence opens the source procedure, and the connector's is one click away", async () => {
    api.orgConnectors.mockResolvedValue([]);
    wrap(<KnowledgeSources org="acme" slug="framework" sources={[]} onChange={vi.fn()} />);
    // Not when the page loads: only once a kind has been chosen.
    await screen.findByRole("button", { name: "How to set this up" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
    fireEvent.change(screen.getByLabelText("source kind"), { target: { value: "confluence" } });
    const panel = await screen.findByTestId("procedure-panel");
    expect(panel).toHaveTextContent("Add a Confluence space or page tree to a knowledge base");
    fireEvent.click(screen.getByRole("button", { name: "Set up the connector" }));
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Connect Confluence Cloud");
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("api.atlassian.com");
  });

  it("a web source has its own procedure and no connector button", async () => {
    wrap(<KnowledgeSources org="acme" slug="framework" sources={[]} onChange={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("source kind"), { target: { value: "web" } });
    expect(await screen.findByTestId("procedure-panel")).toHaveTextContent("Add a web site to a knowledge base");
    expect(screen.queryByRole("button", { name: "Set up the connector" })).toBeNull();
  });

  it("a project's tracker: adding one opens the Jira procedure with this platform's webhook address", async () => {
    api.projectRequirements.mockResolvedValue([{ capability: "tracker", reasons: ["a ticket"], default_type: null }]);
    api.connectors.mockResolvedValue([]);
    api.connectorTypes.mockResolvedValue([type("tracker", "jira", "Jira Cloud", ["api_token"])]);
    wrap(<Connecteurs slug="billing-api" />);
    fireEvent.click(await screen.findByRole("button", { name: /^(Add|Configure|Set up)/ }));
    const panel = await screen.findByTestId("procedure-panel");
    expect(panel).toHaveTextContent("Use Jira Cloud as the board");
    expect(within(panel).getByTestId("procedure-value-webhook_url:jira")).toHaveTextContent("/api/v1/webhooks/jira");
  });

  it("editing a project connector that exists does not interrupt, it offers", async () => {
    const tracker = { id: "c1", kind: "tracker", type: "jira", config: {}, secret_refs: {}, secrets: [], status: "ok" } as unknown as ConnectorDto;
    api.projectRequirements.mockResolvedValue([{ capability: "tracker", reasons: ["a ticket"], default_type: null }]);
    api.connectors.mockResolvedValue([tracker]);
    api.connectorTypes.mockResolvedValue([type("tracker", "jira", "Jira Cloud", ["api_token"])]);
    wrap(<Connecteurs slug="billing-api" />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await screen.findByRole("button", { name: "How to set this up" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
  });
});
