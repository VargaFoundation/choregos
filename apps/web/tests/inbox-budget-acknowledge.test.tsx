import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InboxView } from "@/components/inbox-view";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));
const acknowledgeBudgetAlert = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      projects: vi.fn().mockResolvedValue({ items: [{ slug: "billing-api" }], meta: { has_more: false } }),
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      orgActions: vi.fn().mockResolvedValue([]),
      orgEgress: vi.fn().mockResolvedValue({ mode: "enforce", ceiling: {}, rules: [], requests: [], can_decide: false }),
      budgetAlerts: vi.fn().mockResolvedValue([
        {
          id: "b1",
          scope: "project",
          subject: "billing-api",
          period: "day",
          period_key: "2026-10-16",
          threshold: 80,
          spent_usd: 8.5,
          budget_usd: 10,
          status: "open",
          can_acknowledge: true,
        },
        {
          id: "b2",
          scope: "agent",
          subject: "coordinator",
          period: "day",
          period_key: "2026-10-16",
          threshold: 100,
          spent_usd: 4.2,
          budget_usd: 4,
          status: "open",
          can_acknowledge: false,
        },
      ]),
      acknowledgeBudgetAlert,
    },
  };
});

function view() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InboxView heading="h2" />
    </QueryClientProvider>,
  );
}

describe("acknowledging a budget alert (decision of 2026-10-16)", () => {
  it("offers the gesture only on the alerts the person may acknowledge, and says who can on the others", async () => {
    view();
    expect(await screen.findByTestId("budget-owner-b2")).toHaveTextContent("An organisation owner or administrator acknowledges it.");
    expect(screen.getAllByRole("button", { name: "Acknowledge" })).toHaveLength(1);
  });

  it("shows the API's refusal, should the role change after the read, instead of swallowing it", async () => {
    acknowledgeBudgetAlert.mockRejectedValue(new Error("acknowledging a budget alert needs the project owner role or above"));
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText(/needs the project owner role or above/)).toBeInTheDocument();
    expect(acknowledgeBudgetAlert).toHaveBeenCalledWith("acme", "b1");
  });
});
