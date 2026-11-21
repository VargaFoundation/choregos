import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InboxView } from "@/components/inbox-view";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
const budgetAlerts = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi
        .fn()
        .mockResolvedValue({ items: [], meta: { has_more: false } }),
      workItems: vi
        .fn()
        .mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
      orgEgress: vi
        .fn()
        .mockResolvedValue({
          mode: "enforce",
          ceiling: {},
          rules: [],
          requests: [],
          can_decide: false,
        }),
      budgetAlerts,
    },
  };
});

const alert = (id: string, subject: string) => ({
  id,
  scope: "project",
  subject,
  period: "day",
  period_key: "2026-11-20",
  threshold: 80,
  spent_usd: 8,
  budget_usd: 10,
  status: "open",
  can_acknowledge: false,
});

function view(project?: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InboxView heading="h2" project={project} />
    </QueryClientProvider>,
  );
}

describe("the budget alerts are paged by the server (ADR 0206)", () => {
  it("shows the first page, and More asks the next one by its cursor", async () => {
    budgetAlerts.mockResolvedValueOnce({
      items: [alert("b1", "alpha")],
      meta: { has_more: true, next_cursor: "c1" },
    });
    budgetAlerts.mockResolvedValueOnce({
      items: [alert("b2", "beta")],
      meta: { has_more: false, next_cursor: null },
    });
    view();
    expect(
      await screen.findByText(/Project alpha has spent/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Project beta/)).toBeNull();
    expect(screen.getByText("1+ open")).toBeInTheDocument();
    expect(budgetAlerts).toHaveBeenNthCalledWith(1, "acme", {
      project: undefined,
      cursor: undefined,
      limit: 50,
    });
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(
      await screen.findByText(/Project beta has spent/),
    ).toBeInTheDocument();
    expect(budgetAlerts).toHaveBeenNthCalledWith(2, "acme", {
      project: undefined,
      cursor: "c1",
      limit: 50,
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "More" })).toBeNull(),
    );
    expect(screen.getByText("2 open")).toBeInTheDocument();
  });

  it("sends a project's own inbox to the server as a filter instead of narrowing in the browser", async () => {
    budgetAlerts.mockReset();
    budgetAlerts.mockResolvedValue({
      items: [alert("b3", "alpha")],
      meta: { has_more: false, next_cursor: null },
    });
    view("alpha");
    expect(
      await screen.findByText(/Project alpha has spent/),
    ).toBeInTheDocument();
    expect(budgetAlerts).toHaveBeenCalledWith("acme", {
      project: "alpha",
      cursor: undefined,
      limit: 50,
    });
  });
});
