import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GrapheDesCouts } from "@/components/graphe-des-couts";
import { mockCostReport } from "@/mocks/data";
import { phraseDeBudget } from "@/lib/a-decider";
import { amount, daysInMonth, money, monthForecast, monthStart, top, usdToCurrency } from "@/lib/spend";

const jour = (key: string, usd: number, eur = usd * 0.9) => ({ key, cost_usd: usd, cost_eur: eur });

describe("the spend screen's arithmetic (S29-03)", () => {
  const now = new Date("2026-10-10T12:00:00Z"); // day 10 of a 31-day month

  it("forecasts linearly: what was spent per elapsed day, carried to month end", () => {
    const rows = [jour("2026-10-01", 10), jour("2026-10-05", 20), jour("2026-10-10", 20)];
    const f = monthForecast(rows, now, "USD", 100, 1);
    expect(f.spent).toBe(50);
    expect(f.forecast).toBeCloseTo((50 / 10) * 31);
    expect(f.budget).toBe(100 * 31);
    expect(f.over).toBe(false);
    expect(f.ratio).toBeCloseTo(50 / 3100);
  });

  it("says over budget when the forecast crosses it before the spend does", () => {
    const f = monthForecast([jour("2026-10-03", 200)], now, "USD", 15, 1);
    expect(f.spent).toBeLessThan(f.budget!);
    expect(f.over).toBe(true);
  });

  it("reads the budget in the display currency, with the ledger's own ratio", () => {
    const total = { cost_usd: 100, cost_eur: 92 };
    const f = monthForecast([jour("2026-10-02", 100, 92)], now, "EUR", 10, usdToCurrency(total));
    expect(f.spent).toBe(92);
    expect(f.budget).toBeCloseTo(10 * 0.92 * 31);
    expect(usdToCurrency({ cost_usd: 0, cost_eur: 0 })).toBeNull();
    expect(monthForecast([], now, "EUR", 10, null).budget).toBeNull();
  });

  it("knows the month", () => {
    expect(monthStart(now)).toBe("2026-10-01");
    expect(daysInMonth(new Date("2026-02-15T00:00:00Z"))).toBe(28);
    expect(amount(jour("x", 2, 1.5), "EUR")).toBe(1.5);
    expect(money(1.5, "EUR")).toBe("€1.50");
    expect(money(null, "USD")).toBe("—");
  });

  it("keeps the most expensive rows and their shares", () => {
    const rows = [jour("a", 1), jour("b", 6), jour("c", 3)];
    expect(top(rows, "USD", 2)).toEqual([
      { key: "b", value: 6, share: 0.6 },
      { key: "c", value: 3, share: 0.3 },
    ]);
  });
});

describe("the demo ledger adds up in every grouping", () => {
  it("each grouping has the same total, and the kind filter splits it", () => {
    const totals = ["day", "agent", "workflow", "work_item", "project", "model", "kind"].map(
      (g) => mockCostReport(`/orgs/varga/costs?group_by=${g}`).total.cost_usd,
    );
    expect(new Set(totals).size).toBe(1);
    const tool = mockCostReport("/orgs/varga/costs?group_by=model&kind=tool").total.cost_usd!;
    const model = mockCostReport("/orgs/varga/costs?group_by=model&kind=model").total.cost_usd!;
    expect(tool + model).toBeCloseTo(totals[0]!);
    expect(mockCostReport("/projects/billing-api/costs?group_by=project").rows.map((r) => r.key)).toEqual(["billing-api"]);
  });
});

describe("the chart reads in the organisation's currency", () => {
  it("euros when asked, dollars otherwise", () => {
    const rows = [{ key: "2026-10-01", cost_usd: 10, cost_eur: 9, runs: 1 }] as never;
    const { rerender } = render(<GrapheDesCouts rows={rows} currency="EUR" />);
    expect(screen.getByRole("table", { name: "Cost per day" })).toHaveTextContent("€9.00");
    rerender(<GrapheDesCouts rows={rows} />);
    expect(screen.getByRole("table", { name: "Cost per day" })).toHaveTextContent("US$10.00");
  });
});

describe("a budget alert, in a sentence (S29-04)", () => {
  it("names whose budget, how far, and how much of how much", () => {
    expect(
      phraseDeBudget({ scope: "project", subject: "billing-api", threshold: 80, spent_usd: 121.4, budget_usd: 150 }),
    ).toBe("Project billing-api has spent 80 % of its daily budget: $121.40 of $150.00 today.");
    expect(
      phraseDeBudget({ scope: "agent", subject: "coordinator", threshold: 100, spent_usd: 20.3, budget_usd: 20 }),
    ).toBe("Agent coordinator has spent its whole daily budget: $20.30 of $20.00 today.");
  });
});
