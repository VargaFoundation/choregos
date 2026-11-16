import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPage from "@/app/agents/[slug]/page";
import { api } from "@/lib/api";
import type { Agent, AgentAccess } from "@/lib/types";

/**
 * The Access tab (S60-03, ADR 0127): what a version reaches in one project, as the five components of
 * its definition, each with where it comes from. The API resolves it; the tab only says it.
 */
const query = vi.hoisted(() => ({ current: new URLSearchParams() }));
const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav,
  usePathname: () => "/agents/developer",
  useSearchParams: () => query.current,
}));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      agent: vi.fn(),
      agentAccess: vi.fn(),
      projects: vi.fn(),
      agentCredentials: vi.fn().mockResolvedValue([]),
      myTokens: vi.fn().mockResolvedValue([]),
    },
  };
});

const developer = (kind: Agent["kind"] = "internal"): Agent => ({
  slug: "developer",
  kind,
  display_name: "Developer",
  status: "active",
  lifecycle: "active",
  latest_version: 1,
  scope: "org",
  can: { publish: true, manage: true, pin: true, fork_scopes: [] },
  versions: [{ version: 1, spec: { instructions: "Code." }, checksum: "sha256:1", pinned_by: [] }],
});

const ACCESS: AgentAccess = {
  agent: "developer",
  version: 1,
  project: "billing-api",
  complete: false,
  inherits: ["model", "memory"],
  model: { requested: "profile:standard", resolved: "platform/standard-1", origin: "project" },
  tools: [
    { name: "run_step", source: "frame", origin: "frame" },
    { name: "read_file", source: "catalogue", origin: "agent", access: "read" },
    { name: "shell", source: "catalogue", origin: "refused", reason: "the project does not open shell" },
    { name: "jira_search", source: "connector", origin: "project", access: "read" },
    { name: "fetch", source: "web", origin: "agent" },
  ],
  knowledge: [{ name: "adr-framework", origin: "agent" }],
  memories: [{ name: "project:shared", origin: "project", mode: "write" }],
};

