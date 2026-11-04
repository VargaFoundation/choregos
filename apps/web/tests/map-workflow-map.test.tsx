import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkflowMap } from "@/components/workflows/map/workflow-map";

const n = (id: string, display: string, lane = "agent", terminal = false) => ({ id, display, lane, terminal });
const graph = {
  nodes: [
    n("inbox", "To triage"),
    n("spec", "Spec to approve", "human"),
    n("dev", "Implementing"),
    n("done", "Done", "terminal", true),
    n("needs_human", "Needs a human", "human"),
  ],
  edges: [
    { id: "t-refine", from: "inbox", to: "spec", kind: "nominal", actor: "refiner", gates: [] },
    { id: "t-approve", from: "spec", to: "dev", kind: "nominal", actor: "owner", gates: [], timeout_hours: 72 },
    { id: "t-dev", from: "dev", to: "done", kind: "nominal", actor: "dev", gates: ["scope_respected", "no_secrets"] },
    { id: "dev->needs_human:escalate:x", from: "dev", to: "needs_human", kind: "escalate", label: "when retries run out" },
    { id: "t-resume", from: "needs_human", to: "dev", kind: "nominal", actor: "maintainer", gates: [] },
  ],
};
const process = [
  { id: "t-refine", from: "inbox", to: "spec", actor: "refiner", actor_type: "agent" as const, who: "the agent `refiner`", outputs: ["spec_markdown"], sentence: "" },
];
const actors = { owner: { type: "human", group: "product-owners", sla_hours: 24 } };

function renderMap(onSelect = vi.fn()) {
  render(<WorkflowMap graph={graph} process={process} initial="inbox" actors={actors} onSelect={onSelect} />);
  return onSelect;
}

describe("the flat map, rendered (S21-07)", () => {
  it("every box says in so many words who acts, what it produces, its gates, its time limit", () => {
    renderMap();
    const path = screen.getByRole("list", { name: "Nominal path" });
    expect(path.querySelectorAll(":scope > li")).toHaveLength(4);
    expect(screen.getByTestId("state-inbox")).toHaveTextContent("Agent · refiner");
    expect(screen.getByTestId("state-spec")).toHaveTextContent("Human · owner");
    expect(screen.getByTestId("state-spec")).toHaveTextContent("product-owners, within 24 h");
    expect(screen.getByTestId("state-inbox").parentElement).toHaveTextContent("Produces spec_markdown");
    expect(screen.getByTestId("state-dev").parentElement).toHaveTextContent("Only if scope_respected, no_secrets");
    expect(screen.getByTestId("state-spec").parentElement).toHaveTextContent("Times out after 72 h");
    // What leaves the path is beside it, with where one falls in from.
    const side = screen.getByRole("list", { name: "Off the main path" });
    expect(side).toHaveTextContent("Needs a human");
    expect(side).toHaveTextContent("Reached from Implementing (when retries run out)");
  });

  it("clicking a state or an arrow opens its panel", () => {
    const onSelect = renderMap();
    fireEvent.click(screen.getByTestId("state-spec"));
    expect(onSelect).toHaveBeenLastCalledWith("node", "spec");
    fireEvent.click(screen.getByTestId("transition-t-dev"));
    expect(onSelect).toHaveBeenLastCalledWith("edge", "t-dev");
    expect(screen.getByTestId("transition-t-dev")).toHaveTextContent("By dev · 2 gates");
  });

  it("the visible text names each state and arrow, and the rest describes it (S41-26)", () => {
    renderMap();
    // axe `label-content-name-mismatch`: a voice user says what the button shows.
    for (const bouton of screen.getAllByRole("button")) expect(bouton).not.toHaveAttribute("aria-label");
    expect(screen.getByTestId("state-spec")).toHaveAccessibleName(/^Human · owner.*Spec to approve\s*spec$/);
    expect(screen.getByTestId("state-done")).toHaveAccessibleDescription("Terminal state");
    expect(screen.getByTestId("state-spec")).not.toHaveAccessibleDescription(/./);
    expect(screen.getByTestId("transition-t-dev")).toHaveAccessibleName(/^By dev · 2 gates/);
    expect(screen.getByTestId("transition-t-dev")).toHaveAccessibleDescription("Transition t-dev: Implementing to Done");
  });

  it("the keyboard arrows move the focus, and the state under the cursor is announced", () => {
    renderMap();
    act(() => screen.getByTestId("state-inbox").focus());
    expect(screen.getByTestId("workflow-graph-focus")).toHaveTextContent("To triage (agent): → Spec to approve (by refiner).");
    fireEvent.keyDown(screen.getByTestId("state-inbox"), { key: "ArrowRight" });
    expect(screen.getByTestId("state-spec")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("state-spec"), { key: "End" });
    expect(screen.getByTestId("state-needs_human")).toHaveFocus();
    fireEvent.keyDown(screen.getByTestId("state-needs_human"), { key: "Home" });
    expect(screen.getByTestId("state-inbox")).toHaveFocus();
  });

  it("retries and escalations show on hover or focus of their state, all of them on request", () => {
    renderMap();
    expect(screen.queryByTestId("secondary-dev")).toBeNull();
    fireEvent.mouseEnter(screen.getByTestId("state-dev").parentElement!);
    expect(screen.getByTestId("secondary-dev")).toHaveTextContent("When retries run out → Needs a human");
    fireEvent.mouseLeave(screen.getByTestId("state-dev").parentElement!);
    expect(screen.queryByTestId("secondary-dev")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Show every retry, rejection and escalation \(1\)/));
    expect(screen.getByTestId("secondary-dev")).toBeInTheDocument();
  });

  it("an escalation's target lights up when its source is hovered", () => {
    renderMap();
    const target = screen.getByTestId("state-needs_human").parentElement!;
    expect(target.style.outline).toBe("");
    fireEvent.mouseEnter(screen.getByTestId("state-dev").parentElement!);
    expect(target.style.outline).toContain("var(--choregos-focus)");
  });
});
