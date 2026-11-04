import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JourneyMap } from "@/components/journey/journey-map";
import { reduce, INITIAL } from "@/components/journey/reader";
import { api } from "@/lib/api";
import type { WorkItemJourney } from "@/lib/types";
import { runAccess, runDiff, runEvents } from "@/mocks/data";
import { journeyWaiting, journeyInProgress, journeyFinished } from "@/mocks/journey";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    api: { ...reel.api, runEvents: vi.fn(), runAccess: vi.fn(), runDiff: vi.fn(), decide: vi.fn() },
  };
});
// The SSE stream of a running run: nothing live here, the stored log is enough.
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

const NOW = Date.parse("2026-10-08T12:00:00Z");

function renderMap(journey: WorkItemJourney = journeyInProgress(NOW), decisionAnchor?: string) {
  vi.mocked(api.runEvents).mockResolvedValue(runEvents);
  vi.mocked(api.runAccess).mockResolvedValue(runAccess);
  vi.mocked(api.runDiff).mockResolvedValue(runDiff as never);
  vi.mocked(api.decide).mockResolvedValue({});
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <JourneyMap journey={journey} slug="billing-api" itemId={journey.work_item_id} decisionAnchor={decisionAnchor} />
    </QueryClientProvider>,
  );
}

const status = (id: string) => screen.getByTestId(`step-${id}`).getAttribute("data-status");

afterEach(() => vi.useRealTimers());