async function open() {
  document.body.innerHTML = "";
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback="…">
          <AgentPage params={Promise.resolve({ slug: "developer" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  query.current = new URLSearchParams("tab=access");
  vi.mocked(api.agent).mockResolvedValue(developer());
  vi.mocked(api.projects).mockResolvedValue({
    items: [
      { slug: "billing-api", name: "Billing API" },
      { slug: "hr-portal", name: "HR portal" },
    ],
    meta: { has_more: false },
  } as never);
  vi.mocked(api.agentAccess).mockResolvedValue(ACCESS);
});

describe("the Access tab", () => {
  it("asks for the first project the person reads, and says the five components with their origins", async () => {
    await open();
    const access = await screen.findByTestId("agent-access");
    expect(api.agentAccess).toHaveBeenCalledWith("acme", "developer", "billing-api", undefined);
    const model = within(access).getByTestId("access-model");
    expect(model.textContent).toContain("profile:standard");
    expect(model.textContent).toContain("platform/standard-1");
    expect(within(model).getByTestId("origin-project").textContent).toBe("inherited from the project");
    // Tools are grouped by source, each with its origin.
    expect(within(within(access).getByTestId("source-frame")).getByTestId("origin-frame").textContent).toBe("platform frame");
    expect(within(within(access).getByTestId("tool-catalogue-read_file")).getByTestId("origin-agent").textContent).toBe("agent");
    expect(within(within(access).getByTestId("source-connector")).getByText("jira_search")).toBeTruthy();
    expect(within(within(access).getByTestId("source-web")).getByText("fetch")).toBeTruthy();
    expect(within(access).getByTestId("source-extension").textContent).toContain("None");
    expect(within(within(access).getByTestId("access-knowledge")).getByText("adr-framework")).toBeTruthy();
    expect(within(within(access).getByTestId("access-memories")).getByText("project:shared")).toBeTruthy();
  });

  it("says a refused tool is refused, and why", async () => {
    await open();
    const shell = await screen.findByTestId("tool-catalogue-shell");
    expect(within(shell).getByTestId("origin-refused").textContent).toBe("refused");
    expect(shell.textContent).toContain("the project does not open shell");
  });

  it("warns, with the list, when the version leaves components to its project", async () => {
    await open();
    const banner = await screen.findByTestId("access-inherits");
    expect(banner.textContent).toBe("Inherited from billing-api: model, memory.");
    expect(screen.queryByTestId("access-complete")).toBeNull();
  });

  it("says nothing is inherited when the version is complete", async () => {
    vi.mocked(api.agentAccess).mockResolvedValue({ ...ACCESS, complete: true, inherits: [] });
    await open();
    expect((await screen.findByTestId("access-complete")).textContent).toContain("leaves nothing to project billing-api");
    expect(screen.queryByTestId("access-inherits")).toBeNull();
  });

  it("follows ?project= and puts the chosen project in the address", async () => {
    query.current = new URLSearchParams("tab=access&project=hr-portal");
    await open();
    await screen.findByTestId("agent-access");
    expect(api.agentAccess).toHaveBeenCalledWith("acme", "developer", "hr-portal", undefined);
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "billing-api" } });
    expect(nav.replace).toHaveBeenCalledWith("/agents/developer?tab=access&project=billing-api");
  });

  it("keeps the clients card of an external agent beside it", async () => {
    vi.mocked(api.agent).mockResolvedValue(developer("external"));
    await open();
    await screen.findByTestId("agent-access");
    await waitFor(() => expect(screen.getByText("MCP clients acting as this agent")).toBeTruthy());
  });

  it("names the outpost and the alias a connector tool lives at, and whether it answers now (ADR 0149 §10)", async () => {
    vi.mocked(api.agentAccess).mockResolvedValue({
      ...ACCESS,
      tools: [
        ...ACCESS.tools,
        {
          name: "tracker-onsite__search_issues",
          source: "connector",
          origin: "agent",
          access: "read",
          outpost: { name: "factory-floor", endpoint: "tracker", status: "active", online: true },
        },
        {
          name: "erp-branch__stock_level",
          source: "connector",
          origin: "agent",
          access: "read",
          outpost: { name: "branch-office", endpoint: "erp", status: "active", online: false },
        },
        {
          name: "sensors__read_probe",
          source: "connector",
          origin: "agent",
          access: "read",
          outpost: { name: "cold-store", endpoint: "sensors", status: "paused", online: false },
        },
        {
          name: "borrowed__search_issues",
          source: "connector",
          origin: "agent",
          access: "read",
          outpost: { name: null, endpoint: "tracker", status: null, online: false },
        },
      ],
    });
    await open();
    const where = async (tool: string) => within(await screen.findByTestId(`tool-connector-${tool}`)).getByTestId("tool-outpost");
    const online = await where("tracker-onsite__search_issues");
    expect(online).toHaveTextContent("At the outpost factory-floor, alias tracker");
    expect(online).toHaveTextContent("Online");
    expect(online).not.toHaveTextContent("Offline");
    const offline = await where("erp-branch__stock_level");
    expect(offline).toHaveTextContent("At the outpost branch-office, alias erp");
    expect(offline).toHaveTextContent("Offline");
    const paused = await where("sensors__read_probe");
    expect(paused).toHaveTextContent("Offline");
    expect(paused).toHaveTextContent("Paused");
    const nowhere = await where("borrowed__search_issues");
    expect(nowhere).toHaveTextContent("At no outpost of the organisation, alias tracker");
    expect(nowhere).toHaveTextContent("Offline");
    // A direct connector's tool says nothing of an outpost.
    expect(within(screen.getByTestId("tool-connector-jira_search")).queryByTestId("tool-outpost")).toBeNull();
  });

  it("says so when the person reads no project", async () => {
    vi.mocked(api.projects).mockResolvedValue({ items: [], meta: { has_more: false } } as never);
    await open();
    expect(await screen.findByText(/No project you can read/)).toBeTruthy();
    expect(api.agentAccess).not.toHaveBeenCalled();
  });
});
