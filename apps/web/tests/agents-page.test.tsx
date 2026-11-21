import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { McpClients } from "@/components/agents/mcp-clients";
import { fakeAgentsPage } from "./fake-agents-server";
import { api } from "@/lib/api";
import type { Agent, ApiToken } from "@/lib/types";

/**
 * `/agents` is the registry, and each of its lists is one request (S30-04, plan item 7): the
 * registry and the metrics of every agent; the AI clients' attachments in one list too.
 */
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: null }) }));
const query = vi.hoisted(() => ({ current: new URLSearchParams() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/agents", useSearchParams: () => query.current, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agentsPage: vi.fn(),
      agentMetricsList: vi.fn(),
      agentMetrics: vi.fn(),
      orgAgentReleases: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      agentCatalogue: vi.fn().mockResolvedValue([]),
      platformModels: vi.fn().mockResolvedValue([]),
      backends: vi.fn().mockResolvedValue([]),
      orgConnectors: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false, next_cursor: null } }),
      skills: vi.fn().mockResolvedValue([]),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgAgentCredentials: vi.fn(),
      agentCredentials: vi.fn(),
      myTokens: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false, next_cursor: null } }),
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
  it("hides retired agents until asked for", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([agent("redacteur"), { ...agent("l-k-jn"), lifecycle: "retired" } as Agent]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    wrap(<AgentsPage />);
    await screen.findByTestId("agent-redacteur");
    expect(screen.queryByTestId("agent-l-k-jn")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show retired" }));
    expect(await screen.findByTestId("agent-l-k-jn")).toBeInTheDocument();
  });

  it("shows the registry with its 30-day columns, from two requests whatever the number of agents", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([agent("redacteur"), agent("relecteur"), agent("claude-de-lea", "external")]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [
      { agent: "redacteur", days: 30, runs: 14, success_rate: 0.857, cost_usd: 6.42 },
      { agent: "relecteur", days: 30, runs: 0, success_rate: null, cost_usd: 0 },
    ], meta: { has_more: false } });
    wrap(<AgentsPage />);
    const row = await screen.findByTestId("agent-redacteur");
    await waitFor(() => expect(row).toHaveTextContent("14"));
    expect(row).toHaveTextContent("US$6.42");
    expect(row).toHaveTextContent("86 %");
    expect(screen.getByTestId("agent-claude-de-lea")).toHaveTextContent("External");
    expect(api.agentsPage).toHaveBeenCalledTimes(1);
    expect(api.agentMetricsList).toHaveBeenCalledTimes(1);
    expect(api.agentMetrics).not.toHaveBeenCalled();
    // AI clients and their tokens are not on this page any more.
    expect(screen.queryByText("Your AI clients")).toBeNull();
    expect(api.agentCredentials).not.toHaveBeenCalled();
  });

  it("sends a new agent to its own full page, and opens the catalogue in a drawer", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    query.current = new URLSearchParams();
    wrap(<AgentsPage />);
    // S39-05: a link to a page, not a button opening a 672 px drawer.
    expect(screen.getByRole("link", { name: "New agent" })).toHaveAttribute("href", "/agents/new");
    expect(screen.queryByRole("button", { name: "New agent" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add from catalogue" }));
    expect(await screen.findByRole("dialog", { name: "Add from catalogue" })).toBeInTheDocument();
  });

  it("opens the catalogue at once when the address says so", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    query.current = new URLSearchParams("catalogue=1");
    wrap(<AgentsPage />);
    expect(await screen.findByRole("dialog", { name: "Add from catalogue" })).toBeInTheDocument();
    query.current = new URLSearchParams();
  });
});

