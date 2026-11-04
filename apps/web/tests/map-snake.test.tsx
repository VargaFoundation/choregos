import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SnakeMap } from "@/components/journey/journey-map";
import { KIND_COLOURS } from "@/components/workflows/map/layout";
import { journeyInProgress } from "@/mocks/journey";

// The dev-complex template's map, as the API returns it (`mocks/dev-complex.json`).
const { graph, process, initial } = journeyInProgress(Date.parse("2026-10-08T12:00:00Z"));

describe("a workflow's snake map (S22-05)", () => {
  it("draws every step coloured by who carries it, with no reader and no status", () => {
    render(<SnakeMap graph={graph} process={process} initial={initial} />);
    const diagram = screen.getByTestId("serpentine-map");
    expect(diagram.querySelectorAll("[data-step]")).toHaveLength(18); // sixteen steps and two detours
    expect(screen.queryByRole("toolbar", { name: "Replay the journey" })).not.toBeInTheDocument();
    // The visible text names the box, in its order (axe `label-content-name-mismatch`); the rest describes it.
    expect(screen.getByTestId("step-t-implement")).toHaveAccessibleName(/^Developer\s*writes the change\s*→ branch$/);
    expect(screen.getByTestId("step-t-implement")).toHaveAccessibleDescription("agent — 0 attempts");
    const forme = (id: string) => diagram.querySelector(`[data-step="${id}"] .journey-shape`)!.getAttribute("stroke");
    // Plum for the agent, blue for the person, slate for the platform, green for the train (S24-04): the colours
    // of the list-view map, so that a workflow's two views say the same thing.
    expect(forme("t-implement")).toBe("var(--choregos-agent-ink)");
    expect(forme("t-approve-pr")).toBe("var(--choregos-running-ink)");
    expect(forme("t-merge")).toBe("var(--choregos-neutral-ink)");
    expect(forme("t-release")).toBe("var(--choregos-succeeded-ink)");
    for (const [id, kind] of [["t-implement", "agent"], ["t-approve-pr", "human"], ["t-merge", "platform"], ["t-release", "train"]] as const) {
      expect(forme(id)).toBe(KIND_COLOURS[kind].border);
    }
    expect(diagram.querySelectorAll("[data-arc]").length).toBeGreaterThan(5);
    expect(screen.getByRole("list", { name: "Who carries each step" })).toHaveTextContent("Release train");
  });

  it("in the workflow map, each box says what it produces (S39-13)", () => {
    render(<SnakeMap graph={graph} process={process} initial={initial} />);
    expect(screen.getByTestId("outputs-t-specify")).toHaveTextContent("→ spec_markdown, allowed_paths");
    expect(screen.getByTestId("outputs-t-implement")).toHaveTextContent(/^→ branch$/);
    expect(screen.queryByTestId("outputs-t-approve-spec")).not.toBeInTheDocument();
    expect(screen.getByTestId("step-t-specify")).toHaveAccessibleName(/→ spec_markdown, allowed_paths$/);
  });

  it("without a work item, a platform box says what it waits for, not empty boxes, and every detour says why", () => {
    render(<SnakeMap graph={graph} process={process} initial={initial} />);
    const map = screen.getByTestId("serpentine-map");
    expect(map.textContent).not.toContain("☐");
    expect(map.textContent).toMatch(/waits for: [a-z ,]+/);
    const labels = [...map.querySelectorAll("[data-testid^='arc-label-']")].map((e) => e.textContent);
    expect(labels).toContain("if rejected");
  });

  it("writes how many open work items wait after a state's name", () => {
    const { container } = render(<SnakeMap graph={graph} process={process} initial={initial} />);
    const state = container.querySelector("[data-segment]")!.getAttribute("data-segment")!;
    render(<SnakeMap graph={graph} process={process} initial={initial} openItems={new Map([[state, 3]])} />);
    expect(screen.getAllByTestId("serpentine-map")[1]!.textContent).toContain("3 open · ");
  });

  it("a highlight puts data-flow on the boxes it names", () => {
    const highlight = new Map<string, "produces" | "reads">([["t-test", "produces"], ["t-implement", "reads"]]);
    render(<SnakeMap graph={graph} process={process} initial={initial} highlight={highlight} />);
    expect(screen.getByTestId("step-t-test")).toHaveAttribute("data-flow", "produces");
    expect(screen.getByTestId("step-t-implement")).toHaveAttribute("data-flow", "reads");
    expect(screen.getByTestId("step-t-review")).not.toHaveAttribute("data-flow");
  });

  it("clicking a step returns its transition, for the editing panel", () => {
    const onSelect = vi.fn();
    render(<SnakeMap graph={graph} process={process} initial={initial} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("step-t-review"));
    expect(onSelect).toHaveBeenCalledWith("t-review");
  });

  it("without `actor_type` on the edge, the state's lane says who carries it", () => {
    const n = (id: string, lane: string) => ({ id, display: id, kind: "work", lane });
    const e = (id: string, from: string, to: string) => ({ id, from, to, kind: "nominal", actor: "x", gates: [] });
    const raw = {
      nodes: [n("a", "agent"), n("b", "human"), n("c", "train"), n("d", "terminal")],
      edges: [e("t-a", "a", "b"), e("t-b", "b", "c"), e("t-c", "c", "d")],
    };
    render(<SnakeMap graph={raw} process={[]} initial="a" />);
    expect(screen.getByTestId("step-t-a")).toHaveAccessibleDescription(/^agent —/);
    expect(screen.getByTestId("step-t-b")).toHaveAccessibleDescription(/^person —/);
    expect(screen.getByTestId("step-t-c")).toHaveAccessibleDescription(/^release train —/);
  });
});
