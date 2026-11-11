import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPage from "@/app/agents/[slug]/page";
import WorkItemPage from "@/app/p/[slug]/items/[id]/page";
import { api, ApiError } from "@/lib/api";
import type { Agent, WorkItemDto } from "@/lib/types";
import { activityApi, resetActivity } from "@/mocks/activity";
import { journeyInProgress } from "@/mocks/journey";

/**
 * Stopping with a reason (S64-06, ADR 0142 §3-5), outside the Activity screen: an agent's page stops the
 * agent — it says it suspends it — and shows the counts the API answered; a work item's Stop sends the
 * reason with the `stop` action. Against mocks, not the API.
 */
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/agents/coordinator",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", orgs: [], me: { email: "lea@acme.dev", memberships: [] } }) }));
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  const none = () => Promise.resolve([]);
  return {
    ...real,
    api: {
      ...real.api,
      agent: vi.fn(),
      stopAgent: vi.fn(),
      agentMetrics: vi.fn().mockResolvedValue({ agent: "coordinator", days: 30, runs: 0, succeeded: 0, failed: 0, cost_usd: 0 }),
      agentReleases: vi.fn(none),
      agentExposure: vi.fn().mockResolvedValue({ projects: [] }),
      agentRuns: vi.fn(none),
      projects: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      workItem: vi.fn(),
      stopWorkItem: vi.fn(),
      timeline: vi.fn(none),
      handOffs: vi.fn(none),
      runs: vi.fn(none),
      connectors: vi.fn(none),
      artifacts: vi.fn(none),
      journey: vi.fn(),
      workflowNamed: vi.fn(),
    },
  };
});

const agent = (over: Partial<Agent> = {}): Agent =>
  ({
    slug: "coordinator",
    kind: "internal",
    display_name: "Coordinator",
    status: "active",
    latest_version: 1,
    lifecycle: "active",
    scope: "organisation",
    can: { publish: true, manage: true },
    versions: [{ version: 1, spec: {}, checksum: "sha256:abc" }],
    ...over,
  }) as Agent;

async function show(node: ReactNode) {
  document.body.innerHTML = "";
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Suspense fallback="…">{node}</Suspense>
      </QueryClientProvider>,
    );
  });
}

