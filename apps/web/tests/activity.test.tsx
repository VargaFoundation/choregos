import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ActivityPage from "@/app/activity/page";
import ProjectActivityPage from "@/app/p/[slug]/activity/page";
import { ActivityView } from "@/components/activity/activity-view";
import { elapsedMs, entryLabel, formatElapsed, spendSoFar, statusKind } from "@/lib/activity";
import { api, ApiError } from "@/lib/api";
import type { ActivityEntry } from "@/lib/types";
import { activityApi, resetActivity } from "@/mocks/activity";

/**
 * The Activity screen (S64-06, ADR 0142 §1-2): what is in flight, then what has just finished, from the
 * console's mocks (`src/mocks/activity.ts`), not from the API; a run stopped with a reason; a refused
 * stop says the server's message.
 */
const search = { current: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => search.current,
  usePathname: () => "/activity",
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", orgs: [{ slug: "acme" }], me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      activity: vi.fn(),
      cancelRun: vi.fn(),
      runEvents: vi.fn(),
      agents: vi.fn(),
      projects: vi.fn(),
    },
  };
});

/** The mock API answers as the console's demo does: the same routes, the same refusals. */
function throughTheMock() {
  vi.mocked(api.activity).mockImplementation(async (org, filter = {}) => {
    const query = new URLSearchParams(Object.entries(filter).filter(([, v]) => v) as [string, string][]).toString();
    return activityApi("GET", `/orgs/${org}/activity${query ? `?${query}` : ""}`, undefined) as never;
  });
  vi.mocked(api.cancelRun).mockImplementation(
    async (id, reason) => activityApi("POST", `/runs/${id}/cancel`, { reason }) as never,
  );
}

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

const entry = (over: Partial<ActivityEntry> = {}): ActivityEntry => ({
  kind: "run",
  id: "r-123456789",
  status: "running",
  started_at: "2026-11-11T10:00:00Z",
  can_stop: true,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  resetActivity();
  search.current = new URLSearchParams();
  throughTheMock();
  vi.mocked(api.agents).mockResolvedValue([{ slug: "coordinator" }, { slug: "reviewer" }] as never);
  vi.mocked(api.projects).mockResolvedValue({ items: [{ slug: "billing-api" }], meta: { has_more: false } } as never);
  vi.mocked(api.runEvents).mockResolvedValue([]);
});

describe("an entry, in words", () => {
  it("names a run by its work item key and stage, a session and a turn by their id", () => {
    expect(entryLabel(entry({ work_item_key: "BILL-42", stage_role: "implement" }))).toBe("BILL-42 · implement");
    expect(entryLabel(entry())).toBe("Run r-123456");
    expect(entryLabel(entry({ kind: "session", id: "s-7f3a91c2ff" }))).toBe("Session s-7f3a91");
    expect(entryLabel(entry({ kind: "turn", id: "t-1" }))).toBe("Conversation turn t-1");
  });

  it("ticks its elapsed time, and stops it at its end", () => {
    const now = Date.parse("2026-11-11T10:03:07Z");
    expect(formatElapsed(elapsedMs(entry(), now))).toBe("3 min 07 s");
    expect(formatElapsed(elapsedMs(entry({ ended_at: "2026-11-11T10:00:42Z" }), now))).toBe("42 s");
    expect(formatElapsed(3_600_000 + 5 * 60_000)).toBe("1 h 05 min");
    expect(elapsedMs(entry({ started_at: "2026-11-11T11:00:00Z" }), now)).toBe(0);
  });

  it("says what it spent so far, and '—' when the gateway cannot say, never 0", () => {
    expect(spendSoFar(entry({ spend: null }))).toEqual({ tokens: "—", cost: "—" });
    expect(spendSoFar(entry())).toEqual({ tokens: "—", cost: "—" });
    const read = spendSoFar(
      entry({ spend: { tokens_in: 184_000, tokens_out: 12_400, tokens_cached: 0, cost_usd: 1.92, read_at: "2026-11-11T10:00:00Z" } }),
    );
    expect(read).toEqual({ tokens: "184 k in · 12 k out", cost: "US$1.92" });
  });

  it("gives each status its tone family", () => {
    expect(statusKind("running")).toBe("work");
    expect(statusKind("queued")).toBe("wait");
    expect(statusKind("cancelled")).toBe("blocked");
    expect(statusKind("succeeded")).toBe("terminal");
  });
});

