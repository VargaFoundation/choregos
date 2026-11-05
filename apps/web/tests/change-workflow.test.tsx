import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkItemPage from "@/app/p/[slug]/items/[id]/page";
import { api, ApiError } from "@/lib/api";
import { canControlItems } from "@/lib/memberships";
import type { WorkItemDto } from "@/lib/types";
import { journeyInProgress } from "@/mocks/journey";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/p/fon/items/w1",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      workItem: vi.fn(),
      timeline: vi.fn().mockResolvedValue([]),
      handOffs: vi.fn().mockResolvedValue([]),
      runs: vi.fn().mockResolvedValue([]),
      connectors: vi.fn().mockResolvedValue([]),
      artifacts: vi.fn().mockResolvedValue([]),
      journey: vi.fn(),
      workflowNamed: vi.fn().mockResolvedValue({ name: "onboarding", version: 1, yaml: "", json: { metadata: {} } }),
      me: vi.fn(),
      project: vi.fn().mockResolvedValue({ id: "p1", slug: "fon", org: "acme", name: "fon" }),
      workflows: vi.fn().mockResolvedValue([
        { name: "onboarding", version: 1, is_default: true, open_items: 1 },
        { name: "offboarding", version: 2, is_default: false, open_items: 0 },
        { name: "access", version: 1, is_default: false, open_items: 0 },
      ]),
      reroute: vi.fn(),
    },
  };
});
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

const params = Object.assign(Promise.resolve({ slug: "fon", id: "w1" }), {
  status: "fulfilled",
  value: { slug: "fon", id: "w1" },
});

const item = {
  id: "w1",
  project_slug: "fon",
  tracker_key: "FON-12",
  title: "Léa leaves on Friday",
  state: "collect",
  workflow_name: "onboarding",
  workflow_version: 1,
  created_at: "2026-11-03T10:00:00Z",
  totals: {},
} as unknown as WorkItemDto;

const developer = { id: "u1", email: "lea@acme.dev", display_name: "Léa", memberships: [{ org: "acme", role: "developer" }] };

/** Lets every pending read of the page answer, so that an absent button is absent for good. */
async function settle() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 50)));
}

function ticket(overrides: Partial<WorkItemDto> = {}, me: unknown = developer) {
  vi.mocked(api.workItem).mockResolvedValue({ ...item, ...overrides });
  vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-04T12:00:00Z")));
  vi.mocked(api.me).mockResolvedValue(me as never);
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Suspense fallback={null}>
        <WorkItemPage params={params} />
      </Suspense>
    </QueryClientProvider>,
  );
}

describe("a ticket changes workflow (S52-12, ADR 0123)", () => {
  beforeEach(() => {
    vi.mocked(api.reroute).mockReset();
    vi.mocked(api.workItem).mockClear();
  });

  it("moves the ticket to the chosen workflow with the reason, then reads it again", async () => {
    vi.mocked(api.reroute).mockResolvedValue({ status: "accepted", action: "reroute" });
    ticket();
    fireEvent.click(await screen.findByRole("button", { name: "Change workflow" }));
    const choice = screen.getByLabelText("Move this ticket to");
    // The workflow it follows is not offered: the API refuses it.
    expect([...(choice as HTMLSelectElement).options].map((option) => option.value)).toEqual(["offboarding", "access"]);
    fireEvent.change(choice, { target: { value: "access" } });
    fireEvent.change(screen.getByLabelText("Why (optional, kept in the audit log)"), {
      target: { value: "an access request, not an arrival" },
    });
    const reads = vi.mocked(api.workItem).mock.calls.length;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Change the workflow" })));
    expect(api.reroute).toHaveBeenCalledWith("w1", "access", "an access request, not an arrival");
    await waitFor(() => expect(vi.mocked(api.workItem).mock.calls.length).toBeGreaterThan(reads));
    expect(screen.queryByRole("button", { name: "Change the workflow" })).not.toBeInTheDocument();
  });

  it("sends no reason when none is typed", async () => {
    vi.mocked(api.reroute).mockResolvedValue({ status: "accepted", action: "reroute" });
    ticket();
    fireEvent.click(await screen.findByRole("button", { name: "Change workflow" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Change the workflow" })));
    expect(api.reroute).toHaveBeenCalledWith("w1", "offboarding", undefined);
  });

  it("shows the API's refusal and stays open", async () => {
    vi.mocked(api.reroute).mockRejectedValue(
      new ApiError(409, {
        detail: "a run of this work item is in progress: stop the work item or let the run finish, then change its workflow",
      }),
    );
    ticket();
    fireEvent.click(await screen.findByRole("button", { name: "Change workflow" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Change the workflow" })));
    expect(await screen.findByText(/a run of this work item is in progress/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change the workflow" })).toBeEnabled();
  });

  it("is not offered on an ended ticket", async () => {
    ticket({ closed_at: "2026-11-04T09:00:00Z" });
    await screen.findByRole("button", { name: "Pause" });
    await settle();
    expect(screen.queryByRole("button", { name: "Change workflow" })).not.toBeInTheDocument();
  });

  it("is not offered to a viewer", async () => {
    ticket({}, { ...developer, memberships: [{ org: "acme", role: "viewer" }] });
    await screen.findByRole("button", { name: "Pause" });
    await waitFor(() => expect(api.me).toHaveBeenCalled());
    await settle();
    expect(screen.queryByRole("button", { name: "Change workflow" })).not.toBeInTheDocument();
  });
});

describe("who may control a project's work items", () => {
  it("every role but viewer, on the organisation or on the project", () => {
    expect(canControlItems([{ org: "acme", role: "developer" }], "acme", "fon")).toBe(true);
    expect(canControlItems([{ org: "acme", role: "viewer" }], "acme", "fon")).toBe(false);
    expect(canControlItems([{ org: "acme", role: "viewer" }, { org: "acme", project_slug: "fon", role: "developer" }], "acme", "fon")).toBe(
      true,
    );
    expect(canControlItems([{ org: "acme", project_slug: "other", role: "project_owner" }], "acme", "fon")).toBe(false);
    expect(canControlItems([{ org: "globex", role: "org_admin" }], "acme", "fon")).toBe(false);
  });
});

describe("the reroute client (S52-12)", () => {
  it("posts the reroute action with its workflow and reason to the work item's actions", async () => {
    const real = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "accepted" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    await real.api.reroute("w1", "offboarding", "a departure");
    await real.api.reroute("w2", "access");
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/work-items/w1/actions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "reroute", workflow: "offboarding", reason: "a departure" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/work-items/w2/actions",
      expect.objectContaining({ body: JSON.stringify({ action: "reroute", workflow: "access" }) }),
    );
    vi.unstubAllGlobals();
  });
});
