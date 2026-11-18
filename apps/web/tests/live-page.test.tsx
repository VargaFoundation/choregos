import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  waitFor,
  render,
  screen,
  within,
} from "@testing-library/react";
import { Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LivePage from "@/app/p/[slug]/live/page";
import { LiveBoard } from "@/components/live/live-board";
import { projectGroups } from "@/components/shell/navigation";
import { api } from "@/lib/api";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * The live view (S67-27): lanes from the validated graph, a card per item, a card that moves lane when
 * its state changes, a failed card that says why, the wall that takes the console away, and the motion
 * that goes when the person asked for less. The stream is a FakeSource; the API is stubbed.
 */
const search = { current: new URLSearchParams() };
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => search.current,
  usePathname: () => "/p/acme-web/live",
}));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ org: "acme", orgs: [{ slug: "acme" }], me: null }),
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
    this.listeners.set(
      name,
      (this.listeners.get(name) ?? []).filter((f) => f !== fn),
    );
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    for (const fn of this.listeners.get(name) ?? [])
      fn({ data: JSON.stringify(data) } as MessageEvent);
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

const item = (
  id: string,
  state: string,
  over: Record<string, unknown> = {},
): WorkItemDto =>
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

const run = (
  id: string,
  itemId: string,
  status: string,
  over: Record<string, unknown> = {},
) => ({
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

const lane = (state: string) =>
  document.querySelector(`[data-lane="${state}"]`) as HTMLElement;
const card = (id: string) =>
  document.querySelector(`[data-card-id="${id}"]`) as HTMLElement | null;

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

async function showPage(items: WorkItemDto[]) {
  vi.mocked(api.allWorkItems).mockResolvedValue({
    items,
    meta: { has_more: false },
  } as never);
  vi.mocked(api.workflows).mockResolvedValue([
    { name: "delivery", is_default: true, version: 1 },
  ] as never);
  vi.mocked(api.workflowNamed).mockResolvedValue({
    name: "delivery",
    version: 1,
    yaml: "name: delivery",
  } as never);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ graph } as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <nav data-testid="shell-nav">Console navigation</nav>
        <Suspense fallback="…">
          <LivePage params={Promise.resolve({ slug: "acme-web" })} />
        </Suspense>
      </QueryClientProvider>,
    );
  });
  // The workflow, then its definition, then its validated graph: one read after the other.
  await screen.findByText(/Reading the workflow|No workflow named|To do/);
  if (!search.current.get("workflow")?.startsWith("ghost"))
    await screen.findByTestId("live-board");
  return client;
}

