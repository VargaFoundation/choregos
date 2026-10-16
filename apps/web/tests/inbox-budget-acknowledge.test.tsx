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
        },
      ]),
      acknowledgeBudgetAlert,
    },
  };
});

describe("acknowledging a budget alert (decision of 2026-10-16)", () => {
  it("shows the API's refusal to a developer instead of swallowing it", async () => {
    acknowledgeBudgetAlert.mockRejectedValue(new Error("acknowledging a budget alert needs the project owner role or above"));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <InboxView heading="h2" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText(/needs the project owner role or above/)).toBeInTheDocument();
    expect(acknowledgeBudgetAlert).toHaveBeenCalledWith("acme", "b1");
  });
});
