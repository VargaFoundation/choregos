import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import McpCataloguePage from "@/app/admin/connectors/catalogue/page";
import RemoteAgentsAdminPage from "@/app/admin/remote-agents/page";
import NewRemoteAgentPage from "@/app/agents/new/remote/page";
import { AgentBuilder } from "@/components/agents/builder";
import { mcpCatalogue } from "@/mocks/data";
import { proceduresApi } from "@/mocks/procedures";
import type { McpCatalogue } from "@/lib/types";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));

const { api } = vi.hoisted(() => ({
  api: {
    listProcedures: vi.fn(),
    getProcedure: vi.fn(),
    remoteCredentials: vi.fn(),
    discoverRemoteAgent: vi.fn(),
    mcpCatalogue: vi.fn(),
    projects: vi.fn(),
    usableModels: vi.fn(),
    platformModels: vi.fn(),
    backends: vi.fn(),
    agentImages: vi.fn(),
    orgConnectors: vi.fn(),
    skills: vi.fn(),
    knowledgeBases: vi.fn(),
  },
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, ...api } };
});

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  document.body.className = "";
  api.listProcedures.mockImplementation(async (query = {}) => {
    const search = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]);
    return proceduresApi("GET", `/procedures?${search}`);
  });
  api.getProcedure.mockImplementation(async (id: string) => proceduresApi("GET", `/procedures/${id}`));
  api.remoteCredentials.mockResolvedValue([]);
  api.projects.mockResolvedValue({ items: [], meta: { has_more: false } });
  api.usableModels.mockResolvedValue([]);
  api.platformModels.mockResolvedValue([]);
  api.backends.mockResolvedValue([{ name: "claude-code", enabled: true, capabilities: [] }]);
  api.agentImages.mockResolvedValue([{ name: "ticket-triager", digest_pinned: true }]);
  api.orgConnectors.mockResolvedValue([]);
  api.skills.mockResolvedValue([]);
  api.knowledgeBases.mockResolvedValue([]);
});

describe("the procedure at the gestures of the batch-4 types (S39-23e)", () => {
  it("a new remote agent: the generic A2A procedure opens above the card step, with the callback address", async () => {
    wrap(<NewRemoteAgentPage />);
    const panel = await screen.findByTestId("procedure-panel");
    expect(panel).toHaveTextContent("Add a remote agent (A2A)");
    expect(within(panel).getByTestId("procedure-value-oauth_callback:remote")).toHaveTextContent(
      "/api/v1/remote-credentials/oauth/callback",
    );
    expect(screen.getByTestId("remote-procedure")).toContainElement(screen.getByTestId("procedure-open"));
  });

  it("typing Atlassian's card address switches the procedure to Rovo's", async () => {
    window.localStorage.setItem("choregos.procedure.seen.agent.remote.a2a", "1");
    window.localStorage.setItem("choregos.procedure.seen.agent.remote.a2a.atlassian-rovo", "1");
    wrap(<NewRemoteAgentPage />);
    fireEvent.click(await screen.findByRole("button", { name: "How to set this up" }));
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Add a remote agent (A2A)");
    fireEvent.change(screen.getByLabelText(/Card URL/), { target: { value: "https://a2a.atlassian.com/.well-known/agent.json" } });
    await waitFor(() => expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Add Atlassian Rovo as a remote agent"));
    const panel = screen.getByTestId("procedure-panel");
    expect(panel).toHaveTextContent("work-in-jira");
    expect(panel).toHaveTextContent("full_access:chat:rovo");
    expect(panel).toHaveTextContent("a2a.atlassian.com");
    // Another host goes back to the generic one.
    fireEvent.change(screen.getByLabelText(/Card URL/), { target: { value: "https://agents.example.com/card.json" } });
    await waitFor(() => expect(screen.getByTestId("procedure-panel")).toHaveTextContent("Add a remote agent (A2A)"));
  });

  it("the remote agents administration offers both procedures without interrupting", async () => {
    wrap(<RemoteAgentsAdminPage />);
    await screen.findByRole("button", { name: "Set up Atlassian Rovo" });
    expect(screen.queryByTestId("procedure-panel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Set up Atlassian Rovo" }));
    expect(screen.getByTestId("procedure-panel")).toHaveTextContent("dedicated Atlassian account");
  });

  it("the builder's Code runtime shows the code-agent procedure, and no other runtime does", async () => {
    wrap(<AgentBuilder org="acme" mode={{ kind: "create" }} />);
    const builder = await screen.findByTestId("agent-builder");
    expect(within(builder).queryByRole("button", { name: "How to set this up" })).toBeNull();
    fireEvent.change(within(builder).getByLabelText("Runtime"), { target: { value: "code" } });
    const panel = await screen.findByTestId("procedure-panel");
    expect(panel).toHaveTextContent("Add an agent written in code");
    expect(panel).toHaveTextContent("global.images.agents");
    fireEvent.change(within(builder).getByLabelText("Runtime"), { target: { value: "acp" } });
    await waitFor(() => expect(within(builder).queryByTestId("procedure-open")).toBeNull());
  });

  it("an MCP catalogue install shows its procedure inside the drawer, not beside it", async () => {
    const catalogue = mcpCatalogue() as McpCatalogue;
    api.mcpCatalogue.mockResolvedValue(catalogue);
    expect(catalogue.entries.find((e) => e.id === "github")!.procedure).toBe("connector.mcp.mcp");
    wrap(<McpCataloguePage />);
    fireEvent.click(within(await screen.findByTestId("entry-github")).getByRole("button", { name: "Install" }));
    const drawer = await screen.findByTestId("install-drawer");
    fireEvent.click(await within(drawer).findByRole("button", { name: "How to set this up" }));
    // In the drawer's own flow, not a second panel on top of a modal.
    expect(within(drawer).getByTestId("procedure-panel")).toHaveTextContent("Connect an MCP server");
    expect(document.body.classList.contains("procedure-open")).toBe(false);
  });
});

describe("the catalogue names the procedure of each entry", () => {
  it("the mock gives Atlassian its own and every other entry the generic one", () => {
    const ids = new Set(mcpCatalogue().entries.map((e) => e.procedure));
    expect(ids).toEqual(new Set(["connector.mcp.mcp", "connector.mcp.mcp.atlassian-rovo"]));
  });
});