describe("the journey's animated map (S22-02)", () => {
  it("a ticket's journey keeps its layout budget: no outputs line under the boxes (S39-13)", () => {
    renderMap();
    expect(screen.getByTestId("step-t-implement")).toBeInTheDocument();
    expect(document.querySelector("[data-testid^='outputs-']")).toBeNull();
  });

  it("the replay bar has no ink fill any more: the action in iris, the live view in blue (S24-04)", () => {
    renderMap();
    const relire = screen.getByRole("button", { name: "Replay the journey" });
    expect(relire).toHaveClass("bg-inverse");
    expect(relire).not.toHaveClass("bg-ink");
    expect(screen.getByRole("button", { name: "Restart the replay" })).not.toHaveClass("bg-ink");
    const direct = screen.getByRole("button", { name: "Live: follow the work item" });
    expect(direct).toHaveAttribute("aria-pressed", "true");
    expect(direct).toHaveClass("bg-running-soft", "text-running-ink");
  });

  it("live: every box says who acts, where it stands, and how many rounds it took", () => {
    renderMap();
    expect(screen.getByTestId("journey-now")).toHaveTextContent("Now in Reviewed by an agent");
    expect(screen.getByTestId("journey-now")).toHaveTextContent("Security reviewer working");
    expect(status("t-security-review")).toBe("running");
    expect(status("t-review")).toBe("done");
    expect(status("t-release")).toBe("upcoming");
    expect(status("t-address-review")).toBe("done");
    const testeur = screen.getByTestId("step-t-test");
    expect(testeur).toHaveAccessibleName(/^Tester\s*runs the checks\s*Passed in round 3\s*✓ 420 tests pass$/);
    expect(testeur).toHaveAccessibleDescription("agent — done — 3 attempts");
    expect(screen.getByTestId("tour-t-review")).toHaveTextContent("Approved in round 2");
    expect(screen.getByTestId("step-t-approve-spec")).toHaveAccessibleName(/^Product owners/);
    expect(screen.getByTestId("step-t-approve-spec")).toHaveAccessibleDescription(/^person —/);
    // The arcs taken say it: once, no more.
    const svg = screen.getByTestId("journey").querySelector("svg")!;
    expect(svg.querySelector('[data-arc="implemented->planned"]')).toHaveAttribute("data-times", "1");
    expect(svg.querySelector('[data-arc="pr_approved->fixing_ci"]')).toHaveAttribute("data-times", "0");
    expect(svg.querySelector('[data-segment="reviewed"]')).toHaveAttribute("data-status", "active");
    expect(svg.querySelector('[data-step="t-security-review"] .journey-pulse')).not.toBeNull();
  });

  it("Replay plays the life back event by event, at the chosen speed, and tells it", () => {
    vi.useFakeTimers();
    renderMap();
    fireEvent.click(screen.getByRole("button", { name: "Replay the journey" }));
    expect(screen.getByTestId("journey-moment")).toHaveTextContent("1 / 34");
    expect(status("t-triage")).toBe("running");
    expect(status("t-security-review")).toBe("upcoming");
    expect(screen.getByTestId("journey-story")).toHaveTextContent("The work item arrives in To triage.");
    fireEvent.click(screen.getByRole("button", { name: "2×" }));
    act(() => {
      vi.advanceTimersByTime(550 * 4);
    });
    expect(screen.getByTestId("journey-moment")).toHaveTextContent("5 / 34");
    expect(status("t-triage")).toBe("done");
    fireEvent.click(screen.getByRole("button", { name: "Pause the replay" }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId("journey-moment")).toHaveTextContent("5 / 34");
    // Live brings back where the work item stands.
    fireEvent.click(screen.getByRole("button", { name: /^Live/ }));
    expect(status("t-security-review")).toBe("running");
  });

  it("a clicked phase jumps to the moment it starts; a phase never reached cannot be clicked", () => {
    renderMap();
    const phases = screen.getByRole("navigation", { name: "Stages of the journey" });
    fireEvent.click(within(phases).getByRole("button", { name: /In progress/ }));
    expect(status("t-implement")).toBe("running");
    expect(status("t-test")).toBe("upcoming");
    expect(screen.getByTestId("journey-story")).toHaveTextContent("Planner moved it to Planned.");
    expect(within(phases).getByRole("button", { name: /Deployed/ })).toBeDisabled();
  });

  it("clicking an agent opens its attempts, what it did and its log", async () => {
    renderMap();
    fireEvent.click(screen.getByTestId("step-t-test"));
    const panel = screen.getByTestId("journey-panel");
    expect(within(panel).getAllByRole("tab", { name: /·/ })).toHaveLength(3);
    expect(within(panel).getByText("420 tests pass.")).toBeInTheDocument();
    fireEvent.click(within(panel).getByTestId("attempt-r-test-1"));
    expect(within(panel).getByText("2 tests fail: rounding of partial credit notes.")).toBeInTheDocument();
    expect(within(panel).getByRole("list", { name: "Evidence" })).toHaveTextContent("✗ 412 run, 2 failed");
    const didLog = await within(panel).findByTestId("what-it-did");
    await waitFor(() => expect(didLog).toHaveTextContent("src/billing/rates.py"));
    expect(didLog).toHaveTextContent("Refused: outside the allowed paths");
    expect(didLog).toHaveTextContent("tests/billing/test_totals.py");
    expect(didLog).toHaveTextContent("evidence_present");
    expect(api.runAccess).toHaveBeenCalledWith("r-test-1");
    fireEvent.click(within(panel).getByRole("tab", { name: "Log" }));
    await waitFor(() => expect(within(panel).getByTestId("live-log")).toHaveTextContent("session/request_permission"));
    expect(within(panel).getByRole("link", { name: "Open the full run →" })).toHaveAttribute("href", "/p/billing-api/runs/r-test-1");
    fireEvent.click(within(panel).getByRole("button", { name: "Close the panel" }));
    expect(screen.queryByTestId("journey-panel")).not.toBeInTheDocument();
  });

  it("a person who is awaited decides from their box", async () => {
    renderMap(journeyWaiting(NOW));
    expect(status("t-approve-spec")).toBe("waiting");
    fireEvent.click(screen.getByTestId("step-t-approve-spec"));
    const panel = screen.getByTestId("journey-panel");
    expect(panel).toHaveTextContent("Decide here");
    fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.decide).toHaveBeenCalledWith("w2", { kind: "approve" }));
  });

  it("leads to the page's decision instead of offering a second one when the page shows it (S55-05)", () => {
    renderMap(journeyWaiting(NOW), "decision");
    fireEvent.click(screen.getByTestId("step-t-approve-spec"));
    const panel = screen.getByTestId("journey-panel");
    expect(within(panel).getByTestId("decision-elsewhere")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Go to the decision" })).toHaveAttribute("href", "#decision");
    expect(within(panel).queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("a finished work item: everything is done, the end is reached, the red CI and its repair show", () => {
    renderMap(journeyFinished(NOW));
    expect(screen.getByTestId("journey-now")).toHaveTextContent("Now in Verified in production");
    const svg = screen.getByTestId("journey").querySelector("svg")!;
    expect([...svg.querySelectorAll("[data-step]")].every((g) => g.getAttribute("data-status") === "done")).toBe(true);
    expect(svg.querySelector('[data-arc="pr_approved->fixing_ci"]')).toHaveAttribute("data-times", "1");
    expect(screen.getByTestId("step-t-release")).toHaveAccessibleName(/approved by leo/);
  });
});

describe("the reader (S22-02)", () => {
  it("plays to the end then stops, and starts again from the beginning", () => {
    let state = reduce(INITIAL, { type: "play", count: 3 });
    expect(state).toMatchObject({ mode: "replay", index: 0, playing: true });
    state = reduce(reduce(state, { type: "tick", count: 3 }), { type: "tick", count: 3 });
    expect(state).toMatchObject({ index: 2, playing: true });
    state = reduce(state, { type: "tick", count: 3 });
    expect(state).toMatchObject({ index: 2, playing: false });
    expect(reduce(state, { type: "play", count: 3 })).toMatchObject({ index: 0, playing: true });
    expect(reduce(state, { type: "goto", index: 99, count: 3 })).toMatchObject({ index: 2, playing: false });
    expect(reduce(INITIAL, { type: "play", count: 0 })).toBe(INITIAL);
  });
});
