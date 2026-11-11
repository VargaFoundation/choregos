import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SpendPage from "@/app/costs/page";
import WorkflowSpendPage from "@/app/p/[slug]/workflows/[name]/spend/page";
import { TokensAndCostPanel } from "@/components/spend/tokens-panel";
import { api } from "@/lib/api";
import { tokenTotals, topTokens } from "@/lib/spend";
import type { CostReport } from "@/lib/types";
import { mockCostReport } from "@/mocks/data";

/**
 * Tokens and their breakdown (S64-07, ADR 0141 §1): a workflow's, an agent's and a project's page read
 * the same cost report with their own filter; the totals say input, output and cache; a switch reads
 * the breakdown in tokens. Tested against the console's mocks, not the API.
 */
const params = { current: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => params.current,
  usePathname: () => "/costs",
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", orgs: [{ slug: "acme", currency: "USD" }], me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, costs: vi.fn(), orgCosts: vi.fn(), projects: vi.fn() } };
});

const REPORT: CostReport = {
  group_by: "model",
  currency: "EUR",
  rows: [
    { key: "claude-sonnet", cost_usd: 3, cost_eur: 2.76, tokens_in: 120_000, tokens_out: 9_000, tokens_cached: 60_000, runs: 3 },
    { key: "claude-haiku", cost_usd: 5, cost_eur: 4.6, tokens_in: 20_000, tokens_out: 1_000, tokens_cached: 0, runs: 1 },
  ],
  total: { cost_usd: 8, cost_eur: 7.36, budget_usd: null, tokens_in: 140_000, tokens_out: 10_000, tokens_cached: 60_000 },
};

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

beforeEach(() => {
  vi.clearAllMocks();
  params.current = new URLSearchParams();
  // The mock ledger answers each request as the API would: filtered, grouped, its totals summed.
  vi.mocked(api.costs).mockImplementation(async (project, group, filtre) =>
    mockCostReport(`/projects/${project}/costs?${new URLSearchParams({ group_by: group ?? "day", ...(filtre as Record<string, string>) })}`),
  );
  vi.mocked(api.orgCosts).mockImplementation(async (org, group, filtre) =>
    mockCostReport(`/orgs/${org}/costs?${new URLSearchParams({ group_by: group ?? "day", ...(filtre as Record<string, string>) })}`),
  );
  vi.mocked(api.projects).mockResolvedValue({ items: [], meta: { has_more: false } } as never);
});

describe("the arithmetic of tokens", () => {
  it("reads the totals' three counts, zero when the report says nothing", () => {
    expect(tokenTotals(REPORT.total)).toEqual({ input: 140_000, output: 10_000, cached: 60_000 });
    expect(tokenTotals({})).toEqual({ input: 0, output: 0, cached: 0 });
  });

  it("ranks rows by the tokens they moved, the cache counted once, inside the input", () => {
    const ranked = topTokens(REPORT.rows);
    expect(ranked.map((r) => r.key)).toEqual(["claude-sonnet", "claude-haiku"]);
    expect(ranked[0]).toMatchObject({ value: 129_000, input: 120_000, output: 9_000, cached: 60_000 });
    expect(ranked[0]!.share).toBeCloseTo(129_000 / 150_000);
    expect(topTokens([{ key: "x" }])).toEqual([{ key: "x", value: 0, share: 0, input: 0, output: 0, cached: 0 }]);
  });

  it("the mock ledger narrows by agent, workflow and work item, and its tokens add up", () => {
    const all = mockCostReport("/orgs/acme/costs?group_by=agent");
    const coordinator = mockCostReport("/orgs/acme/costs?group_by=model&agent=coordinator");
    expect(coordinator.total.cost_usd).toBeCloseTo(all.rows.find((r) => r.key === "coordinator")!.cost_usd);
    const onboarding = mockCostReport("/orgs/acme/costs?group_by=run&workflow=onboarding");
    expect(onboarding.rows.map((r) => r.key)).toEqual(["HR-3 · implement"]);
    expect(onboarding.total.tokens_in).toBe(0); // tool calls only: no model token
    const item = mockCostReport("/orgs/acme/costs?group_by=stage&work_item=BILL-14");
    expect(item.rows.map((r) => r.key)).toEqual(["review"]);
    expect(all.total.tokens_in).toBe(all.rows.reduce((s, r) => s + (r.tokens_in ?? 0), 0));
  });
});

