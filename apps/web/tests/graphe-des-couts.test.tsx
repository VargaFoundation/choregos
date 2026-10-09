import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GrapheDesCouts } from "@/components/graphe-des-couts";

const ligne = (key: string, cost_usd: number) => ({ key, cost_usd, cost_eur: 0, runs: 2 }) as never;

describe("le graphe des coûts (S23-10)", () => {
  it("porte son échelle, ses dates et ses valeurs", () => {
    render(<GrapheDesCouts rows={[ligne("2026-10-01", 1.5), ligne("2026-10-02", 4), ligne("2026-10-03", 2)]} />);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "cost per day, 3 days: highest US$4.00 on 2 Oct");
    const graphe = screen.getByTestId("graphe-des-couts");
    expect(graphe).toHaveTextContent("1 Oct");
    expect(graphe).toHaveTextContent("3 Oct");
    expect(screen.getByRole("table", { name: "cost per day" })).toHaveTextContent("US$1.50");
  });
});
