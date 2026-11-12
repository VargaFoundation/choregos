import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkItemPage from "@/app/p/[slug]/items/[id]/page";
import { assigneeInWords, RequestAssignee } from "@/components/ticket/request-assignee";
import { api, ApiError } from "@/lib/api";
import { canReassignRequests } from "@/lib/memberships";
import type { HumanRequest, WorkItemDto } from "@/lib/types";
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
      workflowNamed: vi.fn().mockResolvedValue({ name: "release", version: 1, yaml: "", json: { metadata: {} } }),
      workflows: vi.fn().mockResolvedValue([{ name: "release", version: 1, is_default: true, open_items: 1 }]),
      me: vi.fn(),
      project: vi.fn().mockResolvedValue({ id: "p1", slug: "fon", org: "acme", name: "fon" }),
      members: vi.fn().mockResolvedValue([
        { user_id: "u2", email: "max@acme.dev", org: "acme", role: "developer" },
        { user_id: "u2", email: "max@acme.dev", org: "acme", project_slug: "fon", role: "project_owner" },
        { user_id: "u1", email: "lea@acme.dev", org: "acme", role: "developer" },
      ]),
      reassign: vi.fn(),
    },
  };
});
vi.mock("@/lib/sse", () => ({ useEventStream: () => ({ events: [], connected: false, error: null }) }));

const admin = { id: "u0", email: "boss@acme.dev", memberships: [{ org: "acme", role: "org_admin" }] };
const developer = { id: "u1", email: "lea@acme.dev", memberships: [{ org: "acme", role: "developer" }] };

const waiting: HumanRequest = {
  id: "r1",
  kind: "approval",
  requested_at: "2026-11-12T08:00:00Z",
  due_at: "2026-11-12T12:00:00Z",
  payload: { summary: "Ship?" },
  assignee: { user: "lea@acme.dev", group: "release-captains", reassigned: false },
};

function withClient(children: ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
}

function assignee(me: unknown, request: Partial<HumanRequest> = {}, onDone = vi.fn()) {
  vi.mocked(api.me).mockResolvedValue(me as never);
  render(withClient(<RequestAssignee slug="fon" itemId="w1" request={{ ...waiting, ...request }} onDone={onDone} />));
  return onDone;
}

async function settle() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 50)));
}

describe("who answers a waiting request (ADR 0153)", () => {
  beforeEach(() => {
    vi.mocked(api.reassign).mockReset();
  });

  it("says who it is addressed to, in words", () => {
    expect(assigneeInWords({ user: "lea@acme.dev" })).toBe("lea@acme.dev");
    expect(assigneeInWords({ group: "captains" })).toBe("the group captains");
    expect(assigneeInWords({ user: "lea@acme.dev", group: "captains" })).toBe(
      "lea@acme.dev, then the group captains once the SLA has passed",
    );
    expect(assigneeInWords(null)).toBeNull();
    expect(assigneeInWords({})).toBeNull();
  });

  it("offers reassignment to the organisation's administrator only", () => {
    expect(canReassignRequests([{ org: "acme", role: "org_admin" }], "acme")).toBe(true);
    expect(canReassignRequests([{ org: "acme", role: "org_admin", project_slug: "fon" }], "acme")).toBe(false);
    expect(canReassignRequests([{ org: "acme", role: "project_owner" }], "acme")).toBe(false);
    expect(canReassignRequests([{ org: "other", role: "org_admin" }], "acme")).toBe(false);
  });

  it("shows the assignee, and no Reassign, to a developer", async () => {
    assignee(developer);
    expect(screen.getByTestId("request-assignee")).toHaveTextContent(
      "Assigned to lea@acme.dev, then the group release-captains once the SLA has passed",
    );
    await waitFor(() => expect(api.me).toHaveBeenCalled());
    await settle();
    expect(screen.queryByRole("button", { name: "Reassign" })).not.toBeInTheDocument();
  });

  it("marks a reassigned request", () => {
    assignee(developer, { assignee: { user: "max@acme.dev", group: null, reassigned: true } });
    expect(screen.getByTestId("request-assignee")).toHaveTextContent("Assigned to max@acme.dev (reassigned)");
  });

  it("lets an administrator reassign to a member, with the reason", async () => {
    vi.mocked(api.reassign).mockResolvedValue({ ...waiting, assignee: { user: "max@acme.dev", reassigned: true } });
    const onDone = assignee(admin);
    fireEvent.click(await screen.findByRole("button", { name: "Reassign" }));
    const person = screen.getByLabelText("Person") as HTMLSelectElement;
    // Each member once, whatever their memberships.
    await waitFor(() => expect([...person.options].map((o) => o.value)).toEqual(["", "lea@acme.dev", "max@acme.dev"]));
    fireEvent.change(person, { target: { value: "max@acme.dev" } });
    const confirm = screen.getAllByRole("button", { name: "Reassign" }).at(-1)!;
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Why/), { target: { value: "Léa is on leave" } });
    await act(async () => fireEvent.click(confirm));
    expect(api.reassign).toHaveBeenCalledWith("w1", "r1", { user: "max@acme.dev", group: null, reason: "Léa is on leave" });
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("reassigns to a group, and shows the API's refusal", async () => {
    vi.mocked(api.reassign).mockRejectedValue(new ApiError(409, { detail: "this request has already been decided" }));
    assignee(admin);
    fireEvent.click(await screen.findByRole("button", { name: "Reassign" }));
    fireEvent.change(screen.getByLabelText("Group"), { target: { value: " captains " } });
    fireEvent.change(screen.getByLabelText(/^Why/), { target: { value: "the captains take it" } });
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Reassign" }).at(-1)!));
    expect(api.reassign).toHaveBeenCalledWith("w1", "r1", { user: null, group: "captains", reason: "the captains take it" });
    expect(await screen.findByText(/already been decided/)).toBeInTheDocument();
  });

  it("refuses a reassignment to nobody without calling the API", async () => {
    assignee(admin);
    fireEvent.click(await screen.findByRole("button", { name: "Reassign" }));
    fireEvent.change(screen.getByLabelText(/^Why/), { target: { value: "why not" } });
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Reassign" }).at(-1)!));
    expect(await screen.findByText(/choose a person, a group, or both/)).toBeInTheDocument();
    expect(api.reassign).not.toHaveBeenCalled();
  });

  it("is shown on the ticket's awaited decision", async () => {
    const params = Object.assign(Promise.resolve({ slug: "fon", id: "w1" }), {
      status: "fulfilled",
      value: { slug: "fon", id: "w1" },
    });
    vi.mocked(api.workItem).mockResolvedValue({
      id: "w1",
      project_slug: "fon",
      tracker_key: "FON-12",
      title: "Ship 1.2",
      state: "waiting",
      workflow_name: "release",
      workflow_version: 1,
      created_at: "2026-11-12T08:00:00Z",
      totals: {},
      pending_request: waiting,
    } as unknown as WorkItemDto);
    vi.mocked(api.journey).mockResolvedValue(journeyInProgress(Date.parse("2026-11-12T09:00:00Z")));
    vi.mocked(api.me).mockResolvedValue(admin as never);
    render(
      withClient(
        <Suspense fallback={null}>
          <WorkItemPage params={params} />
        </Suspense>,
      ),
    );
    expect(await screen.findByTestId("request-assignee")).toHaveTextContent("Assigned to lea@acme.dev");
    expect(await screen.findByRole("button", { name: "Reassign" })).toBeInTheDocument();
  });
});
