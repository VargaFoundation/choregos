import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { describe, expect, it, vi } from "vitest";
import BoardPage from "@/app/p/[slug]/board/page";
import { api } from "@/lib/api";
import { trackerOf } from "@/lib/tracker";
import type { ConnectorDto } from "@/lib/types";

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
      connectors: vi.fn(),
      createWorkItem: vi.fn(),
    },
  };
});

const connector = (type: string, config: Record<string, unknown> = {}) =>
  ({ id: "c", kind: "tracker", type, config, secret_refs: {}, status: "ok" }) as unknown as ConnectorDto;

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

describe("trackerOf", () => {
  it("treats no tracker row as internal", () => {
    expect(trackerOf([])).toEqual({ external: false, type: "internal" });
    expect(trackerOf(undefined).external).toBe(false);
  });
  it("reads an external tracker and where it lives", () => {
    expect(trackerOf([connector("github-issues", { repo: "a/b" })])).toEqual({
      external: true,
      type: "github-issues",
      where: "a/b",
    });
    expect(trackerOf([connector("jira", { project_key: "CHK" })]).where).toBe("CHK");
  });
});

describe("the board's New work item", () => {
  it("shows the button on a project without a tracker row", async () => {
    vi.mocked(api.connectors).mockResolvedValue([]);
    board();
    expect(await screen.findByRole("button", { name: "New work item" })).toBeTruthy();
  });

  it("says where the issue is created, and files the request", async () => {
    vi.mocked(api.connectors).mockResolvedValue([connector("github-issues", { repo: "a/b" })]);
    vi.mocked(api.createWorkItem).mockResolvedValue({
      id: "w9",
      tracker_key: "a/b#9",
      url: "https://github.com/a/b/issues/9",
    } as never);
    board();
    fireEvent.click(await screen.findByRole("button", { name: "New work item" }));
    expect((await screen.findByTestId("tracker-notice")).textContent).toContain(
      "Creates the issue in github-issues (a/b)",
    );
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Add SSO" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Please" } });
    fireEvent.change(screen.getByLabelText("Budget in USD"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Create and start" }));
    await waitFor(() =>
      expect(api.createWorkItem).toHaveBeenCalledWith("billing-api", {
        title: "Add SSO",
        body: "Please",
        size: null,
        start: true,
        workflow: "default-simple",
        fields: {},
        budget_usd: 1.5,
      }),
    );
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Created a/b#9");
  });
});
