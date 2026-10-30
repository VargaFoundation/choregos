import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import BoardPage from "@/app/p/[slug]/board/page";
import { api } from "@/lib/api";
import type { WorkItemDto } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/p/billing-api/board",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workItems: vi.fn().mockResolvedValue({ items: [], meta: { has_more: false } }),
      workflows: vi.fn().mockResolvedValue([{ name: "default-simple", version: 1, is_default: true }]),
      workflowNamed: vi.fn().mockResolvedValue({ name: "default-simple", version: 1, yaml: "", json: { metadata: {} } }),
      validateWorkflow: vi.fn().mockResolvedValue({ valid: true, graph: { nodes: [], edges: [] } }),
      connectors: vi.fn().mockResolvedValue([]),
      createWorkItem: vi.fn(),
    },
  };
});

// React reads `status`/`value` on an already-settled promise and renders at once, without suspending.
const params = Object.assign(Promise.resolve({ slug: "billing-api" }), {
  status: "fulfilled",
  value: { slug: "billing-api" },
});

function board() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback={null}>
        <BoardPage params={params} />
      </Suspense>
    </QueryClientProvider>,
  );
}

const item = (fields: Partial<WorkItemDto>) =>
  ({
    id: "w1",
    project_slug: "billing-api",
    tracker_key: "BILLING-1",
    title: "Salary bands",
    state: "review",
    workflow_name: "default-simple",
    created_at: "2026-10-30T10:00:00Z",
    totals: {},
    ...fields,
  }) as WorkItemDto;

describe("a work item marked by a restricted knowledge base (ADR 0114)", () => {
  it("is listed with a Restricted badge and no link when the caller does not read the base", async () => {
    vi.mocked(api.workItems).mockResolvedValue({
      items: [item({ redacted: true, restricted_knowledge: ["hr"] })],
      meta: { has_more: false },
    });
    board();
    expect(await screen.findByText("Salary bands")).toBeInTheDocument();
    expect(screen.getByText("Restricted")).toHaveAttribute("title", "Read by the readers of hr");
    expect(screen.queryByRole("link", { name: "Salary bands" })).toBeNull();
  });

  it("opens as usual for a reader", async () => {
    vi.mocked(api.workItems).mockResolvedValue({
      items: [item({ redacted: false, restricted_knowledge: ["hr"] })],
      meta: { has_more: false },
    });
    board();
    expect(await screen.findByRole("link", { name: "Salary bands" })).toHaveAttribute(
      "href",
      "/p/billing-api/items/w1",
    );
    expect(screen.queryByText("Restricted")).toBeNull();
  });
});
