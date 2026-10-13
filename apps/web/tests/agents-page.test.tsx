import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { ClientsMcp } from "@/components/agents/clients-mcp";
import { api } from "@/lib/api";
import type { Agent, ApiToken } from "@/lib/types";

/**
 * `/agents` is the registry, and each of its lists is one request (S30-04, plan item 7): the
 * registry and the metrics of every agent; the AI clients' attachments in one list too.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agents: vi.fn(),
      agentMetricsList: vi.fn(),
      agentMetrics: vi.fn(),
      agentCatalogue: vi.fn().mockResolvedValue([]),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue([]),
      skills: vi.fn().mockResolvedValue([]),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgAgentCredentials: vi.fn(),
      agentCredentials: vi.fn(),
      myTokens: vi.fn().mockResolvedValue([]),
    },
  };
});

const agent = (slug: string, kind: "internal" | "external" = "internal"): Agent =>
  ({ slug, kind, display_name: slug, status: "active", latest_version: 2, owner: "lea@acme.dev" }) as Agent;

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  );
}

describe("the agents' page", () => {
  it("shows the registry with its 30-day columns, from two requests whatever the number of agents", async () => {
    vi.mocked(api.agents).mockResolvedValue([agent("redacteur"), agent("relecteur"), agent("claude-de-lea", "external")]);
    vi.mocked(api.agentMetricsList).mockResolvedValue([
      { agent: "redacteur", days: 30, runs: 14, success_rate: 0.857, cost_usd: 6.42 },
      { agent: "relecteur", days: 30, runs: 0, success_rate: null, cost_usd: 0 },
    ]);
    wrap(<AgentsPage />);
    const ligne = await screen.findByTestId("agent-redacteur");
    await waitFor(() => expect(ligne).toHaveTextContent("14"));
    expect(ligne).toHaveTextContent("US$6.42");
    expect(ligne).toHaveTextContent("86 %");
    expect(screen.getByTestId("agent-claude-de-lea")).toHaveTextContent("External");
    expect(api.agents).toHaveBeenCalledTimes(1);
    expect(api.agentMetricsList).toHaveBeenCalledTimes(1);
    expect(api.agentMetrics).not.toHaveBeenCalled();
    // AI clients and their tokens are not on this page any more.
    expect(screen.queryByText("Your AI clients")).toBeNull();
    expect(api.agentCredentials).not.toHaveBeenCalled();
  });

  it("opens the new agent's form and the catalogue in a drawer", async () => {
    vi.mocked(api.agents).mockResolvedValue([]);
    vi.mocked(api.agentMetricsList).mockResolvedValue([]);
    wrap(<AgentsPage />);
    expect(screen.queryByLabelText("Slug")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New agent" }));
    const dialogue = await screen.findByRole("dialog", { name: "New agent" });
    // The builder is loaded when the drawer opens (S32-01).
    expect(await within(dialogue).findByLabelText("Slug")).toBeInTheDocument();
    fireEvent.click(within(dialogue).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "New agent" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add from catalogue" }));
    expect(await screen.findByRole("dialog", { name: "Add from catalogue" })).toBeInTheDocument();
  });
});

describe("the AI clients' list", () => {
  it("reads every external agent's clients in one request, and says which agent a client acts as", async () => {
    vi.mocked(api.orgAgentCredentials).mockResolvedValue([
      { id: "c1", kind: "token", token_id: "tok-2", agent: "cursor-de-marc" },
    ]);
    const jeton = (id: string, name: string): ApiToken => ({ id, name, scopes: ["mcp:read"], created_at: "2026-10-10T10:00:00Z" }) as ApiToken;
    wrap(
      <ClientsMcp
        org="acme"
        clients={[jeton("tok-1", "claude-code"), jeton("tok-2", "cursor")]}
        agents={[agent("claude-de-lea", "external"), agent("cursor-de-marc", "external"), agent("autre", "external")]}
      />,
    );
    expect(await screen.findByRole("link", { name: "Acts as cursor-de-marc" })).toHaveAttribute("href", "/agents/cursor-de-marc");
    expect(api.orgAgentCredentials).toHaveBeenCalledTimes(1);
    expect(api.agentCredentials).not.toHaveBeenCalled();
  });
});
