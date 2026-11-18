import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FlowScreen } from "@/components/workflows/flow/flow-screen";
import { api } from "@/lib/api";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * The flow's screen (S67-29): the workflow read from the validator, the tickets of the workflow on it, the
 * summary and the ticker; a click on a station narrows the ticker and lists its tickets; the wall mode takes
 * the whole screen with the project, the workflow and a clock. The stream is a FakeSource; the API is stubbed.
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ org: "acme", orgs: [{ slug: "acme", currency: "EUR" }], me: null }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, allWorkItems: vi.fn(), workflowNamed: vi.fn(), validateWorkflow: vi.fn(), project: vi.fn(), costs: vi.fn() },
  };
});

class FakeSource {
  static all: FakeSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  constructor(public url: string) {
    FakeSource.all.push(this);
  }
  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  removeEventListener() {}
  close() {}
  emit(name: string, data: unknown) {
    for (const fn of this.listeners.get(name) ?? []) fn({ data: JSON.stringify(data) } as MessageEvent);
  }
}

const graph = {
  nodes: [
    { id: "todo", display: "To do", lane: "agent" },
    { id: "doing", display: "Doing", lane: "agent" },
    { id: "done", display: "Done", terminal: true, lane: "terminal" },
  ],
  edges: [
    { id: "a", from: "todo", to: "doing", kind: "nominal", actor: "dev" },
    { id: "b", from: "doing", to: "done", kind: "nominal", actor: "dev" },
  ],
} as unknown as WorkflowGraph;
const item = (id: string, state: string): WorkItemDto =>
  ({ id, project_slug: "acme-web", tracker_key: `ACME-${id}`, title: `Title ${id}`, state, workflow_name: "delivery", totals: {} }) as unknown as WorkItemDto;

async function show(wallRequested = false) {
  vi.mocked(api.allWorkItems).mockResolvedValue({ items: [item("1", "todo"), item("2", "doing")], meta: { has_more: false } } as never);
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "delivery", version: 1, yaml: "name: delivery", json: { initial: "todo" } } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ graph } as never);
  vi.mocked(api.project).mockResolvedValue({ slug: "acme-web", name: "Acme Web" } as never);
  vi.mocked(api.costs).mockResolvedValue({ rows: [], total: { cost_usd: 0, cost_eur: 0 } } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <FlowScreen slug="acme-web" workflow="delivery" isDefault={false} wallRequested={wallRequested} />
      </QueryClientProvider>,
    );
  });
  await screen.findByTestId("flow-view");
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeSource.all = [];
  vi.stubGlobal("EventSource", FakeSource);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as never;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the flow screen", () => {
  it("draws the workflow's stations with the tickets that are in them, and a summary", async () => {
    await show();
    expect(screen.getAllByTestId("flow-station")).toHaveLength(3);
    expect(document.querySelector('[data-token-id="1"]')).toHaveAttribute("data-station", "todo");
    expect(document.querySelector('[data-token-id="2"]')).toHaveAttribute("data-station", "doing");
    expect(screen.getByTestId("live-summary")).toBeInTheDocument();
    expect(screen.getByTestId("live-ticker")).toBeInTheDocument();
    expect(screen.queryByTestId("live-wall")).toBeNull();
  });

  it("narrows the ticker and lists the tickets when a station is clicked, and clears it again", async () => {
    await show();
    const stream = FakeSource.all[0]!;
    await act(async () => stream.onopen?.());
    const entry = (id: string) => ({
      kind: "run",
      id: `r${id}`,
      status: "running",
      project_slug: "acme-web",
      work_item_id: id,
      workflow: "delivery",
      stage_role: id === "1" ? "triage" : "developer",
      agent_slug: "dev",
      started_at: new Date().toISOString(),
      can_stop: true,
    });
    await act(async () => {
      stream.emit("activity.upsert", entry("1"));
      stream.emit("activity.upsert", entry("2"));
    });
    const ticker = screen.getByTestId("live-ticker");
    expect(within(ticker).getByText("ACME-1")).toBeInTheDocument();
    expect(within(ticker).getByText("ACME-2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Doing,/ }));
    const panel = screen.getByTestId("flow-panel");
    expect(within(panel).getByRole("link", { name: /ACME-2/ })).toHaveAttribute("href", "/p/acme-web/items/2");
    expect(within(panel).queryByRole("link", { name: /ACME-1/ })).toBeNull();
    expect(within(screen.getByTestId("live-ticker")).queryByText("ACME-1")).toBeNull();
    expect(within(screen.getByTestId("live-ticker")).getByText("ACME-2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear the station filter" }));
    expect(screen.queryByTestId("flow-panel")).toBeNull();
    expect(within(screen.getByTestId("live-ticker")).getByText("ACME-1")).toBeInTheDocument();
  });

  it("opens the wall with the project, the workflow and a clock, and leaves it on Escape", async () => {
    await show();
    fireEvent.click(screen.getByRole("button", { name: "Wall mode" }));
    const wall = screen.getByTestId("live-wall");
    expect(within(wall).getByRole("heading", { level: 1 })).toHaveTextContent("Acme Web");
    expect(within(wall).getByRole("heading", { level: 1 })).toHaveTextContent("delivery");
    expect(within(wall).getByTestId("live-clock")).toBeInTheDocument();
    expect(within(wall).getByTestId("flow-view")).toBeInTheDocument();
    // The flow is on the wall only: not drawn twice.
    expect(screen.getAllByTestId("flow-view")).toHaveLength(1);
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(screen.queryByTestId("live-wall")).toBeNull();
    expect(screen.getAllByTestId("flow-view")).toHaveLength(1);
  });

  it("opens already on the wall when asked to (?wall=1)", async () => {
    await show(true);
    expect(screen.getByTestId("live-wall")).toBeInTheDocument();
  });
});