describe("agent scopes in the registry (S39-09)", () => {
  const scoped = (slug: string, scope: Agent["scope"], project: string | null = null): Agent => ({ ...agent(slug), scope, project });

  it("shows who owns each agent, and filters by scope", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([
      scoped("developer", "platform"),
      scoped("shared", "org"),
      scoped("dev-agent", "project", "dev"),
      scoped("mine", "personal"),
      agent("legacy"),
    ]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    wrap(<AgentsPage />);
    const badge = async (slug: string) => (await screen.findByTestId(`agent-${slug}`)).querySelector("[data-testid=scope]")!;
    expect((await badge("developer")).textContent).toBe("Common");
    expect((await badge("shared")).textContent).toBe("Organisation");
    expect((await badge("dev-agent")).textContent).toBe("Project · dev");
    expect((await badge("mine")).textContent).toBe("Personal");
    // An API that predates the scopes sends none: an organisation agent.
    expect((await badge("legacy")).textContent).toBe("Organisation");

    fireEvent.click(screen.getByRole("button", { name: /^Personal/ }));
    // The server filters: the scope goes in the request, and the other rows are not in the answer.
    await waitFor(() => expect(api.agentsPage).toHaveBeenLastCalledWith("acme", expect.objectContaining({ scope: "personal" })));
    await waitFor(() => expect(screen.queryByTestId("agent-developer")).toBeNull());
    expect(screen.getByTestId("agent-mine")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-shared")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(await screen.findByTestId("agent-developer")).toBeInTheDocument();
  });
});

describe("the AI clients' list", () => {
  it("reads every external agent's clients in one request, and says which agent a client acts as", async () => {
    vi.mocked(api.orgAgentCredentials).mockResolvedValue({ items: [
      { id: "c1", kind: "token", token_id: "tok-2", agent: "cursor-de-marc" },
    ], meta: { has_more: false } });
    const token = (id: string, name: string): ApiToken => ({ id, name, scopes: ["mcp:read"], created_at: "2026-10-10T10:00:00Z" }) as ApiToken;
    wrap(
      <McpClients
        org="acme"
        clients={[token("tok-1", "claude-code"), token("tok-2", "cursor")]}
        agents={[agent("claude-de-lea", "external"), agent("cursor-de-marc", "external"), agent("autre", "external")]}
      />,
    );
    expect(await screen.findByRole("link", { name: "Acts as cursor-de-marc" })).toHaveAttribute("href", "/agents/cursor-de-marc");
    expect(api.orgAgentCredentials).toHaveBeenCalledTimes(1);
    expect(api.agentCredentials).not.toHaveBeenCalled();
  });
});

describe("the agents' channels (S40-02c)", () => {
  it("shows production and testing per agent, and filters the agents awaiting approval", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage([
      { ...agent("redacteur"), stable_version: 1, candidate_version: 2, candidate_percent: 10 },
      { ...agent("relecteur"), stable_version: 3 },
    ], ["relecteur"]));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    vi.mocked(api.orgAgentReleases).mockResolvedValue({ items: [
      { id: "r1", agent: "relecteur", channel: "stable", kind: "promote", status: "pending_approval", requested_at: "2026-10-16T09:00:00Z" },
    ], meta: { has_more: false } });
    wrap(<AgentsPage />);
    const redacteur = await screen.findByTestId("agent-redacteur");
    expect(redacteur).toHaveTextContent("Production v1");
    expect(redacteur).toHaveTextContent("Testing v2 · 10 %");
    const chip = await screen.findByTestId("awaiting-chip");
    expect(chip).toHaveTextContent("Awaiting approval");
    expect(screen.getByTestId("agent-relecteur")).toHaveTextContent("Approval pending");
    expect(api.orgAgentReleases).toHaveBeenCalledWith("acme", "pending_approval", { limit: 200 });
    fireEvent.click(chip);
    await waitFor(() => expect(screen.queryByTestId("agent-redacteur")).toBeNull());
    expect(screen.getByTestId("agent-relecteur")).toBeInTheDocument();
    expect(api.agentsPage).toHaveBeenLastCalledWith("acme", expect.objectContaining({ awaiting_approval: true }));
  });
});


describe("the registry is paged by the server (ADR 0206)", () => {
  const many = Array.from({ length: 120 }, (_, n) => agent(`agent-${String(n).padStart(3, "0")}`));

  it("shows a page, and More reads the next one", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage(many));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    wrap(<AgentsPage />);
    await screen.findByTestId("agent-agent-000");
    expect(screen.getAllByTestId(/^agent-agent-/)).toHaveLength(50);
    expect(screen.queryByTestId("agent-agent-050")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByTestId("agent-agent-050")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^agent-agent-/)).toHaveLength(100);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    await waitFor(() => expect(screen.getAllByTestId(/^agent-agent-/)).toHaveLength(120));
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
  });

  it("sends what is typed to the server instead of filtering the rows it holds", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage(many));
    vi.mocked(api.agentMetricsList).mockResolvedValue({ items: [], meta: { has_more: false } });
    wrap(<AgentsPage />);
    await screen.findByTestId("agent-agent-000");
    fireEvent.change(screen.getByLabelText("Find an agent"), { target: { value: "agent-119" } });
    fireEvent.submit(screen.getByLabelText("Find an agent"));
    await waitFor(() => expect(api.agentsPage).toHaveBeenLastCalledWith("acme", expect.objectContaining({ q: "agent-119" })));
    await waitFor(() => expect(screen.getAllByTestId(/^agent-agent-/)).toHaveLength(1));
  });

  it("reads the metrics pages as far as the agents shown", async () => {
    vi.mocked(api.agentsPage).mockImplementation(fakeAgentsPage(many.slice(0, 3)));
    vi.mocked(api.agentMetricsList)
      .mockResolvedValueOnce({ items: [{ agent: "agent-000", days: 30, runs: 1, success_rate: 1, cost_usd: 0 }], meta: { has_more: true, next_cursor: "c1" } })
      .mockResolvedValueOnce({ items: [{ agent: "agent-002", days: 30, runs: 7, success_rate: 1, cost_usd: 0 }], meta: { has_more: false } });
    wrap(<AgentsPage />);
    const row = await screen.findByTestId("agent-agent-002");
    await waitFor(() => expect(row).toHaveTextContent("7"));
    expect(api.agentMetricsList).toHaveBeenCalledTimes(2);
  });
});
