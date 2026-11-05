import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SpendPage from "@/app/costs/page";
import { api } from "@/lib/api";
import type { CostReport } from "@/lib/types";
import { mockCostReport } from "@/mocks/data";

/**
 * Spend shows the tool calls by the door they came through (S57-10, ADR 0122 §4): the model, a
 * script, a job step, and `—` for a line booked before the door was recorded; one ledger line is one
 * call. The organisation's page and a project's are the same page, narrowed. The console writes each
 * door's name in sentence case ("None recorded" for `—`), and a row's header wraps, so that the card
 * fits a phone's window (#1398; `e2e/letter-case` and `e2e/no-overflow` measure the built page).
 */
const params = { current: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => params.current,
  usePathname: () => "/costs",
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme", orgs: [{ slug: "acme", currency: "EUR" }], me: null }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, costs: vi.fn(), orgCosts: vi.fn(), projects: vi.fn() } };
});

const row = (key: string, calls: number, eur: number) => ({ key, cost_usd: eur / 0.92, cost_eur: eur, runs: calls });
const report = (group_by: string, rows: CostReport["rows"]): CostReport => ({
  group_by,
  currency: "EUR",
  rows,
  total: { cost_usd: 0, cost_eur: 0, budget_usd: null },
});
const byDoor = report("origin", [row("model", 7, 0.5), row("script", 21, 0), row("—", 3, 0.25)]);

function answer(group = "day", filtre: { kind?: string } = {}): Promise<CostReport> {
  return Promise.resolve(group === "origin" && filtre.kind === "tool" ? byDoor : report(group, []));
}

async function renderSpend(project?: string) {
  params.current = new URLSearchParams(project ? { project } : {});
  vi.mocked(api.projects).mockResolvedValue({ items: [] } as never);
  vi.mocked(api.orgCosts).mockImplementation((_org, group, filtre) => answer(group, filtre));
  vi.mocked(api.costs).mockImplementation((_project, group, filtre) => answer(group, filtre));
  await act(async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SpendPage />
      </QueryClientProvider>,
    );
  });
  return screen.findByTestId("tool-calls-by-door");
}

afterEach(() => vi.clearAllMocks());

describe("tool calls by door, on the spend pages", () => {
  it("the organisation's page lists the three doors, a door no call used at 0, and the calls before attribution", async () => {
    const table = await renderSpend();
    expect(vi.mocked(api.orgCosts).mock.calls.some(([, group, filtre]) => group === "origin" && filtre?.kind === "tool")).toBe(true);
    const doors = within(table)
      .getAllByRole("row")
      .map((r) => r.getAttribute("data-door"))
      .filter(Boolean);
    expect(doors).toEqual(["model", "script", "job", "—"]);
    const line = (door: string) => table.querySelector(`[data-door="${door}"]`)!;
    // Each row's header: the door's name, in sentence case, then what it means.
    const names = within(table)
      .getAllByRole("rowheader")
      .map((header) => header.firstElementChild?.textContent);
    expect(names).toEqual(["Model", "Script", "Job", "None recorded"]);
    // A header cell does not wrap in the theme: the row's header must, or the card is wider than a phone.
    for (const header of within(table).getAllByRole("rowheader")) expect(header).toHaveClass("whitespace-normal");
    expect(line("model")).toHaveTextContent("7 calls");
    expect(line("model")).toHaveTextContent("€0.50");
    expect(line("script")).toHaveTextContent("21 calls");
    expect(line("script")).toHaveTextContent("through the tool bridge");
    expect(line("job")).toHaveTextContent("0 calls");
    expect(line("—")).toHaveTextContent("3 calls");
    expect(line("—")).toHaveTextContent("booked before the door was recorded");
    expect(screen.getByRole("heading", { name: "Tool calls by door" })).toBeInTheDocument();
  });

  it("a project's page reads the project's own lines, and its CSV is the same grouping", async () => {
    await renderSpend("billing-api");
    expect(vi.mocked(api.costs).mock.calls.some(([slug, group, filtre]) => slug === "billing-api" && group === "origin" && filtre?.kind === "tool")).toBe(true);
    const card = screen.getByRole("heading", { name: "Tool calls by door" }).closest("section")!;
    const csv = within(card).getByRole("link", { name: "CSV" }).getAttribute("href")!;
    expect(csv).toContain("/projects/");
    expect(csv).toContain("group_by=origin");
    expect(csv).toContain("kind=tool");
  });

  it("the demo ledger groups by door too, and adds up to the same total", () => {
    const doors = mockCostReport("/orgs/varga/costs?group_by=origin&kind=tool");
    expect(doors.rows.map((r) => r.key).sort()).toEqual(["model", "script", "—"]);
    expect(doors.total.cost_usd).toBeCloseTo(mockCostReport("/orgs/varga/costs?group_by=model&kind=tool").total.cost_usd!);
  });
});
