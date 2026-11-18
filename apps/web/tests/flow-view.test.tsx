import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Wall } from "@/components/live/live-kit";
import { FlowView, TRAVEL_MS } from "@/components/workflows/flow/flow-view";
import { applyEvent, applyItems, initialState, view, type LiveEntry } from "@/lib/live-flow";
import type { WorkflowGraph, WorkItemDto } from "@/lib/types";

/**
 * The flow's view (S67-29, ADR 0187 §4): a token rests in its station; when its work item changes state it
 * walks along the edge (the Web Animations API, from where it was), the edge lights up, and with reduced
 * motion it simply is somewhere else. A run in flight rings its token and glows its station; a failure and
 * a wait say so on the token.
 */
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/" }));

const graph = {
  nodes: [
    { id: "triage", display: "Triage", lane: "agent" },
    { id: "plan", display: "Plan", lane: "agent" },
    { id: "approval", display: "Plan approval", kind: "wait", lane: "human" },
    { id: "done", display: "Done", terminal: true, lane: "terminal" },
  ],
  edges: [
    { id: "t1", from: "triage", to: "plan", kind: "nominal", actor: "triager" },
    { id: "t2", from: "plan", to: "approval", kind: "nominal", actor: "planner" },
    { id: "t3", from: "approval", to: "done", kind: "nominal", actor: "a maintainer" },
  ],
} as unknown as WorkflowGraph;

const item = (id: string, state: string, over: Record<string, unknown> = {}): WorkItemDto =>
  ({ id, project_slug: "acme", tracker_key: `ACME-${id}`, title: `Title ${id}`, state, totals: {}, ...over }) as unknown as WorkItemDto;
const AT = "2026-11-18T10:00:00.000Z";
const NOW = Date.parse(AT);

const props = (flow: ReturnType<typeof view>, extra: Partial<Parameters<typeof FlowView>[0]> = {}) => ({
  graph,
  initial: "triage",
  flow,
  workflow: "default-simple",
  slug: "acme",
  now: NOW,
  wall: false,
  arrived: new Set<string>(),
  selected: null,
  onSelect: () => {},
  format: (usd: number | null | undefined) => (usd == null ? "" : `€${usd.toFixed(2)}`),
  ...extra,
});

const animate = vi.fn((frames: Keyframe[], options: KeyframeAnimationOptions) => {
  void frames;
  void options;
  return { addEventListener: vi.fn() } as unknown as Animation;
});
let reduced = false;