describe("the tokens-and-cost panel", () => {
  it("shows input, output and cached tokens from the report's totals, and the cost", async () => {
    vi.mocked(api.costs).mockResolvedValue(REPORT);
    await show(<TokensAndCostPanel project="billing-api" groups={["model", "agent"]} />);
    const totals = await screen.findByTestId("token-totals");
    expect(within(totals).getByTestId("total-input")).toHaveTextContent("140,000");
    expect(within(totals).getByTestId("total-output")).toHaveTextContent("10,000");
    expect(within(totals).getByTestId("total-cached")).toHaveTextContent("60,000");
    expect(within(totals).getByTestId("total-cost")).toHaveTextContent("US$8.00");
  });

  it("switches its breakdown from money to tokens", async () => {
    vi.mocked(api.costs).mockResolvedValue(REPORT);
    await show(<TokensAndCostPanel project="billing-api" groups={["model"]} />);
    const table = await screen.findByTestId("panel-model");
    expect(table).toHaveAttribute("data-measure", "cost");
    expect(table).toHaveTextContent("US$5.00");
    expect(within(table).queryAllByTestId("row-tokens")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Tokens" }));
    const tokens = screen.getByTestId("panel-model");
    expect(tokens).toHaveAttribute("data-measure", "tokens");
    const rows = within(tokens).getAllByTestId("row-tokens");
    // The heaviest in tokens first, though it cost less.
    expect(rows[0]).toHaveTextContent("120,000 in · 9,000 out · 60,000 cached");
    expect(rows[1]).toHaveTextContent("20,000 in · 1,000 out · 0 cached");
    expect(screen.getByRole("button", { name: "Tokens" })).toHaveAttribute("aria-pressed", "true");
  });

  it("changes grouping, and its CSV carries the grouping and the filter", async () => {
    await show(<TokensAndCostPanel project="billing-api" filter={{ workflow: "delivery" }} groups={["model", "run"]} />);
    await screen.findByTestId("panel-model");
    fireEvent.click(screen.getByRole("button", { name: "By run" }));
    const runs = await screen.findByTestId("panel-run");
    expect(runs).toHaveTextContent("BILL-12 · implement");
    const csv = screen.getByRole("link", { name: "CSV" });
    expect(csv.getAttribute("href")).toContain("group_by=run");
    expect(csv.getAttribute("href")).toContain("workflow=delivery");
    expect(vi.mocked(api.costs).mock.calls.some(([, group, f]) => group === "run" && f?.workflow === "delivery")).toBe(true);
  });

  it("says when nothing was spent", async () => {
    vi.mocked(api.orgCosts).mockResolvedValue({ group_by: "model", rows: [], total: {} });
    await show(<TokensAndCostPanel filter={{ agent: "idle" }} groups={["model"]} />);
    expect(await screen.findByText("Nothing spent here in this period.")).toBeInTheDocument();
    expect(screen.getByTestId("total-input")).toHaveTextContent("0");
  });
});

describe("each page passes its own filter", () => {
  it("a workflow's page narrows the project's report to the workflow, by model first", async () => {
    await show(<WorkflowSpendPage params={Promise.resolve({ slug: "billing-api", name: "delivery" })} />);
    await screen.findByTestId("panel-model");
    expect(api.costs).toHaveBeenCalledWith("billing-api", "model", expect.objectContaining({ workflow: "delivery" }));
    expect(api.orgCosts).not.toHaveBeenCalled();
    const groups = within(screen.getByRole("group", { name: "Group by" })).getAllByRole("button").map((b) => b.textContent);
    expect(groups).toEqual(["By model", "By stage", "By agent", "By run"]);
  });

  it("an agent's page narrows the organisation's report to the agent", async () => {
    const { default: AgentPage } = await import("@/app/agents/[slug]/page");
    vi.spyOn(api, "agent").mockResolvedValue({
      slug: "coordinator",
      kind: "internal",
      display_name: "Coordinator",
      status: "active",
      latest_version: 1,
      versions: [],
    } as never);
    vi.spyOn(api, "agentMetrics").mockResolvedValue({ agent: "coordinator", days: 30, runs: 0, succeeded: 0, failed: 0, cost_usd: 0 } as never);
    await show(<AgentPage params={Promise.resolve({ slug: "coordinator" })} />);
    await screen.findByTestId("panel-model");
    expect(api.orgCosts).toHaveBeenCalledWith("acme", "model", expect.objectContaining({ agent: "coordinator" }));
    const groups = within(screen.getByRole("group", { name: "Group by" })).getAllByRole("button").map((b) => b.textContent);
    expect(groups).toEqual(["By model", "By stage", "By workflow", "By run"]);
    // The mock ledger's coordinator: its tokens, none of the reviewer's.
    const since = vi.mocked(api.orgCosts).mock.calls[0]![2]!.since!;
    const expected = mockCostReport(`/orgs/acme/costs?group_by=model&agent=coordinator&since=${since}`).total.tokens_in!;
    const everyone = mockCostReport(`/orgs/acme/costs?group_by=model&since=${since}`).total.tokens_in!;
    expect(expected).toBeLessThan(everyone);
    expect(screen.getByTestId("total-input")).toHaveTextContent(new Intl.NumberFormat("en-GB").format(expected));
  });

  it("the project's page reads the project's report, with no narrower filter", async () => {
    const { default: ProjectOverview } = await import("@/app/p/[slug]/page");
    const down = () => Promise.reject(new Error("503 Service Unavailable"));
    vi.spyOn(api, "project").mockResolvedValue({ slug: "billing-api", status: "active", stats: {} } as never);
    for (const read of ["dora", "allWorkItems", "crossBackend", "projectRequirements", "connectors", "workflows"] as const)
      vi.spyOn(api, read).mockImplementation(down as never);
    await show(<ProjectOverview params={Promise.resolve({ slug: "billing-api" })} />);
    await screen.findByTestId("panel-model");
    const call = vi.mocked(api.costs).mock.calls.find(([, group]) => group === "model");
    expect(call?.[0]).toBe("billing-api");
    expect(call?.[2]).not.toHaveProperty("agent");
    expect(call?.[2]).not.toHaveProperty("workflow");
    const groups = within(screen.getByRole("group", { name: "Group by" })).getAllByRole("button").map((b) => b.textContent);
    expect(groups).toEqual(["By model", "By agent", "By workflow", "By run"]);
  });

  it("the Costs page gains the run grouping and reads its breakdowns in tokens", async () => {
    await show(<SpendPage />);
    const runs = await screen.findByTestId("ventilation-run");
    expect(runs).toHaveAttribute("data-measure", "cost");
    expect(vi.mocked(api.orgCosts).mock.calls.some(([, group]) => group === "run")).toBe(true);
    expect(await screen.findByTestId("period-tokens")).toHaveTextContent(/input tokens, of which .* cached · .* output tokens/);
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Measure" }), { target: { value: "tokens" } });
    });
    expect(screen.getByTestId("ventilation-run")).toHaveAttribute("data-measure", "tokens");
    expect(within(screen.getByTestId("ventilation-agent")).getAllByTestId("row-tokens").length).toBeGreaterThan(0);
    // A tool call spends no model token: its card stays in money.
    expect(screen.getByTestId("ventilation-model-tool")).toHaveAttribute("data-measure", "cost");
  });
});