async function showBoard(items: WorkItemDto[], demo = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <LiveBoard
          graph={graph}
          items={items}
          org="acme"
          slug="acme-web"
          workflow="delivery"
          wall={false}
          demo={demo}
        />
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeSource.all = [];
  vi.stubGlobal("EventSource", FakeSource);
  search.current = new URLSearchParams();
  stubReducedMotion(false);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the lanes and the cards", () => {
  it("draws the workflow's states as lanes, with a card per item and a count", async () => {
    await showPage([item("1", "todo"), item("2", "todo"), item("3", "doing")]);
    expect(
      ["todo", "doing", "done"].map((s) => lane(s)?.getAttribute("aria-label")),
    ).toEqual(["To do", "Doing", "Done"]);
    expect(
      within(lane("todo"))
        .getAllByRole("listitem")
        .filter((li) => li.dataset.cardId),
    ).toHaveLength(2);
    expect(within(lane("todo")).getByTestId("lane-count").textContent).toBe(
      "2",
    );
    expect(within(lane("doing")).getByTestId("lane-count").textContent).toBe(
      "1",
    );
    expect(within(lane("done")).getByTestId("lane-count").textContent).toBe(
      "0",
    );
  });

  it("listens to this project's and this workflow's activity stream", async () => {
    await showPage([item("1", "todo")]);
    expect(source().url).toContain("/orgs/acme/activity/stream");
    expect(source().url).toContain("project=acme-web");
    expect(source().url).toContain("workflow=delivery");
  });

  it("slides a card to its next lane when its state changes, and re-reads the item when a run ends", async () => {
    await showPage([item("1", "todo")]);
    expect(lane("todo").contains(card("1"))).toBe(true);
    vi.mocked(api.allWorkItems).mockResolvedValue({
      items: [item("1", "doing")],
      meta: { has_more: false },
    } as never);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await act(async () =>
      source().emit(
        "activity.ended",
        run("r1", "1", "succeeded", { ended_at: new Date().toISOString() }),
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(api.allWorkItems).toHaveBeenCalledTimes(2);
    expect(lane("todo").contains(card("1"))).toBe(false);
    expect(lane("doing").contains(card("1"))).toBe(true);
    expect(within(lane("todo")).getByTestId("lane-count").textContent).toBe(
      "0",
    );
  });

  it("shows a running card's stage, agent, elapsed time (ticking each second) and spend", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-11-18T10:00:10Z"));
    await showBoard([item("1", "doing")]);
    await act(async () =>
      source().emit(
        "activity.upsert",
        run("r1", "1", "running", {
          started_at: "2026-11-18T10:00:00Z",
          spend: {
            tokens_in: 1,
            tokens_out: 1,
            tokens_cached: 0,
            cost_usd: 1.92,
            read_at: "2026-11-18T10:00:05Z",
          },
        }),
      ),
    );
    const running = card("1")!;
    expect(running.dataset.status).toBe("running");
    expect(running.className).toContain("live-running");
    expect(running.textContent).toContain("developer");
    expect(running.textContent).toContain("dev-agent");
    expect(running.textContent).toContain("10 s");
    expect(running.textContent).toContain("US$1.92");
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(card("1")!.textContent).toContain("12 s");
  });

  it("shows a failed card in red with the reason word, and the summary for hover and focus", async () => {
    await showBoard([item("1", "doing")]);
    await act(async () =>
      source().emit(
        "activity.ended",
        run("r1", "1", "failed", {
          ended_at: new Date().toISOString(),
          failure: {
            reason: "policy_denied",
            summary: "The tool call was refused by the egress policy.",
          },
        }),
      ),
    );
    const failed = card("1")!;
    expect(failed.dataset.status).toBe("failed");
    expect(failed.className).toContain("border-failed-line");
    expect(within(failed).getByTestId("failure-reason").textContent).toBe(
      "policy denied",
    );
    const summary = within(failed).getByRole("tooltip", { hidden: true });
    expect(summary.textContent).toBe(
      "The tool call was refused by the egress policy.",
    );
    expect(failed.getAttribute("aria-describedby")).toBe(summary.id);
    expect(failed.tabIndex).toBe(0);
    expect(summary.className).toContain("group-hover:block");
    expect(summary.className).toContain("group-focus:block");
  });

  it("shows a waiting card in amber with what it waits for", async () => {
    const waiting = item("1", "doing", {
      pending_request: {
        id: "q",
        kind: "approval",
        payload: { title: "Approve the rate limit plan" },
        decided_at: null,
      },
    });
    await showBoard([waiting]);
    const el = card("1")!;
    expect(el.dataset.status).toBe("waiting");
    expect(el.className).toContain("border-waiting-line");
    expect(el.textContent).toContain(
      "Waiting for: Approve the rate limit plan",
    );
  });

  it("says RECONNECTING when the stream drops, and LIVE again when it is back", async () => {
    await showBoard([item("1", "todo")]);
    expect(screen.getByTestId("live-indicator").dataset.state).toBe(
      "reconnecting",
    );
    await act(async () => source().onopen?.());
    expect(screen.getByTestId("live-indicator").dataset.state).toBe("live");
    expect(screen.getByTestId("live-indicator").textContent).toContain("LIVE");
    await act(async () => source().onerror?.());
    expect(screen.getByTestId("live-indicator").dataset.state).toBe(
      "reconnecting",
    );
  });

  it("writes what happens in the ticker", async () => {
    await showBoard([item("1", "doing")]);
    await act(async () =>
      source().emit("activity.upsert", run("r1", "1", "running")),
    );
    expect(screen.getByTestId("live-ticker").textContent).toContain("ACME-1");
    expect(screen.getByTestId("live-ticker").textContent).toContain(
      "started developer",
    );
  });
});

describe("the workflow filter", () => {
  it("shows the workflow ?workflow= names, and says so when there is none of that name", async () => {
    search.current = new URLSearchParams("workflow=ghost");
    await showPage([item("1", "todo")]);
    expect(screen.getByText(/No workflow named/)).toBeTruthy();
    expect(document.querySelector("[data-lane]")).toBeNull();
  });
});