beforeEach(() => {
  animate.mockClear();
  reduced = false;
  HTMLElement.prototype.animate = animate as unknown as HTMLElement["animate"];
  window.matchMedia = ((query: string) => ({
    matches: reduced && query.includes("reduce"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
  class RO {
    observe() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const before = () => view(initialState(graph, [item("1", "triage"), item("2", "plan")]), AT);
const moved = () => {
  const state = applyItems(initialState(graph, [item("1", "triage"), item("2", "plan")]), [item("1", "plan"), item("2", "plan")], AT);
  return view(state, AT);
};
const token = (id: string) => document.querySelector<HTMLElement>(`[data-token-id="${id}"]`)!;

describe("the flow view", () => {
  it("rests each token in its station, and says the station's actor and count", () => {
    render(<FlowView {...props(before())} />);
    expect(token("1").dataset.station).toBe("triage");
    expect(token("2").dataset.station).toBe("plan");
    const station = screen.getByRole("button", { name: /^Plan, agent, 1 item$/ });
    expect(within(station).getByTestId("flow-count")).toHaveTextContent("1");
    expect(within(station).getByText(/agent · planner/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Plan approval, human, 0 items$/ })).toBeInTheDocument();
  });

  it("walks a token along the edge when its work item changes state, and lights the edge", () => {
    const { rerender } = render(<FlowView {...props(before())} />);
    const from = { left: token("1").style.left, top: token("1").style.top };
    expect(animate).not.toHaveBeenCalled();
    rerender(<FlowView {...props(moved())} />);
    expect(token("1").dataset.station).toBe("plan");
    expect(animate).toHaveBeenCalledTimes(1);
    const [frames, options] = animate.mock.calls[0]!;
    expect((options as KeyframeAnimationOptions).duration).toBe(TRAVEL_MS);
    // It starts where the token was (an offset back to the old place) and ends on its new one.
    const first = (frames[0] as { transform: string }).transform;
    const last = (frames[frames.length - 1] as { transform: string }).transform;
    const dx = Number(/translate\((-?[\d.]+)px/.exec(first)?.[1]);
    expect(dx).toBeCloseTo(parseFloat(from.left) - parseFloat(token("1").style.left), 0);
    expect(dx).toBeLessThan(0);
    expect(last).toMatch(/translate\(0px, 0px\)/);
    // The edge between the two lights up.
    expect(document.querySelector('[data-edge-id="t1"]')).toHaveAttribute("data-lit", "true");
    expect(document.querySelector('[data-edge-id="t2"]')).not.toHaveAttribute("data-lit");
  });

  it("does not move a token that did not change state", () => {
    const { rerender } = render(<FlowView {...props(before())} />);
    rerender(<FlowView {...props(before(), { now: NOW + 5000 })} />);
    expect(animate).not.toHaveBeenCalled();
    expect(document.querySelector("[data-lit]")).toBeNull();
  });

  it("jumps, without animating, for whoever asked for less motion (the edge still lights)", () => {
    reduced = true;
    const { rerender } = render(<FlowView {...props(before())} />);
    rerender(<FlowView {...props(moved())} />);
    expect(token("1").dataset.station).toBe("plan");
    expect(animate).not.toHaveBeenCalled();
    expect(document.querySelector('[data-edge-id="t1"]')).toHaveAttribute("data-lit", "true");
  });

  it("rings the token of a run in flight and glows its station, with elapsed and spend on hover", () => {
    const run: LiveEntry = {
      kind: "run",
      id: "r1",
      status: "running",
      project_slug: "acme",
      work_item_id: "1",
      workflow: "default-simple",
      stage_role: "triage",
      agent_slug: "triager",
      started_at: new Date(NOW - 65_000).toISOString(),
      can_stop: true,
      spend: { tokens_in: 1, tokens_out: 1, tokens_cached: 0, cost_usd: 0.42, read_at: AT },
    } as LiveEntry;
    const flow = view(applyEvent(initialState(graph, [item("1", "triage"), item("2", "plan")]), "activity.upsert", run), AT);
    render(<FlowView {...props(flow)} />);
    expect(token("1").querySelector(".flow-ring")).not.toBeNull();
    expect(token("2").querySelector(".flow-ring")).toBeNull();
    expect(screen.getByRole("button", { name: /^Triage/ })).toHaveAttribute("data-running", "true");
    expect(screen.getByRole("button", { name: /^Plan,/ })).not.toHaveAttribute("data-running");
    const tip = within(token("1")).getByRole("tooltip");
    expect(tip).toHaveTextContent(/1 min 05 s/);
    expect(tip).toHaveTextContent("€0.42");
  });

  it("flashes a failed token with the reason, and a waiting token is amber", () => {
    const failed = view(
      applyEvent(initialState(graph, [item("1", "triage"), item("2", "approval", { pending_request: { id: "q", kind: "approval", payload: { title: "Approve" }, decided_at: null } })]), "activity.ended", {
        kind: "run",
        id: "r9",
        status: "timed_out",
        project_slug: "acme",
        work_item_id: "1",
        workflow: "default-simple",
        stage_role: "triage",
        started_at: AT,
        ended_at: AT,
        can_stop: false,
        failure: { reason: "timeout", summary: "Hit the 8 minute limit." },
      } as unknown as LiveEntry),
      AT,
    );
    render(<FlowView {...props(failed)} />);
    expect(token("1").dataset.status).toBe("failed");
    expect(within(token("1")).getByTestId("flow-token").className).toContain("flow-flash");
    expect(within(token("1")).getByRole("tooltip")).toHaveTextContent("timeout");
    expect(within(token("1")).getByRole("tooltip")).toHaveTextContent("Hit the 8 minute limit.");
    expect(token("2").dataset.status).toBe("waiting");
  });

  it("links a token to its work item and reports a station click", () => {
    const onSelect = vi.fn();
    render(<FlowView {...props(before(), { onSelect })} />);
    expect(within(token("1")).getByTestId("flow-token")).toHaveAttribute("href", "/p/acme/items/1");
    fireEvent.click(screen.getByRole("button", { name: /^Plan,/ }));
    expect(onSelect).toHaveBeenCalledWith("plan");
  });
});

describe("the wall", () => {
  it("covers the console while it is up, leaves it on Escape, and gives the page back", () => {
    const shell = document.createElement("div");
    document.body.appendChild(shell);
    const onExit = vi.fn();
    const { unmount } = render(
      <Wall onExit={onExit}>
        <p>on the wall</p>
      </Wall>,
    );
    expect(screen.getByTestId("live-wall")).toHaveTextContent("on the wall");
    expect(shell.hasAttribute("inert")).toBe(true);
    expect(screen.getByTestId("live-wall").hasAttribute("inert")).toBe(false);
    expect(document.documentElement.dataset.wall).toBe("true");
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(onExit).toHaveBeenCalledTimes(1);
    unmount();
    expect(shell.hasAttribute("inert")).toBe(false);
    expect(document.documentElement.dataset.wall).toBeUndefined();
    shell.remove();
  });
});
