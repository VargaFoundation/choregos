import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BoardPage from "@/app/p/[slug]/board/page";
import { useActivityFeed } from "@/components/live/live-kit";
import { LiveBoardSection } from "@/components/workflows/live-board-section";
import { api } from "@/lib/api";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * The board is live (S67-27, ADR 0187 §4): the board's own columns, kept up to date by the project's
 * activity stream. A card that changes state slides to its column, a run in flight says where it is, a
 * failure says why, a wait is amber. The stream is a FakeSource; the API is stubbed.
 */
const search = { current: new URLSearchParams() };
const orgCurrency = vi.hoisted(() => ({ current: "EUR" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => search.current,
  usePathname: () => "/p/acme-web/board",
}));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ org: "acme", orgs: [{ slug: "acme", currency: orgCurrency.current }], me: null }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      allWorkItems: vi.fn(),
      workflows: vi.fn(),
      workflowNamed: vi.fn(),
      validateWorkflow: vi.fn(),
      connectors: vi.fn().mockResolvedValue([]),
      costs: vi.fn(),
    },
  };
});

class FakeSource {
  static all: FakeSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  closed = false;
  constructor(public url: string) {
    FakeSource.all.push(this);
  }
  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  removeEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(name, (this.listeners.get(name) ?? []).filter((f) => f !== fn));
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    for (const fn of this.listeners.get(name) ?? []) fn({ data: JSON.stringify(data) } as MessageEvent);
  }
}
const source = () => FakeSource.all[FakeSource.all.length - 1]!;

const graph = {
  nodes: [
    { id: "todo", display: "To do" },
    { id: "doing", display: "Doing" },
    { id: "done", display: "Done", terminal: true },
  ],
  edges: [],
} as unknown as WorkflowGraph;

const item = (id: string, state: string, over: Record<string, unknown> = {}): WorkItemDto =>
  ({
    id,
    project_slug: "acme-web",
    tracker_key: `ACME-${id}`,
    title: `Title ${id}`,
    state,
    workflow_name: "delivery",
    totals: {},
    ...over,
  }) as unknown as WorkItemDto;

const run = (id: string, itemId: string, status: string, over: Record<string, unknown> = {}) => ({
  kind: "run",
  id,
  status,
  project_slug: "acme-web",
  work_item_id: itemId,
  stage_role: "developer",
  agent_slug: "dev-agent",
  started_at: new Date().toISOString(),
  can_stop: status === "running",
  ...over,
});

const column = (state: string) => document.getElementById(`colonne-delivery-${state}`) as HTMLElement;
const card = (id: string) => document.querySelector(`[data-card-id="${id}"]`) as HTMLElement | null;

function stubReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: reduce && query.includes("reduce"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

/** One real second of the clock's tick. */
const tick = () => act(async () => void (await new Promise((resolve) => setTimeout(resolve, 1100))));

const today = new Date().toISOString().slice(0, 10);
const costReport = {
  rows: [{ key: today, cost_usd: 10, cost_eur: 9 }],
  total: { cost_usd: 20, cost_eur: 18 },
};

async function showBoard(items: WorkItemDto[]) {
  vi.mocked(api.allWorkItems).mockResolvedValue({ items, meta: { has_more: false } } as never);
  vi.mocked(api.workflows).mockResolvedValue([{ name: "delivery", is_default: true, version: 1 }] as never);
  vi.mocked(api.workflowNamed).mockResolvedValue({ name: "delivery", version: 1, yaml: "name: delivery" } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ graph } as never);
  vi.mocked(api.costs).mockResolvedValue(costReport as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const params = Object.assign(Promise.resolve({ slug: "acme-web" }), { status: "fulfilled", value: { slug: "acme-web" } });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback="…">
          <BoardPage params={params} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
  await screen.findAllByText("Title " + (items[0]?.id ?? ""));
  await vi.waitFor(() => expect(column("todo") ?? column("doing") ?? column("done")).toBeTruthy());
  return client;
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeSource.all = [];
  vi.stubGlobal("EventSource", FakeSource);
  search.current = new URLSearchParams();
  stubReducedMotion(false);
  orgCurrency.current = "EUR";
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the board listens to the project", () => {
  it("opens one activity stream, filtered by the project, whatever the number of workflows", async () => {
    await showBoard([item("1", "todo")]);
    expect(FakeSource.all).toHaveLength(1);
    expect(source().url).toContain("/orgs/acme/activity/stream");
    expect(source().url).toContain("project=acme-web");
  });

  it("says LIVE while the stream is open and RECONNECTING when it drops, and polls the items meanwhile", async () => {
    await showBoard([item("1", "todo")]);
    expect(screen.getByTestId("live-indicator").dataset.state).toBe("reconnecting");
    await act(async () => source().onopen?.());
    expect(screen.getByTestId("live-indicator").dataset.state).toBe("live");
    expect(screen.getByTestId("live-indicator").textContent).toBe("LIVE");
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await act(async () => source().onerror?.());
    expect(screen.getByTestId("live-indicator").textContent).toBe("RECONNECTING");
    const reads = vi.mocked(api.allWorkItems).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(vi.mocked(api.allWorkItems).mock.calls.length).toBeGreaterThan(reads);
  });

  it("closes the stream when the page goes", async () => {
    await showBoard([item("1", "todo")]);
    const s = source();
    cleanup();
    expect(s.closed).toBe(true);
  });
});