async function stopWith(scope: HTMLElement, opener: string, confirm: string, reason: string) {
  fireEvent.click(within(scope).getByRole("button", { name: opener }));
  const confirmation = within(scope).getByTestId("confirmation");
  expect(within(confirmation).getByRole("button", { name: confirm })).toBeDisabled();
  fireEvent.change(within(confirmation).getByRole("textbox"), { target: { value: reason } });
  await act(async () => {
    fireEvent.click(within(confirmation).getByRole("button", { name: confirm }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetActivity();
});

describe("an agent's Stop", () => {
  it("says it suspends the agent and stops its runs and sessions, sends the reason, and shows the counts", async () => {
    vi.mocked(api.agent).mockResolvedValue(agent());
    // The demo's own answer: the coordinator has one run in flight.
    vi.mocked(api.stopAgent).mockImplementation(async (org, slug, reason) => activityApi("POST", `/orgs/${org}/agents/${slug}/stop`, { reason }) as never);
    await show(<AgentPage params={Promise.resolve({ slug: "coordinator" })} />);
    const block = await screen.findByTestId("agent-stop");
    fireEvent.click(within(block).getByRole("button", { name: "Stop the agent…" }));
    const question = within(block).getByTestId("confirmation");
    expect(question).toHaveTextContent("It is suspended, so that nothing new starts");
    expect(question).toHaveTextContent("its runs in flight, in every project, are cancelled, its open sessions closed");
    fireEvent.click(within(block).getByRole("button", { name: "Cancel" }));
    await stopWith(block, "Stop the agent…", "Stop the agent", "it opens pull requests on the wrong repository");
    expect(api.stopAgent).toHaveBeenCalledWith("acme", "coordinator", "it opens pull requests on the wrong repository");
    expect(within(block).getByTestId("agent-stop-result")).toHaveTextContent(
      "Suspended. 1 run cancelled, 0 sessions closed, 0 conversation turns cancelled.",
    );
  });

  it("lists what could not be stopped yet", async () => {
    vi.mocked(api.agent).mockResolvedValue(agent());
    vi.mocked(api.stopAgent).mockResolvedValue({
      slug: "coordinator",
      status: "suspended",
      runs_cancelled: 2,
      sessions_closed: 1,
      turns_cancelled: 3,
      not_stopped: [{ kind: "run", id: "r-9", detail: "the executor did not answer" }],
    });
    await show(<AgentPage params={Promise.resolve({ slug: "coordinator" })} />);
    const block = await screen.findByTestId("agent-stop");
    await stopWith(block, "Stop the agent…", "Stop the agent", "runaway spend");
    const result = within(block).getByTestId("agent-stop-result");
    expect(result).toHaveTextContent("Suspended. 2 runs cancelled, 1 session closed, 3 conversation turns cancelled.");
    expect(result).toHaveTextContent("run r-9: the executor did not answer");
  });

  it("shows the server's message when the stop is refused", async () => {
    vi.mocked(api.agent).mockResolvedValue(agent());
    vi.mocked(api.stopAgent).mockRejectedValue(new ApiError(403, { title: "Forbidden", detail: "Only an organisation admin manages this agent" }));
    await show(<AgentPage params={Promise.resolve({ slug: "coordinator" })} />);
    const block = await screen.findByTestId("agent-stop");
    await stopWith(block, "Stop the agent…", "Stop the agent", "too expensive");
    expect(await within(block).findByRole("alert")).toHaveTextContent("Only an organisation admin manages this agent");
    expect(within(block).queryByTestId("agent-stop-result")).toBeNull();
  });

  it("is not offered to who may not manage the agent", async () => {
    vi.mocked(api.agent).mockResolvedValue(agent({ can: { publish: false, manage: false } }));
    await show(<AgentPage params={Promise.resolve({ slug: "coordinator" })} />);
    const block = await screen.findByTestId("agent-stop");
    expect(within(block).getByRole("button", { name: "Stop the agent…" })).toBeDisabled();
  });
});

describe("a work item's Stop", () => {
  it("sends the reason with the stop", async () => {
    vi.mocked(api.workItem).mockResolvedValue({
      id: "w1",
      project_slug: "billing-api",
      tracker_key: "OPS-7",
      title: "Database down in eu-west",
      state: "triage",
      created_at: "2026-11-03T10:00:00Z",
      totals: {},
    } as unknown as WorkItemDto);
    vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-04T12:00:00Z")));
    vi.mocked(api.stopWorkItem).mockResolvedValue(undefined);
    await show(<WorkItemPage params={Promise.resolve({ slug: "billing-api", id: "w1" })} />);
    await screen.findByRole("heading", { name: "Database down in eu-west" });
    await stopWith(document.body, "Stop", "Stop the ticket", "filed twice");
    expect(api.stopWorkItem).toHaveBeenCalledWith("w1", "filed twice");
  });

  it("shows the server's message when the stop is refused", async () => {
    vi.mocked(api.workItem).mockResolvedValue({
      id: "w1",
      project_slug: "billing-api",
      tracker_key: "OPS-7",
      title: "Database down in eu-west",
      state: "triage",
      created_at: "2026-11-03T10:00:00Z",
      totals: {},
    } as unknown as WorkItemDto);
    vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-04T12:00:00Z")));
    vi.mocked(api.stopWorkItem).mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "The work item has already ended" }));
    await show(<WorkItemPage params={Promise.resolve({ slug: "billing-api", id: "w1" })} />);
    await screen.findByRole("heading", { name: "Database down in eu-west" });
    await stopWith(document.body, "Stop", "Stop the ticket", "filed twice");
    const confirmation = screen.getByTestId("confirmation");
    expect(await within(confirmation).findByRole("alert")).toHaveTextContent("The work item has already ended");
  });
});