describe("the wall", () => {
  it("takes the console away: the shell is inert and hidden from the page, the page's own navigation is gone", async () => {
    search.current = new URLSearchParams("wall=1");
    await showPage([item("1", "todo")]);
    const wall = screen.getByTestId("live-wall");
    expect(wall.className).toContain("fixed inset-0");
    expect(wall.hasAttribute("inert")).toBe(false);
    expect(screen.getByTestId("shell-nav").closest("[inert]")).not.toBeNull();
    expect(document.documentElement.dataset.wall).toBe("true");
    expect(screen.getByTestId("live-board").dataset.wall).toBe("true");
    expect(
      screen.queryByRole("navigation", { name: "Live workflow" }),
    ).toBeNull();
    // Escape and the button leave it.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(replace).toHaveBeenCalledWith("/p/acme-web/live");
    fireEvent.click(screen.getByRole("button", { name: /Exit wall mode/ }));
    expect(replace).toHaveBeenCalledTimes(2);
  });

  it("is entered by a button, which keeps the chosen workflow", async () => {
    search.current = new URLSearchParams("workflow=delivery");
    await showPage([item("1", "todo")]);
    expect(screen.queryByTestId("live-wall")).toBeNull();
    expect(screen.getByTestId("shell-nav").closest("[inert]")).toBeNull();
    expect(document.documentElement.dataset.wall).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: /Wall mode/ }));
    expect(replace).toHaveBeenCalledWith(
      "/p/acme-web/live?workflow=delivery&wall=1",
    );
  });

  it("gives the shell back when the wall goes", async () => {
    search.current = new URLSearchParams("wall=1");
    await showPage([item("1", "todo")]);
    cleanup();
    expect(document.documentElement.dataset.wall).toBeUndefined();
    expect(document.querySelector("[inert]")).toBeNull();
  });
});

describe("motion", () => {
  /** jsdom lays nothing out: a card's place is its lane's index. */
  function layOut() {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
      function (this: Element) {
        const lanes = ["todo", "doing", "done"];
        const at = lanes.indexOf(
          this.closest("[data-lane]")?.getAttribute("data-lane") ?? "",
        );
        return {
          left: at * 300,
          top: 0,
          right: at * 300 + 280,
          bottom: 100,
          width: 280,
          height: 100,
          x: at * 300,
          y: 0,
          toJSON() {},
        } as DOMRect;
      },
    );
  }
  async function moveACard() {
    const client = await showPage([item("1", "todo")]);
    vi.mocked(api.allWorkItems).mockResolvedValue({
      items: [item("1", "doing")],
      meta: { has_more: false },
    } as never);
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["items", "acme-web"] });
    });
    await waitFor(() => expect(lane("doing").contains(card("1"))).toBe(true));
  }

  it("slides a moved card with a transform and a transition, by FLIP", async () => {
    layOut();
    await moveACard();
    const moved = card("1")!;
    expect(lane("doing").contains(moved)).toBe(true);
    expect(moved.style.transition).toContain("transform");
    // The inverse transform has been released: it slides to where it now is.
    expect(moved.style.transform).toBe("");
  });

  it("changes plainly when the person asked for less motion", async () => {
    stubReducedMotion(true);
    layOut();
    await moveACard();
    const moved = card("1")!;
    expect(lane("doing").contains(moved)).toBe(true);
    expect(screen.getByTestId("live-board").dataset.reducedMotion).toBe("true");
    expect(moved.style.transition).toBe("");
    expect(moved.style.transform).toBe("");
  });

  it("stops the pulse and the blinking dot with a media query", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const css = readFileSync(
      resolve(import.meta.dirname, "../src/app/globals.css"),
      "utf8",
    );
    const reduced = css.slice(
      css.indexOf("@media (prefers-reduced-motion: reduce) {\n  .live-running"),
    );
    expect(reduced).toContain(".live-running");
    expect(reduced).toContain(".live-dot");
    expect(reduced).toContain("animation: none");
  });
});

describe("the demo", () => {
  it("plays a scripted story that moves cards, and starts over", async () => {
    // The script is loaded on demand; load it before the clock is stopped.
    await import("@/mocks/live");
    vi.useFakeTimers({
      toFake: [
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "Date",
      ],
    });
    await showBoard([], true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    const ids = () =>
      Array.from(document.querySelectorAll("[data-card-id]")).map(
        (el) => (el as HTMLElement).dataset.cardId,
      );
    expect(ids().length).toBeGreaterThanOrEqual(5);
    const whereIs = (id: string) =>
      (card(id)?.closest("[data-lane]") as HTMLElement | null)?.dataset.lane;
    const start = whereIs("demo-0");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(document.querySelector('[data-status="running"]')).not.toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(whereIs("demo-0")).not.toBe(start);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(document.querySelector('[data-status="failed"]')).not.toBeNull();
    // Then it starts over.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(13500);
    });
    expect(whereIs("demo-0")).toBe(start);
    expect(screen.getByTestId("live-indicator").dataset.state).toBe("live");
  });
});

describe("navigation", () => {
  it("links the live view from the project's navigation, after the board", () => {
    const work = projectGroups("acme-web")[0]!.entries.map((e) => e.label);
    expect(work.indexOf("Live")).toBe(work.indexOf("Board") + 1);
    const live = projectGroups("acme-web")[0]!.entries.find(
      (e) => e.label === "Live",
    )!;
    expect(live.href).toBe("/p/acme-web/live");
    expect(live.active("/p/acme-web/live")).toBe(true);
  });
});