describe("the cards", () => {
  it("moves a card to its new column when its work item changes state, re-reading the items when a run ends", async () => {
    await showBoard([item("1", "todo")]);
    expect(column("todo").contains(card("1"))).toBe(true);
    vi.mocked(api.allWorkItems).mockResolvedValue({ items: [item("1", "doing")], meta: { has_more: false } } as never);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () =>
      source().emit("activity.ended", run("r1", "1", "succeeded", { ended_at: new Date().toISOString() })),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(api.allWorkItems).toHaveBeenCalledTimes(2);
    expect(column("doing").contains(card("1"))).toBe(true);
    expect(column("todo")).toBeNull();
    expect(card("1")!.dataset.arrived).toBe("true");
  });

  it("slides a card from where it was (FLIP) and, asked for less motion, lets it jump", async () => {
    const place = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const where = this.closest("section[id^=colonne]")?.id ?? "";
      const left = where.endsWith("doing") ? 300 : where.endsWith("todo") ? 0 : 600;
      return { left, top: 0, right: left + 100, bottom: 40, width: 100, height: 40, x: left, y: 0, toJSON: () => ({}) };
    });
    const moved = async (reduce: boolean) => {
      stubReducedMotion(reduce);
      await showBoard([item("1", "todo")]);
      vi.mocked(api.allWorkItems).mockResolvedValue({ items: [item("1", "doing")], meta: { has_more: false } } as never);
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      await act(async () => source().emit("activity.ended", run("r1", "1", "succeeded")));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      const transition = card("1")!.style.transition;
      vi.useRealTimers();
      cleanup();
      return transition;
    };
    // Put back where it was and released to slide to its place: the transition is set only with motion.
    expect(await moved(false)).toContain("transform 450ms");
    FakeSource.all = [];
    expect(await moved(true)).toBe("");
    place.mockRestore();
  });

  it("shows a running card's stage and agent, elapsed time (ticking) and spend in the organisation's currency", async () => {
    // Only the date is frozen: the clock's own one-second tick is the real one.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-18T10:00:10Z"));
    await showBoard([item("1", "doing")]);
    await act(async () => {
      source().emit(
        "activity.upsert",
        run("r1", "1", "running", {
          started_at: "2026-11-18T10:00:00Z",
          spend: { tokens_in: 1, tokens_out: 1, tokens_cached: 0, cost_usd: 1.92, read_at: "2026-11-18T10:00:05Z" },
        }),
      );
    });
    await tick();
    const running = card("1")!;
    expect(running.dataset.status).toBe("running");
    expect(within(running).getByTestId("stage-line").textContent).toBe("developer · dev-agent");
    expect(running.textContent).toContain("10 s");
    // The ledger's own rate (18 EUR for 20 USD): 1.92 USD is 1.73 EUR, the symbol only.
    expect(running.textContent).toContain("€1.73");
    expect(running.textContent).not.toContain("US$");
    expect(running.querySelector(".live-progress")).not.toBeNull();
    vi.setSystemTime(new Date("2026-11-18T10:00:13Z"));
    await tick();
    expect(card("1")!.textContent).toContain("13 s");
  });

  it("writes the stage once when the agent has the stage's own name", async () => {
    await showBoard([item("1", "doing")]);
    await act(async () => source().emit("activity.upsert", run("r1", "1", "running", { stage_role: "triage", agent_slug: "Triage" })));
    expect(within(card("1")!).getByTestId("stage-line").textContent).toBe("triage");
  });

  it("writes the spend in dollars for an organisation that bills in dollars", async () => {
    orgCurrency.current = "USD";
    await showBoard([item("1", "doing")]);
    await act(async () =>
      source().emit("activity.upsert", run("r1", "1", "running", { spend: { tokens_in: 1, tokens_out: 1, tokens_cached: 0, cost_usd: 1.92, read_at: new Date().toISOString() } })),
    );
    expect(card("1")!.textContent).toContain("$1.92");
  });

  it("shows a failed card in red with the reason, and the summary one hover or focus away", async () => {
    await showBoard([item("1", "doing")]);
    await act(async () =>
      source().emit(
        "activity.ended",
        run("r1", "1", "failed", {
          ended_at: new Date().toISOString(),
          failure: { reason: "policy_denied", summary: "The tool call was refused by the egress policy." },
        }),
      ),
    );
    const failed = card("1")!;
    expect(failed.dataset.status).toBe("failed");
    expect(failed.querySelector("section")!.className).toContain("border-failed-line");
    const reason = within(failed).getByTestId("failure-reason");
    expect(reason.textContent).toBe("policy denied");
    expect(reason.getAttribute("tabindex")).toBe("0");
    const summary = within(failed).getByRole("tooltip", { hidden: true });
    expect(reason.getAttribute("aria-describedby")).toBe(summary.id);
    expect(summary.textContent).toContain("egress policy");
  });

  it("shows a card that waits for a person in amber, and says what for", async () => {
    await showBoard([
      item("1", "doing", {
        pending_request: {
          id: "q1",
          kind: "task",
          payload: { title: "Approve the rate limit plan" },
          requested_at: new Date().toISOString(),
          decided_at: null,
        },
      }),
    ]);
    const waiting = card("1")!;
    expect(waiting.dataset.status).toBe("waiting");
    expect(waiting.querySelector("section")!.className).toContain("border-waiting-line");
  });
});

