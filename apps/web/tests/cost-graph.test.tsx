import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CostGraph } from "@/components/cost-graph";

const row = (key: string, cost_usd: number) => ({ key, cost_usd, cost_eur: 0, runs: 2 }) as never;

describe("the cost graph (S23-10)", () => {
  it("carries its scale, its dates and its values", () => {
    render(<CostGraph rows={[row("2026-10-01", 1.5), row("2026-10-02", 4), row("2026-10-03", 2)]} />);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "Cost per day, 3 days: highest US$4.00 on 2 Oct");
    const graph = screen.getByTestId("cost-graph");
    expect(graph).toHaveTextContent("1 Oct");
    expect(graph).toHaveTextContent("3 Oct");
    expect(screen.getByRole("table", { name: "Cost per day" })).toHaveTextContent("US$1.50");
  });
});
