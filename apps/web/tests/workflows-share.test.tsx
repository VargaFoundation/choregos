import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import WorkflowsPage from "@/app/p/[slug]/workflows/page";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workflows: vi.fn(async () => [
        { name: "onboarding", version: 2, is_default: true, open_items: 0, can_share: true },
        { name: "offboarding", version: 1, is_default: false, open_items: 0, can_share: false },
      ]),
      workflowRouting: vi.fn(async () => ({ default: "onboarding", rules: [] })),
      project: vi.fn(async () => ({ id: "p", slug: "hr", org: "acme", status: "active", config: {} })),
      shareWorkflow: vi.fn(async () => ({})),
    },
  };
});

describe("sharing a workflow to the organisation's catalogue (S49-07b)", () => {
  it("is offered where the server says so, confirmed, and shares the project's workflow", async () => {
    await act(async () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <Suspense fallback="…">
            <WorkflowsPage params={Promise.resolve({ slug: "hr" })} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
    await screen.findByTestId("workflow-card-onboarding");
    const cards = screen.getByRole("list", { name: "Workflows" });
    await waitFor(() => expect(within(cards).getAllByRole("button", { name: "Share to the catalogue" })).toHaveLength(1));
    fireEvent.click(within(cards).getByRole("button", { name: "Share to the catalogue" }));
    fireEvent.click(await screen.findByRole("button", { name: "Share onboarding" }));
    await waitFor(() => expect(api.shareWorkflow).toHaveBeenCalledWith("acme", { project: "hr", name: "onboarding" }));
  });
});