describe("the summary and the events", () => {
  it("counts what is in flight, waiting, failed, done today and what was spent today", async () => {
    await showBoard([item("1", "doing"), item("2", "doing"), item("3", "todo")]);
    await act(async () => source().emit("activity.upsert", run("r1", "1", "running")));
    await act(async () => source().emit("activity.ended", run("r2", "2", "failed", { failure: { reason: "timeout", summary: "Too long." } })));
    const text = (id: string) => within(screen.getByTestId("live-summary")).getByTestId(id).textContent;
    expect(text("sum-running")).toContain("1");
    expect(text("sum-failed")).toContain("1");
    expect(text("sum-waiting")).toContain("0");
    // Today's row of the ledger (10 USD), in the organisation's currency at the ledger's rate (0.9).
    expect(text("sum-spend")).toContain("€9.00");
  });

  it("keeps the latest events in a strip that is folded until asked for", async () => {
    await showBoard([item("1", "doing")]);
    const details = screen.getByTestId("board-ticker-details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await act(async () => source().emit("activity.upsert", run("r1", "1", "running")));
    expect(within(details).getByTestId("live-ticker").textContent).toContain("ACME-1");
    expect(within(details).getByTestId("live-ticker").textContent).toContain("started developer");
  });
});

describe("the demo", () => {
  it("plays its story on the board's own columns: a card moves on without a stream", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const client = new QueryClient();
    function Probe() {
      const feed = useActivityFeed({ org: "acme", slug: "acme-web", demo: true });
      return (
        <LiveBoardSection
          slug="acme-web"
          model={{ name: "delivery", version: 1, isDefault: true, columns: [], hidden: 0, total: 0, waiting: 0 }}
          graph={graph}
          items={[]}
          isDefault
          showEmpty
          feed={feed}
          demo
          alone
          onDecided={() => {}}
          currency="EUR"
          usdTo={null}
          spentTodayUsd={null}
        />
      );
    }
    await act(async () => {
      render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      );
    });
    await vi.dynamicImportSettled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(FakeSource.all).toHaveLength(0);
    expect(column("todo").contains(card("demo-0"))).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6500);
    });
    expect(column("doing").contains(card("demo-0"))).toBe(true);
  });
});