describe("the Activity screen", () => {
  it("lists what is in flight first, then what has finished, from the mocks", async () => {
    await show(<ActivityPage />);
    const inFlight = await screen.findByTestId("in-flight");
    const finished = screen.getByTestId("finished");
    expect(inFlight.compareDocumentPosition(finished) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(inFlight).getByRole("heading", { name: "In flight (3)" })).toBeInTheDocument();
    expect(within(finished).getByRole("heading", { name: "Finished (2)" })).toBeInTheDocument();
    const rows = within(inFlight).getAllByTestId("activity-entry");
    expect(rows.map((r) => r.getAttribute("data-entry"))).toEqual(["r3", "r-checkout-review", "s-7f3a91c2"]);
    // A running run: its label, agent, model, tokens and cost so far, its status.
    const running = rows[0]!;
    expect(running).toHaveTextContent("BILL-42 · implement");
    expect(running).toHaveTextContent("coordinator");
    expect(running).toHaveTextContent("claude-sonnet");
    expect(within(running).getByTestId("spend-tokens")).toHaveTextContent("184 k in · 12 k out");
    expect(within(running).getByTestId("spend-cost")).toHaveTextContent("US$1.92");
    expect(within(running).getByTestId("elapsed")).toHaveTextContent(/^12 min \d\d s$/);
    expect(within(running).getByRole("link", { name: "BILL-42 · implement" })).toHaveAttribute("href", "/p/billing-api/runs/r3");
    // The reader may stop it, not the other run; a session's spend is unknown.
    expect(within(running).getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(within(rows[1]!).queryByRole("button", { name: "Stop" })).toBeNull();
    expect(within(rows[2]!).getByTestId("spend-cost")).toHaveTextContent("—");
    expect(within(rows[2]!).getByTestId("spend-tokens")).toHaveTextContent("—");
    // Nothing that ended can be stopped.
    expect(within(finished).queryByRole("button", { name: "Stop" })).toBeNull();
    expect(api.activity).toHaveBeenCalledWith("acme", {});
  });

  it("stops a run with the reason typed, and the run moves to the finished entries", async () => {
    await show(<ActivityPage />);
    const running = (await screen.findAllByTestId("activity-entry"))[0]!;
    fireEvent.click(within(running).getByRole("button", { name: "Stop" }));
    const confirmation = within(running).getByTestId("confirmation");
    const confirm = within(confirmation).getByRole("button", { name: "Stop the run" });
    // No reason, no stop.
    expect(confirm).toBeDisabled();
    expect(within(confirmation).getByRole("textbox")).toHaveAttribute("maxLength", "500");
    fireEvent.change(within(confirmation).getByRole("textbox"), { target: { value: "  it is rewriting the wrong module " } });
    await act(async () => {
      fireEvent.click(confirm);
    });
    expect(api.cancelRun).toHaveBeenCalledWith("r3", "it is rewriting the wrong module");
    await waitFor(() => expect(screen.getByRole("heading", { name: "In flight (2)" })).toBeInTheDocument());
    const finished = screen.getByTestId("finished");
    expect(within(finished).getAllByTestId("activity-entry")[0]).toHaveAttribute("data-entry", "r3");
    expect(within(finished).getAllByTestId("activity-entry")[0]).toHaveTextContent("Cancelled");
  });

  it("shows the server's message when a stop is refused, and keeps the confirmation open", async () => {
    vi.mocked(api.cancelRun).mockRejectedValue(new ApiError(409, { title: "Conflict", detail: "The run has already ended (succeeded)" }));
    await show(<ActivityPage />);
    const running = (await screen.findAllByTestId("activity-entry"))[0]!;
    fireEvent.click(within(running).getByRole("button", { name: "Stop" }));
    fireEvent.change(within(running).getByRole("textbox"), { target: { value: "wrong branch" } });
    await act(async () => {
      fireEvent.click(within(running).getByRole("button", { name: "Stop the run" }));
    });
    expect(await within(running).findByRole("alert")).toHaveTextContent("The run has already ended (succeeded)");
    expect(within(running).getByTestId("confirmation")).toBeInTheDocument();
  });

  it("the demo refuses as the API does: 403 without the right, 409 once ended, 422 without a reason", () => {
    expect(() => activityApi("POST", "/runs/r-checkout-review/cancel", { reason: "x" })).toThrow(/item:control/);
    expect(() => activityApi("POST", "/runs/r2/cancel", { reason: "x" })).toThrow(/already ended \(succeeded\)/);
    expect(() => activityApi("POST", "/runs/r3/cancel", { reason: " " })).toThrow(/1 to 500/);
  });

  it("filters by workflow, agent, assistant and kind", async () => {
    await show(<ActivityPage />);
    await screen.findByTestId("in-flight");
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), { target: { value: "session" } });
    });
    await waitFor(() => expect(api.activity).toHaveBeenLastCalledWith("acme", { kind: "session" }));
    expect(await screen.findByRole("heading", { name: "In flight (1)" })).toBeInTheDocument();
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), { target: { value: "" } });
      fireEvent.change(screen.getByRole("combobox", { name: "Agent" }), { target: { value: "reviewer" } });
      fireEvent.change(screen.getByRole("textbox", { name: "Workflow" }), { target: { value: "delivery" } });
      fireEvent.change(screen.getByRole("textbox", { name: "Assistant" }), { target: { value: "inst-1" } });
    });
    await waitFor(() =>
      expect(api.activity).toHaveBeenLastCalledWith("acme", { agent: "reviewer", workflow: "delivery", assistant: "inst-1" }),
    );
  });

  it("is narrowed to a project by `?project=` and by the project's own section", async () => {
    search.current = new URLSearchParams({ project: "billing-api" });
    await show(<ActivityPage />);
    expect(await screen.findByRole("heading", { name: "In flight (1)" })).toBeInTheDocument();
    expect(api.activity).toHaveBeenCalledWith("acme", { project: "billing-api" });
    vi.mocked(api.activity).mockClear();
    await show(<ProjectActivityPage params={Promise.resolve({ slug: "checkout-web" })} />);
    expect(await screen.findByRole("heading", { name: "In flight (1)" })).toBeInTheDocument();
    expect(api.activity).toHaveBeenCalledWith("acme", { project: "checkout-web" });
  });

  it("opens a run onto its steps, the run's events", async () => {
    vi.mocked(api.runEvents).mockResolvedValue([
      { seq: 1, type: "tool_call", ts: "2026-11-11T10:00:01Z", data: { toolCallId: "c1", title: "Read invoice.py", kind: "read", status: "completed" } },
    ] as never);
    await show(<ActivityPage />);
    const running = (await screen.findAllByTestId("activity-entry"))[0]!;
    const toggle = within(running).getByRole("button", { name: "Steps of BILL-42 · implement" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(api.runEvents).toHaveBeenCalledWith("r3");
    expect(await within(running).findByTestId("run-activity")).toBeInTheDocument();
    // A session has no run events to open.
    const session = screen.getAllByTestId("activity-entry")[2]!;
    expect(within(session).queryByRole("button", { name: /^Steps of/ })).toBeNull();
  });
});

