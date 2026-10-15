import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EvalMatrixTable } from "@/components/eval-matrix";

const cell = (context: string, tokensIn: number | null) => ({
  backend: "claude-code",
  model: "platform/standard",
  validated: true,
  success_rate: 1,
  median_cost_usd: 0.3,
  with_memory: true,
  context: context as "none" | "rtk" | "headroom" | "rtk+headroom",
  metered: tokensIn == null ? 0 : 3,
  median_tokens_in: tokensIn,
  median_tokens_cached: tokensIn == null ? null : 80_000,
  median_tokens_out: tokensIn == null ? null : 4_800,
  median_turns: tokensIn == null ? null : 14,
});

describe("the eval matrix with its context dimension (S37-09)", () => {
  it("renders the four context cells with their ledger tokens", () => {
    render(
      <EvalMatrixTable
        entries={[cell("none", 144_000), cell("rtk", 121_600), cell("headroom", 112_800), cell("rtk+headroom", 94_400)]}
      />,
    );
    const rows = screen.getByTestId("eval-matrix").querySelectorAll("tbody tr");
    expect([...rows].map((row) => row.getAttribute("data-context"))).toEqual(["none", "rtk", "headroom", "rtk+headroom"]);
    const both = within(rows[3] as HTMLElement);
    expect(both.getByText("rtk+headroom")).toBeInTheDocument();
    expect(both.getByText("94 k")).toBeInTheDocument();
    expect(both.getByText("80 k")).toBeInTheDocument();
    expect(both.getByText("5 k")).toBeInTheDocument();
    expect(both.getByText("14")).toBeInTheDocument();
  });

  it("shows a dash, never a zero, for a cell the ledger has no measure for", () => {
    render(<EvalMatrixTable entries={[cell("headroom", null)]} />);
    const row = screen.getByTestId("eval-matrix").querySelector("tbody tr") as HTMLElement;
    expect(within(row).queryByText("0")).toBeNull();
    expect(within(row).getAllByText("—")).toHaveLength(4);
  });
});
