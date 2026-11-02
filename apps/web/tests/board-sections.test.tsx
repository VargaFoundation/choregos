import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BoardPage from "@/app/p/[slug]/board/page";
import { api } from "@/lib/api";
import type { WorkItemDto } from "@/lib/types";

let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/p/fon/board",
  useSearchParams: () => search,
}));

const graphs: Record<string, { id: string; display: string; kind?: string; terminal?: boolean }[]> = {
  "fon-support": [
    { id: "inbox", display: "To take", kind: "wait" },
    { id: "diagnosed", display: "Diagnosed" },
    { id: "in_pr", display: "Documentation fix to review", kind: "wait" },
    { id: "awaiting_answer", display: "Diagnosis to review", kind: "wait" },
    { id: "merged", display: "Documentation fixed", terminal: true },
    { id: "answered", display: "Answered by Fondation", terminal: true },
    { id: "handed_back", display: "Handed back to Fondation", terminal: true },
    { id: "needs_human", display: "Needs a Fondation member", kind: "wait" },
  ],
  "fon-access": [
    { id: "inbox", display: "To take", kind: "wait" },
    { id: "awaiting_decision", display: "Access change to apply", kind: "wait" },
    { id: "applied", display: "Applied by Fondation", terminal: true },
  ],
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workItems: vi.fn(),
      workflows: vi.fn().mockResolvedValue([
        { name: "fon-access", version: 1, is_default: false, open_items: 1, description: "Access requests (form 38)." },
        { name: "fon-support", version: 1, is_default: true, open_items: 1 },
      ]),
      workflowNamed: vi.fn((_slug: string, name: string) =>
        Promise.resolve({ name, version: 1, yaml: `name: ${name}`, json: { metadata: {} } }),
      ),
      validateWorkflow: vi.fn((yaml: string) =>
        Promise.resolve({ valid: true, graph: { nodes: graphs[yaml.replace("name: ", "")] ?? [], edges: [] } }),
      ),
      connectors: vi.fn().mockResolvedValue([]),
      createWorkItem: vi.fn(),
    },
  };
});

const params = Object.assign(Promise.resolve({ slug: "fon" }), { status: "fulfilled", value: { slug: "fon" } });

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
  ({ project_slug: "fon", created_at: "2026-11-02T09:22:00Z", totals: {}, ...fields }) as WorkItemDto;

describe("the board shows every workflow of the project (2026-11-02)", () => {
  beforeEach(() => {
    search = new URLSearchParams();
    window.localStorage.clear();
    vi.mocked(api.workItems).mockResolvedValue({
      items: [
        item({ id: "w269", tracker_key: "FON-269", title: "Add a maintainer", state: "answered", workflow_name: "fon-support" }),
        item({
          id: "w270",
          tracker_key: "FON-270",
          title: "Arrival on recrutauto",
          state: "awaiting_decision",
          workflow_name: "fon-access",
        }),
      ],
      meta: { has_more: false },
    });
  });

  it("gives each workflow its own section, the default first, so no ticket looks missing", async () => {
    board();
    const support = await screen.findByRole("region", { name: "fon-support" });
    const access = await screen.findByRole("region", { name: "fon-access" });
    // The default workflow comes first in the page.
    expect(support.compareDocumentPosition(access) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(await within(access).findByText("Arrival on recrutauto")).toBeInTheDocument();
    expect(within(support).getByText("Add a maintainer")).toBeInTheDocument();
    expect(within(access).getByText("Access requests (form 38).")).toBeInTheDocument();
    expect(within(access).getByRole("link", { name: "Only this workflow" })).toHaveAttribute(
      "href",
      "/p/fon/board?workflow=fon-access",
    );
  });

  it("hides empty columns until the viewer asks, and remembers the choice", async () => {
    board();
    const support = await screen.findByRole("region", { name: "fon-support" });
    await within(support).findByText("Add a maintainer");
    // Eight states, one ticket: only its column is shown.
    expect(within(support).getAllByRole("region").map((column) => column.getAttribute("aria-label"))).toEqual([
      "Answered by Fondation, 1 ticket",
    ]);
    // 7 hidden in fon-support, 2 in fon-access.
    fireEvent.click(screen.getByRole("button", { name: "Show empty columns (9)" }));
    expect(within(support).getAllByRole("region")).toHaveLength(8);
    expect(window.localStorage.getItem("choregos.board.show-empty-columns")).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Hide empty columns" }));
    expect(within(support).getAllByRole("region")).toHaveLength(1);
  });

  it("shows one workflow only with ?workflow=", async () => {
    search = new URLSearchParams("workflow=fon-access");
    board();
    expect(await screen.findByText("Arrival on recrutauto")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "fon-support" })).toBeNull();
    expect(screen.queryByText("Add a maintainer")).toBeNull();
    expect(screen.queryByRole("link", { name: "Only this workflow" })).toBeNull();
    expect(screen.getByLabelText("Board workflow")).toHaveValue("fon-access");
  });
});
