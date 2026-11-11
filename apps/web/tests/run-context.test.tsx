import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunContextCard } from "@/components/context-breakdown";
import { contextView, percent } from "@/lib/context-breakdown";
import type { ContextBreakdown, RunContext } from "@/lib/types";
import { runContext } from "@/mocks/data";

const breakdown = (over: Partial<ContextBreakdown> = {}): ContextBreakdown => ({
  call: 3,
  at: "2026-11-11T10:00:00Z",
  model: "platform/standard",
  prompt_tokens: 1_000,
  max_input_tokens: 4_000,
  source: "loop",
  sections: { system: 100, "tools.platform": 50, "tools.mcp": 350, messages: 500 },
  ...over,
});
const context = (over: Partial<RunContext> = {}): RunContext => ({
  run_id: "r-1",
  measurable: true,
  backend: "prompt",
  latest: breakdown(),
  series: [{ call: 3, at: "2026-11-11T10:00:00Z", prompt_tokens: 1_000 }],
  ...over,
});

describe("what fills a run's context (ADR 0141 §6, S64-08)", () => {
  it("draws each section against the model's window, and the free space", () => {
    const view = contextView(breakdown());
    expect(view.free).toBe(3_000);
    expect(view.rows.map((row) => [row.key, row.share])).toEqual([
      ["system", 0.025],
      ["tools.platform", 0.0125],
      ["tools.mcp", 0.0875],
      ["messages", 0.125],
    ]);
    expect(percent(view.freeShare!)).toBe("75.0 %");

    render(<RunContextCard context={context()} />);
    const bar = screen.getByTestId("context-bar");
    expect([...bar.querySelectorAll("[data-section]")].map((s) => s.getAttribute("data-section"))).toEqual([
      "system",
      "tools.platform",
      "tools.mcp",
      "messages",
    ]);
    expect(bar.querySelector('[data-section="tools.mcp"]')).toHaveStyle({ width: "8.75%" });
    expect(within(screen.getByTestId("context-row-tools.mcp")).getByText("MCP tools")).toBeInTheDocument();
    expect(within(screen.getByTestId("context-row-tools.mcp")).getByText("8.8 %")).toBeInTheDocument();
    expect(within(screen.getByTestId("context-row-free")).getByText("3,000")).toBeInTheDocument();
    expect(screen.getByTestId("context-free")).toHaveTextContent("3 k free");
    expect(screen.getByTestId("context-note")).toHaveTextContent("Split estimated, total exact");
  });

  it("says the free space is unknown when the catalogue does not know the model's window", () => {
    const view = contextView(breakdown({ max_input_tokens: null }));
    expect(view.free).toBeNull();
    expect(view.rows.reduce((sum, row) => sum + row.share, 0)).toBeCloseTo(1);

    render(<RunContextCard context={context({ latest: breakdown({ max_input_tokens: null }) })} />);
    expect(screen.getByTestId("context-free")).toHaveTextContent("Free space unknown");
    expect(within(screen.getByTestId("context-row-free")).getByText("unknown")).toBeInTheDocument();
    expect(screen.getByText("Of the total")).toBeInTheDocument();
  });

  it("says a backend the platform cannot see into is not measurable", () => {
    render(<RunContextCard context={context({ measurable: false, backend: "code", latest: null, series: [] })} />);
    expect(screen.getByTestId("context-not-measurable")).toHaveTextContent("Not measurable for this backend (code)");
    expect(screen.queryByTestId("context-bar")).toBeNull();
  });

  it("waits for a first call, and shows nothing before the API answers", () => {
    render(<RunContextCard context={context({ latest: null, series: [] })} />);
    expect(screen.getByText("No model call measured yet.")).toBeInTheDocument();
    const { container } = render(<RunContextCard context={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("the demo fixture's sections sum to its total", () => {
    const latest = runContext.latest!;
    const sum = Object.values(latest.sections).reduce((a, b) => a + (b ?? 0), 0);
    expect(sum).toBe(latest.prompt_tokens);
  });
});