describe("the Activity screen, live", () => {
  class FakeSource {
    static last: FakeSource | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    listeners = new Map<string, () => void>();
    closed = false;
    constructor(public url: string) {
      FakeSource.last = this;
    }
    addEventListener(name: string, listener: () => void) {
      this.listeners.set(name, listener);
    }
    removeEventListener(name: string) {
      this.listeners.delete(name);
    }
    close() {
      this.closed = true;
    }
  }
  beforeEach(() => vi.stubGlobal("EventSource", FakeSource));
  afterEach(() => vi.unstubAllGlobals());

  it("follows the organisation's activity stream, and polls while it is not open", async () => {
    await show(<ActivityView org="acme" project="billing-api" />);
    await screen.findByTestId("in-flight");
    const source = FakeSource.last!;
    expect(source.url).toBe("/api/v1/orgs/acme/activity/stream?project=billing-api");
    expect(screen.getByTestId("live-state")).toHaveTextContent("Read every 10 s");
    act(() => source.onopen?.());
    expect(screen.getByTestId("live-state")).toHaveTextContent("Live");
    expect([...source.listeners.keys()]).toEqual(["activity.upsert", "activity.ended"]);
    const before = vi.mocked(api.activity).mock.calls.length;
    await act(async () => {
      source.listeners.get("activity.ended")!();
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    expect(vi.mocked(api.activity).mock.calls.length).toBeGreaterThan(before);
    act(() => source.onerror?.());
    expect(screen.getByTestId("live-state")).toHaveTextContent("Read every 10 s");
  });
});
